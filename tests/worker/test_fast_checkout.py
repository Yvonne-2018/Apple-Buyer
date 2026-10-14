"""Automatic checkout must stay bounded to the explicitly requested item."""
import contextlib
import io
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import duo_killer as d
import interaction
import test_duo_killer as fixtures
from test_checkout_options import FakeFlow, fulfillment_model, pickup_time_model, CONFIG
from copy import deepcopy
import checkout_options as options


class FastCheckoutTests(unittest.TestCase):
    def test_auto_review_accepts_same_or_lower_total_but_never_higher(self):
        model = fixtures.PurchaseFlowTests.review_model()
        for limit, accepted in ((3107, True), (3200, True), (3106, False)):
            handler = SimpleNamespace(stage=Mock(), confirm=Mock(side_effect=AssertionError('No prompt')))
            with patch.object(interaction, 'handler', handler), contextlib.redirect_stdout(io.StringIO()):
                if accepted:
                    d.confirm_order(model, auto_limit=Decimal(limit))
                else:
                    with self.assertRaisesRegex(ValueError, '超过'):
                        d.confirm_order(model, auto_limit=Decimal(limit))
                handler.confirm.assert_not_called()

    def test_auto_rejects_unknown_or_non_yuan_price(self):
        for price in ('USD 1', '$1', '', '免费', True):
            with self.subTest(price=price), self.assertRaises(ValueError):
                d.yuan_amount(price)

    def test_cart_with_extra_item_stops_before_checkout(self):
        flow = d.Checkout(Mock())
        flow.model = fixtures.PurchaseFlowTests.review_model()
        flow.model['shoppingCart']['actions'] = {'a': {'checkout': {'url': '/test'}}}
        flow.headers['x-aos-stk'] = 'fixture'
        flow.purchase_target = ('MKWA4FE/A', 2)
        flow.action = Mock()
        handler = SimpleNamespace(checkout_preferences={'auto_submit': True})
        with patch.object(interaction, 'handler', handler), self.assertRaisesRegex(ValueError, '只有本次指定'):
            d.pay_bag(flow=flow)
        flow.action.assert_not_called()

    def test_selected_alipay_skips_request_and_submit_remains_once(self):
        for selected, selects in (('ALIPAY', 0), ('OTHER', 1)):
            model = fixtures.PurchaseFlowTests.review_model()
            model['checkout']['billing']['billingOptions']['d']['selectBillingOption'] = selected
            flow = d.Checkout(Mock())
            flow.model = model
            flow.model['shoppingCart']['actions'] = {'a': {'checkout': {'url': '/test'}}}
            flow.headers['x-aos-stk'] = 'fixture'
            flow.action = Mock()
            with tempfile.TemporaryDirectory() as temp, patch.object(d, 'ROOT', Path(temp)), \
                 patch.object(d, 'PAYMENT_ROOT', None), patch.object(d, 'save_auth'), \
                 patch.object(d, 'checkout_delivery'), patch.object(d, 'checkout_shipping'), \
                 patch.object(d, 'reauthenticate_checkout'), patch.object(d, 'confirm_order'), \
                 patch.object(d, 'finish_payment', return_value={'success': True}), \
                 patch.object(d, 'payment_qr'), patch.object(interaction, 'handler', None):
                d.pay_bag(flow=flow)
            names = [c.args[1] for c in flow.action.call_args_list]
            self.assertEqual(names.count('selectBillingOptionAction'), selects)
            self.assertEqual(names.count('continueFromReviewToProcess'), 1)

    def test_auto_flow_combines_billing_without_prompt_and_submits_once(self):
        model = fixtures.PurchaseFlowTests.review_model()
        for root in (model['shoppingCart']['items'], model['checkout']['review']['fulfillmentReview']['items']):
            root.pop('item-phone', None)
        model['checkout']['review']['summary']['d']['total'] = 'RMB 1,598'
        flow = d.Checkout(Mock())
        flow.model = model
        flow.model['shoppingCart']['actions'] = {'a': {'checkout': {'url': '/test'}}}
        flow.headers['x-aos-stk'] = 'fixture'
        flow.purchase_target = ('MKWA4FE/A', 2)
        flow.verified_pickup_store = 'R574'
        flow.action = Mock()
        handler = SimpleNamespace(checkout_preferences={'auto_submit': True, 'mode': 'RETAIL'},
                                  stage=Mock(), confirm=Mock(side_effect=AssertionError('No prompt')))
        with tempfile.TemporaryDirectory() as temp, patch.object(d, 'ROOT', Path(temp)), \
             patch.object(d, 'PAYMENT_ROOT', None), patch.object(d, 'save_auth'), \
             patch.object(d, 'checkout_delivery'), patch.object(d, 'checkout_shipping'), \
             patch.object(d, 'reauthenticate_checkout'), \
             patch.object(d, 'finish_payment', return_value={'success': True}), \
             patch.object(d, 'payment_qr'), patch.object(interaction, 'handler', handler), \
             contextlib.redirect_stdout(io.StringIO()):
            d.pay_bag(flow=flow)
        names = [c.args[1] for c in flow.action.call_args_list]
        self.assertNotIn('selectBillingOptionAction', names)
        self.assertEqual(names.count('continueFromReviewToProcess'), 1)
        handler.confirm.assert_not_called()

    def test_reuse_only_current_exact_region_and_available_store(self):
        for search, searches in (('四川 成都 锦江区', 0), ('旧地区', 1)):
            model = fulfillment_model(mixed=False)
            locator = 'checkout.fulfillment.pickupTab.pickup.storeLocator'
            options.value(model, locator + '.d')['searchInput'] = search
            flow = FakeFlow(model, mixed=False)
            handler = SimpleNamespace(checkout_preferences={'mode': 'RETAIL', 'city': '成都', 'store': 'R580'}, stage=Mock())
            with tempfile.TemporaryDirectory() as temp, patch.object(interaction, 'handler', handler):
                path = Path(temp)/'checkout.json'
                options.save_config(path, CONFIG)
                options.fulfillment(flow, path)
            self.assertEqual(sum(name == 'search' for _, name, _ in flow.calls), searches)
            self.assertEqual(flow.verified_pickup_store, 'R580')

    def test_skip_store_selection_requires_current_matching_signed_slots(self):
        locator = 'checkout.fulfillment.pickupTab.pickup.storeLocator'
        slots = 'checkout.fulfillment.pickupTab.pickup.timeSlot'
        for condition in ('valid', 'other_selected_store', 'other_slot_store', 'no_slots', 'no_signature'):
            with self.subTest(condition=condition):
                model = pickup_time_model()
                fresh_slots = deepcopy(options.value(model, slots))
                if condition == 'other_selected_store':
                    options.value(model, locator + '.d')['selectStore'] = 'R999'
                elif condition == 'other_slot_store':
                    options.value(model, slots + '.d')['storeId'] = 'R999'
                elif condition == 'no_slots':
                    options.value(model, slots + '.dateTimeSlots.d')['timeSlotWindows'] = []
                elif condition == 'no_signature':
                    for group in options.value(model, slots + '.dateTimeSlots.d.timeSlotWindows'):
                        for windows in group.values():
                            for window in windows:
                                window.pop('signKey')
                flow = FakeFlow(model, mixed=False)
                original_action = flow.action
                def action(module, name, data=None):
                    original_action(module, name, data)
                    if name == 'select':
                        options.value(flow.model, 'checkout.fulfillment.pickupTab.pickup')['timeSlot'] = deepcopy(fresh_slots)
                flow.action = action
                handler = SimpleNamespace(checkout_preferences={'mode': 'RETAIL', 'city': '成都', 'store': 'R580'}, stage=Mock())
                with tempfile.TemporaryDirectory() as temp, patch.object(interaction, 'handler', handler), contextlib.redirect_stdout(io.StringIO()):
                    path = Path(temp)/'checkout.json'
                    options.save_config(path, CONFIG)
                    options.fulfillment(flow, path)
                self.assertEqual(sum(name == 'select' for _, name, _ in flow.calls), 0 if condition == 'valid' else 1)
                submitted = next(data for _, name, data in flow.calls if name == 'continueFromFulfillmentToPickupContact')
                self.assertEqual(submitted[slots + '.dateTimeSlots.signKey'], 'test-sign-14')
