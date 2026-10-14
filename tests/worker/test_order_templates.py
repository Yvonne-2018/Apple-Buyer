"""Template checkout regression tests. Every network boundary uses synthetic models."""
import base64
import contextlib
from copy import deepcopy
from decimal import Decimal
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import checkout_options as options
import duo_killer as d
import interaction
from order_template import TemplatePolicy, validate_template
from protocol import Channel
from service import Service
from test_checkout_options import FakeFlow, CONFIG, fulfillment_model, shipping_model, pickup_time_model
import test_duo_killer as fixtures


def template(mode='HOME'):
    return dict(schema_version=1, id='11111111-1111-4111-8111-111111111111', revision=1,
                name='离线测试模板', sku='MLH74FE', quantity=1, max_total='170', mode=mode,
                address_source='local', saved_address='', contact={**deepcopy(CONFIG['contact']), 'nationalIdLast4': '123X'},
                delivery=deepcopy(CONFIG['delivery']), address={**CONFIG['address'], 'street2': ''},
                shipping=dict(strategy='cheapest', service='', max_fee='10'),
                pickup=dict(city='成都', store='R580', time_mode='earliest', date_from='', date_to='', time_from='', time_to=''),
                invoice=dict(header='测试个人'), wait_for_release=False, fallback_skus=[])


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.globals = {key: getattr(d, key) for key in ('ROOT', 'AUTH_PATH', 'LOG_PATH', 'PAYMENT_ROOT')}
        self.previous_handler = interaction.handler
        import alipay_qr
        self.qr_root = alipay_qr.ROOT
        self.addCleanup(self.restore)
        self.network = patch('requests.sessions.Session.request', side_effect=AssertionError('No live network'))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def restore(self):
        for key, value in self.globals.items():
            setattr(d, key, value)
        interaction.handler = self.previous_handler
        import alipay_qr
        alipay_qr.ROOT = self.qr_root

    def handler(self, value):
        policy = TemplatePolicy(value)
        interaction.handler = SimpleNamespace(template_policy=policy, checkout_preferences=policy.preferences(), stage=Mock(),
                                               text=Mock(side_effect=AssertionError('No business prompts')),
                                               choose=Mock(side_effect=AssertionError('No choices')))
        return policy

    def service(self):
        channel = Channel('account-a', 'task-a', io.StringIO())
        channel.prompt = Mock(side_effect=AssertionError('No prompts'))
        return Service(channel, self.root, self.root, 'offline@example.test', Mock())

    def test_invalid_template_stops_before_authentication_or_purchase(self):
        value = template()
        value['contact']['emailAddress'] = ''
        value['max_total'] = '0'
        service = self.service()
        with patch.object(service, 'ensure_auth_file') as auth, patch.object(d, 'purchase_sku') as purchase:
            with self.assertRaisesRegex(ValueError, '订单总额上限.*通知邮箱'):
                service.run('purchase', {'order_template': value, 'template_account_id': 'account-a'})
        auth.assert_not_called()
        purchase.assert_not_called()
        service.channel.prompt.assert_not_called()

    def test_wrong_account_cannot_execute_template(self):
        with self.assertRaisesRegex(ValueError, '账号'):
            self.service().run('purchase', {'order_template': template(), 'template_account_id': 'account-b'})

    def test_validation_rejects_invalid_money_quantity_sku_and_nested_config(self):
        for key, invalid in [('max_total', '-1'), ('max_total', 'NaN'), ('max_total', '1.001'),
                             ('quantity', True), ('quantity', 0), ('sku', 'invalid-sku'), ('contact', [])]:
            value = template()
            value[key] = invalid
            with self.subTest(key=key, value=invalid), self.assertRaises(ValueError):
                validate_template(value)
        original = template()
        validated = validate_template(original)
        self.assertEqual(validated['sku'], 'MLH74FE/A')
        self.assertEqual(original['sku'], 'MLH74FE')

    def test_business_prompt_guard_preserves_apple_otp(self):
        service = self.service()
        service.template_policy = TemplatePolicy(template())
        with self.assertRaisesRegex(ValueError, '缺少业务配置'):
            service.text('收货手机号码')
        with self.assertRaisesRegex(ValueError, '未能唯一确定'):
            service.choose('配送服务', [('A8', '标准配送')], 'A8')
        with self.assertRaisesRegex(ValueError, '未完成自动核验'):
            service.confirm({})
        service.channel.prompt.return_value = '012345'
        service.channel.prompt.side_effect = None
        self.assertEqual(service.text('设备六位验证码'), '012345')
        service.channel.prompt.assert_called_once()

    def test_shipping_policies_use_live_options_and_enforce_total_shipping_fee(self):
        policy = TemplatePolicy(template())
        choices = [{'value': 'EXPRESS', 'shippingCost': 'RMB 10', 'deliveryQuote': '明天'},
                   {'value': 'STANDARD', 'shippingCost': '免费', 'deliveryQuote': '后天'}]
        self.assertEqual(policy.shipping_option(choices), 'STANDARD')
        policy.data['shipping']['strategy'] = 'earliest'
        self.assertEqual(policy.shipping_option(choices), 'EXPRESS')
        policy.data['shipping'].update(strategy='specified', service='EXPRESS')
        with self.assertRaisesRegex(ValueError, '运费上限'):
            policy.shipping_option(choices)  # Two shipment groups cannot each spend the full cap.

    def test_unknown_or_ambiguous_shipping_is_rejected(self):
        for choices in ([{'value': 'A8'}], [{'value': 'A8', 'shippingCost': '$10'}]):
            with self.assertRaisesRegex(ValueError, '运费'):
                TemplatePolicy(template()).shipping_option(choices)
        policy = TemplatePolicy(template())
        policy.data['shipping'].update(strategy='specified', service='missing')
        with self.assertRaisesRegex(ValueError, '未唯一匹配'):
            policy.shipping_option([{'value': 'A8', 'shippingCost': '免费'}])

    def test_pickup_time_window_uses_current_signed_slot_and_rejects_missing_window(self):
        value = template('RETAIL')
        value['pickup'].update(time_mode='window', date_from='2026-10-15', date_to='2026-10-15', time_from='10:00', time_to='12:00')
        self.handler(value)
        payload = options.pickup_time_data(FakeFlow(pickup_time_model()), 'R580')
        self.assertEqual(payload['checkout.fulfillment.pickupTab.pickup.timeSlot.dateTimeSlots.signKey'], 'test-sign-15')
        interaction.handler.template_policy.data['pickup']['time_to'] = '10:30'
        with self.assertRaisesRegex(ValueError, '没有可用'):
            options.pickup_time_data(FakeFlow(pickup_time_model()), 'R580')

    def test_local_address_does_not_use_official_default(self):
        policy = self.handler(template())
        path = self.root / 'checkout.json'
        options.save_config(path, {key: policy.data[key] for key in ('contact', 'delivery', 'address')})
        flow = FakeFlow(shipping_model(invoice=True))
        options.shipping(flow, path)
        submitted = flow.calls[-1][2]
        self.assertEqual(submitted['checkout.shipping.addressSelector.selectAddress'], 'newAddr')
        self.assertEqual(submitted['checkout.shipping.eFapiaoSelector.ePersonalFapiao.invoiceHeader'], '测试个人')
        self.assertEqual(flow.template_address, CONFIG['address']['street'])

    def test_saved_address_is_an_exact_account_local_match(self):
        value = template()
        value.update(address_source='saved', saved_address='四川 成都 锦江区 测试旧地址')
        policy = self.handler(value)
        path = self.root / 'checkout.json'
        options.save_config(path, {key: policy.data[key] for key in ('contact', 'delivery', 'address')})
        model = shipping_model()
        options.value(model, 'checkout.shipping.addressSelector.d.radioAddresses')[0]['provinceCityDistrict'] = '四川 成都 锦江区'
        flow = FakeFlow(model)
        options.shipping(flow, path)
        self.assertEqual(flow.calls[-1][2]['checkout.shipping.addressSelector.selectAddress'], 'address-0')
        policy.data['saved_address'] = '其他地址'
        with self.assertRaisesRegex(ValueError, '未唯一匹配'):
            options.shipping(FakeFlow(model), path)

    def test_no_wait_template_stops_unavailable_store_without_polling(self):
        policy = self.handler(template('RETAIL'))
        model = fulfillment_model(mixed=False)
        options.value(model, 'checkout.fulfillment.pickupTab.pickup.storeLocator.searchResults.d.retailStores')[0]['storeDisabled'] = True
        flow = FakeFlow(model, mixed=False)
        path = self.root / 'checkout.json'
        options.save_config(path, {key: policy.data[key] for key in ('contact', 'delivery', 'address')})
        with patch('time.sleep', side_effect=AssertionError('No waiting')), self.assertRaisesRegex(ValueError, '暂不可'):
            options.fulfillment(flow, path)
        self.assertEqual(sum(name == 'search' for _, name, _ in flow.calls), 1)

    def test_alternatives_are_limited_to_ordered_allowlist_and_budget(self):
        value = template('RETAIL')
        value['fallback_skus'] = ['MK2W4CH', 'MK2U4CH']
        policy = TemplatePolicy(value)
        rows = [{'sku': part + '/A', 'price': price} for part, price in [('MK2U4CH', '150'), ('MK2W4CH', '160'), ('MK2T4CH', '100'), ('MLH74FE', '999')]]
        self.assertEqual([r['sku'] for r in policy.filter_candidates(rows)], ['MK2W4CH/A', 'MK2U4CH/A'])

    def run_checkout(self, mode='HOME', *, total='160', fail_submission=False, ordered_stores=False):
        value = template(mode)
        if ordered_stores:
            value['pickup']['store_ids'] = ['R580', 'R502']
        service = self.service()
        review = fixtures.PurchaseFlowTests.review_model()
        item = {'d': {'sku': 'MLH74FE/A', 'name': '测试商品', 'totalPrice': 'RMB 150'}, 'itemQuantity': {'d': {'quantity': 1}}}
        review['shoppingCart'].update(items={'item-target': item}, actions={'a': {'checkout': {'url': '/fixture'}}})
        review['checkout']['review']['fulfillmentReview']['items'] = {'item-target': item}
        review['checkout']['review']['summary']['d']['total'] = 'RMB ' + total
        review['checkout']['companionBar'] = {'orderDetails': {'shippingCompanionBar': {'address': {'d': {'street': CONFIG['address']['street']}}}}}
        initial = deepcopy(review)

        class PurchaseFlow(FakeFlow):
            def __init__(self, session):
                super().__init__(initial, mixed=False)
                self.session = session
                self.headers['x-aos-stk'] = 'fixture'
                self.page_response = Mock()

            def page(self, *args, **kwargs):
                return None

            def action(self, module, name, data=None):
                if name == 'checkout':
                    self.calls.append((module, name, data))
                    self.model = fulfillment_model(mixed=False)
                    if ordered_stores:
                        options.value(self.model, 'checkout.fulfillment.pickupTab.pickup.storeLocator.searchResults.d')['retailStores'] = [
                            {'storeId': 'R580', 'storeDisabled': True}, {'storeId': 'R502', 'storeDisabled': False}]
                    options.value(self.model, 'checkout.fulfillment.deliveryTab.delivery.shipmentGroups.group.shipmentOptionsGroups.option.shippingOptions.d.options')[0]['shippingCost'] = 'RMB 10'
                elif name == 'continueFromBillingToReview':
                    self.calls.append((module, name, data))
                    self.model = deepcopy(review)
                elif name == 'continueFromReviewToProcess':
                    self.calls.append((module, name, data))
                    if fail_submission:
                        import requests
                        raise requests.Timeout('synthetic uncertain order')
                else:
                    super().action(module, name, data)
                    if options.value(self.model, 'checkout.d.page') == 'Billing':
                        self.model['checkout']['billing'] = deepcopy(review['checkout']['billing'])

        flow = PurchaseFlow(Mock())
        # Synthetic image, never a payable code.
        qr = self.root / 'demo.png'
        qr.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII='))
        with patch.object(d, 'Checkout', return_value=flow), patch.object(d, 'ensure_strong_login', return_value=False), \
                patch.object(d, 'load_auth'), patch.object(d, 'save_auth'), patch.object(d, 'check_login', return_value={'logged_in': True}), \
                patch.object(d, 'add_to_bag'), patch.object(d, 'finish_payment', return_value={'success': True, 'status': 'pending_payment', 'order_number': 'DEMO-ONLY'}), \
                patch.object(d, 'payment_qr', side_effect=lambda result: result.update(qr_image=str(qr))):
            try:
                result = service.run('purchase', {'order_template': value, 'template_account_id': 'account-a'})
            except BaseException as error:
                return service, flow, error
        return service, flow, result

    def test_home_and_pickup_templates_reach_qr_without_any_prompts(self):
        for mode in ('HOME', 'RETAIL'):
            with self.subTest(mode=mode):
                service, flow, result = self.run_checkout(mode)
                self.assertIsInstance(result, dict, repr(result))
                self.assertEqual(result['status'], 'pending_payment')
                self.assertTrue(result['qr_data'].startswith('data:image/png;base64,'))
                self.assertEqual(sum(name == 'continueFromReviewToProcess' for _, name, _ in flow.calls), 1)
                service.channel.prompt.assert_not_called()
                self.assertEqual(result['template_revision'], 1)
                self.assertFalse((self.root / 'checkout.json').exists())

    def test_over_budget_and_uncertain_submission_never_repeat_place_order(self):
        service, flow, error = self.run_checkout(total='171')
        self.assertIsInstance(error, ValueError)
        self.assertFalse(service.submitted)
        self.assertEqual(sum(name == 'continueFromReviewToProcess' for _, name, _ in flow.calls), 0)
        service, flow, error = self.run_checkout(fail_submission=True)
        self.assertIsInstance(error, Exception)
        self.assertTrue(service.submitted)
        self.assertEqual(sum(name == 'continueFromReviewToProcess' for _, name, _ in flow.calls), 1)

    def test_ordered_pickup_fails_over_and_reaches_qr_with_one_submission_no_prompts(self):
        service, flow, result = self.run_checkout('RETAIL', ordered_stores=True)
        self.assertIsInstance(result, dict, repr(result))
        self.assertEqual(result['status'], 'pending_payment')
        self.assertEqual(flow.verified_pickup_store, 'R502')
        self.assertTrue(result['qr_data'].startswith('data:image/png;base64,'))
        self.assertEqual(sum(name == 'continueFromReviewToProcess' for _, name, _ in flow.calls), 1)
        service.channel.prompt.assert_not_called()

    def test_review_address_mismatch_stops_submission(self):
        policy = TemplatePolicy(template())
        flow = SimpleNamespace(template_mode='HOME', template_address='模板地址', model={
            'checkout': {'companionBar': {'orderDetails': {'shippingCompanionBar': {'address': {'d': {'street': '不同地址'}}}}}}})
        with self.assertRaisesRegex(ValueError, '地址与模板不一致'):
            policy.verify_fulfillment(flow)
