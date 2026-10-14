"""门店目录、按序切店及 SKU 组合策略；所有结账响应均为离线模型。"""
import contextlib
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import checkout_options as o
import duo_killer as d
import interaction
from order_template import TemplatePolicy
from retail_stores import CATALOG, STORES, validate_store_ids
import variant_fallback as v
from test_checkout_options import FakeFlow, CONFIG, fulfillment_model, pickup_time_model
from test_order_templates import template

LOCATOR = 'checkout.fulfillment.pickupTab.pickup.storeLocator'
SLOTS = 'checkout.fulfillment.pickupTab.pickup.timeSlot'
IDS = ['R580', 'R502']


class PickupStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'checkout.json'
        o.save_config(self.path, CONFIG)
        self.handler = SimpleNamespace(checkout_preferences={
            'mode': 'RETAIL', 'city': '成都', 'store': IDS[0], 'store_ids': IDS[:],
            'auto_submit': True, 'wait_for_release': False, 'fallback_configurations': False}, stage=Mock())
        patcher = patch.object(interaction, 'handler', self.handler)
        patcher.start(); self.addCleanup(patcher.stop)
        network = patch('requests.sessions.Session.request', side_effect=AssertionError('No live network'))
        network.start(); self.addCleanup(network.stop)
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__(); self.addCleanup(output.__exit__, None, None, None)

    def flow(self, disabled=(True, False), *, slots=False):
        model = pickup_time_model() if slots else fulfillment_model(mixed=False)
        o.value(model, LOCATOR + '.searchResults.d')['retailStores'] = [
            dict(storeId=s, storeName=STORES[s]['name'], storeDisabled=off)
            for s, off in zip(IDS, disabled)] + [dict(storeId='R999', storeName='未选择', storeDisabled=False)]
        flow = FakeFlow(model, mixed=False)
        action = flow.action
        current_slots = deepcopy(o.value(model, SLOTS))
        def select(module, name, data=None):
            action(module, name, data)
            if name == 'select' and slots:
                updated = deepcopy(current_slots)
                updated['d']['storeId'] = data[LOCATOR + '.selectStore']
                o.value(flow.model, 'checkout.fulfillment.pickupTab.pickup')['timeSlot'] = updated
        flow.action = select
        return flow

    def test_catalog_is_mainland_complete_and_unique(self):
        self.assertEqual(CATALOG['scope'], '中国大陆')
        self.assertEqual(CATALOG['source_url'], 'https://www.apple.com.cn/retail/storelist/')
        self.assertEqual(len(CATALOG['stores']), len(STORES))
        self.assertEqual(len(STORES), 49)
        self.assertEqual(len({s['city'] for s in STORES.values()}), 25)
        for store in STORES.values():
            self.assertTrue(all(store.values()))
            self.assertTrue(store['url'].startswith('https://www.apple.com.cn/retail/'))
        self.assertEqual(STORES['R502']['city'], '成都')

    def test_only_current_city_unique_ids_are_accepted(self):
        self.assertEqual(validate_store_ids('成都市', IDS), IDS)
        for ids in ([], IDS + ['R401'], ['R580', 'R580'], ['unknown'], None, 'R580', [None], [True]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                validate_store_ids('成都', ids)

    def test_new_and_legacy_template_preferences(self):
        value = template('RETAIL')
        self.assertEqual(TemplatePolicy(value).preferences()['store_ids'], [])
        value['pickup'].update(store='', store_ids=IDS)
        self.assertEqual(TemplatePolicy(value).preferences()['store_ids'], IDS)
        self.assertEqual(TemplatePolicy(value).preferences()['store'], IDS[0])
        value['pickup']['store_ids'] = ['R401']
        with self.assertRaisesRegex(ValueError, '所选城市'):
            TemplatePolicy(value)

    def test_unavailable_first_store_automatically_selects_second(self):
        flow = self.flow()
        o.fulfillment(flow, self.path)
        self.assertEqual(flow.verified_pickup_store, 'R502')
        selects = [data for _, name, data in flow.calls if name == 'select']
        self.assertEqual([s[LOCATOR + '.selectStore'] for s in selects], ['R502'])
        searches = [data for _, name, data in flow.calls if name == 'search']
        self.assertEqual(searches[0][LOCATOR + '.showAllStores'], 'true')

    def test_first_available_wins_in_configured_order(self):
        for ids in (IDS, list(reversed(IDS))):
            self.handler.checkout_preferences['store_ids'] = ids
            flow = self.flow((False, False))
            o.fulfillment(flow, self.path)
            self.assertEqual(flow.verified_pickup_store, ids[0])

    def test_all_unavailable_never_selects_unconfigured_store_or_home(self):
        flow = self.flow((True, True))
        with patch('time.sleep', side_effect=AssertionError('No polling')), self.assertRaisesRegex(ValueError, '所选门店均'):
            o.fulfillment(flow, self.path)
        self.assertFalse(any(n == 'select' or n.startswith('continue') for _, n, _ in flow.calls))

    def test_valid_search_without_first_store_tries_next_selected_store(self):
        flow = self.flow()
        o.value(flow.model, LOCATOR + '.searchResults.d.retailStores').pop(0)
        o.fulfillment(flow, self.path)
        self.assertEqual(flow.verified_pickup_store, IDS[1])

    def test_unknown_search_status_or_network_failure_stops(self):
        for kind in ('missing', 'unknown', 'duplicate', 'search_error', 'network'):
            flow = self.flow()
            node = o.value(flow.model, LOCATOR)
            if kind == 'missing': node['searchResults']['d'].pop('retailStores')
            if kind == 'unknown': node['searchResults']['d']['retailStores'][0].pop('storeDisabled')
            if kind == 'duplicate': node['searchResults']['d']['retailStores'].append(deepcopy(node['searchResults']['d']['retailStores'][0]))
            if kind == 'search_error': node['d']['isStoreSearchErrorPresent'] = True
            if kind == 'network': flow.action = Mock(side_effect=ValueError('synthetic network failure'))
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                o.fulfillment(flow, self.path)
            self.assertFalse(any(n == 'select' for _, n, _ in flow.calls))

    def test_wait_refreshes_all_stores_then_restarts_from_first(self):
        self.handler.checkout_preferences['wait_for_release'] = True
        flow = self.flow((True, True))
        def available(_):
            o.value(flow.model, LOCATOR + '.searchResults.d.retailStores')[0]['storeDisabled'] = False
        with patch.object(o.release_wait, 'pause', side_effect=available) as pause:
            o.fulfillment(flow, self.path)
        pause.assert_called_once()
        self.assertEqual(flow.verified_pickup_store, IDS[0])
        self.assertEqual(sum(n == 'search' for _, n, _ in flow.calls), 2)

    def test_complete_current_city_results_skip_duplicate_search(self):
        flow = self.flow()
        o.value(flow.model, LOCATOR + '.d')['searchInput'] = '四川 成都 锦江区'
        o.fulfillment(flow, self.path)
        self.assertEqual(flow.verified_pickup_store, IDS[1])
        self.assertFalse(any(n == 'search' for _, n, _ in flow.calls))

    def test_store_becoming_unavailable_during_selection_tries_next(self):
        flow = self.flow((False, False))
        action = flow.action
        def select(module, name, data=None):
            action(module, name, data)
            if name == 'select' and data[LOCATOR + '.selectStore'] == IDS[0]:
                o.value(flow.model, LOCATOR + '.searchResults.d.retailStores')[0]['storeDisabled'] = True
        flow.action = select
        o.fulfillment(flow, self.path)
        self.assertEqual(flow.verified_pickup_store, IDS[1])

    def test_all_stores_checked_before_sku_fallback(self):
        self.handler.checkout_preferences['fallback_configurations'] = True
        flow = self.flow((True, True))
        with self.assertRaises(v.PickupUnavailable) as error:
            o.fulfillment(flow, self.path)
        self.assertEqual(error.exception.store_ids, tuple(IDS))
        self.assertFalse(any(n.startswith('continue') for _, n, _ in flow.calls))

    def test_second_store_uses_its_own_live_signed_slot(self):
        flow = self.flow(slots=True)
        o.fulfillment(flow, self.path)
        payload = next(data for _, name, data in flow.calls if name == 'continueFromFulfillmentToPickupContact')
        self.assertEqual(payload[LOCATOR + '.selectStore'], IDS[1])
        self.assertEqual(payload[SLOTS + '.dateTimeSlots.signKey'], 'test-sign-14')

    def test_no_matching_slot_tries_next_but_wrong_store_signature_stops(self):
        for kind in ('empty', 'wrong_store', 'malformed'):
            flow = self.flow((False, False), slots=True)
            action = flow.action
            def select(module, name, data=None):
                action(module, name, data)
                if name == 'select' and data[LOCATOR + '.selectStore'] == IDS[0]:
                    node = o.value(flow.model, SLOTS)
                    if kind == 'empty': node['dateTimeSlots']['d']['timeSlotWindows'] = []
                    if kind == 'wrong_store': node['d']['storeId'] = 'R999'
                    if kind == 'malformed': node['dateTimeSlots']['d']['timeSlotWindows'][0]['14'][0].pop('signKey')
            flow.action = select
            # Force a fresh selection even though the synthetic initial slot is valid.
            o.value(flow.model, LOCATOR + '.d')['selectStore'] = ''
            with self.subTest(kind=kind):
                if kind == 'empty':
                    o.fulfillment(flow, self.path)
                    self.assertEqual(flow.verified_pickup_store, IDS[1])
                else:
                    with self.assertRaises(ValueError): o.fulfillment(flow, self.path)
                    self.assertFalse(any(n == 'continueFromFulfillmentToPickupContact' for _, n, _ in flow.calls))

    def test_alternative_sku_priority_precedes_store_priority(self):
        self.handler.checkout_preferences['fallback_configurations'] = True
        rows = [dict(sku=s, name=s, price='100') for s in ('BBBBBCH/A', 'CCCCCCH/A')]
        queries = []
        def choose(session, request, candidates, store, attempted):
            queries.append((store, [r['sku'] for r in candidates]))
            return rows[1] if store == IDS[0] else rows[0]
        flow = SimpleNamespace(session=Mock())
        with patch.object(d, 'pay_bag', side_effect=[v.PickupUnavailable(IDS[0], IDS), {'success': True}]) as pay, \
                patch.object(v, 'fetch_variants', return_value=rows), patch.object(v, 'choose_available', side_effect=choose), \
                patch.object(v, 'replace_cart') as replace:
            result = d.pay_with_alternatives(flow, 'AAAAACH/A', 1)
        self.assertEqual(queries, [(IDS[0], ['BBBBBCH/A', 'CCCCCCH/A']), (IDS[1], ['BBBBBCH/A'])])
        replace.assert_called_once_with(flow, 'AAAAACH/A', 'BBBBBCH/A', 1)
        self.assertEqual(result['purchased_sku'], 'BBBBBCH/A')
        self.assertEqual(pay.call_count, 2)
        self.assertEqual(self.handler.checkout_preferences['store_ids'], IDS)

    def test_uncertain_submission_never_attempts_another_store_or_sku(self):
        self.handler.checkout_preferences['fallback_configurations'] = True
        with patch.object(d, 'pay_bag', side_effect=ValueError('提交结果未知')) as pay, patch.object(v, 'choose_available') as choose:
            with self.assertRaisesRegex(ValueError, '提交结果未知'):
                d.pay_with_alternatives(Mock(), 'AAAAACH/A', 1)
        pay.assert_called_once(); choose.assert_not_called()

    def test_catalog_resolves_province_without_network_request(self):
        current = {'stateOptions': [{'value': '四川'}, {'value': '上海'}]}
        with patch.object(d, 'request_get', side_effect=AssertionError('No directory network')):
            self.assertEqual(o.pickup_state(Mock(), current, '成都'), '四川')
