import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
import interaction
import release_wait as w
import duo_killer as d
import variant_fallback as v
import checkout_options as o
from protocol import Channel, Cancelled
from service import Service
from test_checkout_options import FakeFlow, fulfillment_model, CONFIG

SKU = 'MK2Q4CH/A'


def page(soon=False, code=200):
    r = requests.Response()
    r.status_code = code
    r.url = 'https://www.apple.com.cn/shop/product/' + SKU
    r._content = ('productSelectionData: ' + json.dumps({'products': [
        {'partNumber': SKU, 'comingSoon': soon, 'getReady': False}]})).encode()
    r.close = Mock()
    return r


class ReleaseWaitTests(unittest.TestCase):
    def setUp(self):
        self.handler = SimpleNamespace(checkout_preferences={'wait_for_release': True}, stage=Mock(), wait_retry=Mock())
        self.binding = patch.object(interaction, 'handler', self.handler)
        self.binding.start()
        self.addCleanup(self.binding.stop)

    def test_release_after_more_than_thirty_polls_reuses_ready_response(self):
        ready = page()
        request = Mock(side_effect=[page(True) for _ in range(35)] + [ready])
        self.assertIs(w.product_page(Mock(), request, ready.url, SKU), ready)
        self.assertEqual(request.call_count, 36)
        self.assertEqual(self.handler.wait_retry.call_count, 35)
        self.handler.wait_retry.assert_called_with(2)

    def test_transient_read_errors_wait_then_resume(self):
        ready = page()
        request = Mock(side_effect=[requests.Timeout(), page(code=404), page(code=503), ready])
        self.assertIs(w.product_page(Mock(), request, ready.url, SKU), ready)
        self.assertEqual(self.handler.wait_retry.call_count, 3)

    def test_denied_access_is_not_treated_as_unreleased(self):
        for code in (401, 403, 429, 541):
            with self.subTest(code=code), self.assertRaises(requests.HTTPError):
                w.product_page(Mock(), Mock(return_value=page(code=code)), 'url', SKU)
        self.handler.wait_retry.assert_not_called()

    def test_unknown_or_malformed_matrix_never_claims_ready(self):
        self.assertIn('尚未列出', w.unavailable_reason('productSelectionData: {"products":[]}', SKU))
        with self.assertRaises(ValueError): w.unavailable_reason('productSelectionData: broken', SKU)
        self.assertIsNone(w.unavailable_reason('<meta name="globalnav-store-key">', SKU))

    def test_disabled_mode_keeps_single_attempt(self):
        self.handler.checkout_preferences['wait_for_release'] = False
        request = Mock(return_value=page(True))
        w.product_page(Mock(), request, 'url', SKU)
        request.assert_called_once()
        self.handler.wait_retry.assert_not_called()
        self.assertEqual(list(w.attempts(3)), [0, 1, 2])

    def test_cancel_interrupts_wait(self):
        channel = Channel('a', 't', io.StringIO())
        channel.cancelled.set()
        with self.assertRaises(Cancelled):
            Service.wait_retry(SimpleNamespace(channel=channel), 2)

    def test_cancel_stops_product_wait_before_next_request(self):
        self.handler.wait_retry.side_effect = Cancelled()
        request = Mock(return_value=page(True))
        with self.assertRaises(Cancelled): w.product_page(Mock(), request, 'url', SKU)
        request.assert_called_once()

    def test_store_becomes_available_after_old_limit(self):
        self.handler.checkout_preferences.update(mode='RETAIL', city='成都', store='R580')
        flow = FakeFlow(fulfillment_model(mixed=False), mixed=False)
        original = flow.action
        searches = []
        def action(path, name, data=None):
            original(path, name, data)
            if name == 'search':
                searches.append(1)
                o.value(flow.model, path+'.searchResults.d.retailStores')[0]['storeDisabled'] = len(searches) <= 35
        flow.action = action
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)/'checkout.json'; o.save_config(config, CONFIG)
            o.fulfillment(flow, config)
        self.assertEqual(len(searches), 36)
        self.assertEqual(flow.verified_pickup_store, 'R580')
        self.assertEqual(self.handler.wait_retry.call_count, 35)

    def test_alternatives_reconsider_original_after_stock_returns(self):
        self.handler.checkout_preferences['fallback_configurations'] = True
        flow = Mock()
        candidate = {'sku': SKU, 'price': '9999', 'name': '测试配置'}
        with patch.object(d, 'pay_bag', side_effect=[v.PickupUnavailable('R574'), {'success':True}]) as pay, \
             patch.object(v, 'fetch_variants', return_value=[candidate]), \
             patch.object(v, 'choose_available', side_effect=[None]*35+[candidate]) as stock, \
             patch.object(v, 'replace_cart') as replace:
            self.assertTrue(d.pay_with_alternatives(flow, SKU, 1)['success'])
        self.assertEqual(stock.call_count, 36)
        self.assertEqual(pay.call_count, 2)
        replace.assert_not_called()

    def test_submit_error_never_restarts_checkout_in_wait_mode(self):
        self.handler.checkout_preferences['fallback_configurations'] = True
        with patch.object(d, 'pay_bag', side_effect=ValueError('提交结果未知')) as pay:
            with self.assertRaisesRegex(ValueError, '提交结果未知'):
                d.pay_with_alternatives(Mock(), SKU, 1)
        pay.assert_called_once()

    def cart_flow(self):
        flow = Mock()
        flow.session = requests.Session()
        flow.model = {'shoppingCart': {'items': {}, 'summary': {'d': {'total': 'RMB 9999'}}}}
        flow.require.side_effect = lambda path: o.value(flow.model, path)
        return flow

    def test_explicit_add_rejection_with_empty_cart_can_wait_and_retry(self):
        flow = self.cart_flow()
        reads = []
        def read(*args):
            reads.append(1)
            if len(reads) == 2:
                flow.model['shoppingCart']['items'] = {'item-one': {'d': {'sku':SKU, 'totalPrice':'RMB 9999'},
                    'itemQuantity': {'d': {'quantity':1, 'maxQuantity':2}}}}
        flow.page.side_effect = read
        with patch.object(d, '_add_one_to_bag', side_effect=[w.AddRejected('暂未开放'), '']) as add:
            self.assertTrue(d.add_to_bag(SKU, set_total=True, flow=flow)['success'])
        self.assertEqual(add.call_count, 2)
        self.handler.wait_retry.assert_called_once_with(2)

    def test_ambiguous_add_result_never_repeats_mutation(self):
        flow = self.cart_flow()
        with patch.object(d, '_add_one_to_bag', return_value='网络异常') as add:
            with self.assertRaisesRegex(ValueError, '不要直接重复加购'):
                d.add_to_bag(SKU, set_total=True, flow=flow)
        add.assert_called_once()
        self.handler.wait_retry.assert_not_called()

    def test_live_empty_bag_metadata_waits_then_recovers(self):
        flow = self.cart_flow()
        flow.model['shoppingCart']['items'] = {'d': {'id':'cart-items','bagCount':0}, 'b':{}, 'p':{}, 'view':{}}
        reads = []
        def read(*args):
            reads.append(1)
            if len(reads) == 3:
                flow.model['shoppingCart']['items']['item-one'] = {'d':{'sku':SKU,'totalPrice':'RMB 9999'}, 'itemQuantity':{'d':{'quantity':1}}}
        flow.page.side_effect = read
        with patch.object(d, '_add_one_to_bag', side_effect=[w.AddRejected('ADD_TO_CART_ERROR'),w.AddRejected('ADD_TO_CART_ERROR'),'']) as add:
            self.assertTrue(d.add_to_bag(SKU, set_total=True, flow=flow)['success'])
        self.assertEqual(add.call_count,3)
        self.assertEqual(self.handler.wait_retry.call_count,2)
        self.assertTrue(all(c.args == (2,) for c in self.handler.wait_retry.call_args_list))

    def test_unknown_or_contradictory_empty_bag_never_repeats(self):
        for items in ({'d':{}}, {'d':{'bagCount':1}}, {'d':{'bagCount':False}}, {'d':{'bagCount':0},'item-other':{'d':{'sku':'OTHER/A'},'itemQuantity':{'d':{'quantity':1}}}}):
            flow=self.cart_flow();flow.model['shoppingCart']['items']=items
            with patch.object(d, '_add_one_to_bag', return_value=w.AddRejected('error')) as add:
                with self.assertRaises(ValueError): d.add_to_bag(SKU,set_total=True,flow=flow)
            add.assert_called_once()

    def test_live_empty_bag_wait_is_cancellable(self):
        from protocol import Cancelled
        flow=self.cart_flow();flow.model['shoppingCart']['items']={'d':{'bagCount':0},'view':{}}
        self.handler.wait_retry.side_effect=Cancelled()
        with patch.object(d, '_add_one_to_bag', return_value=w.AddRejected('error')) as add:
            with self.assertRaises(Cancelled):d.add_to_bag(SKU,set_total=True,flow=flow)
        add.assert_called_once()
