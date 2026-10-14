import os
import contextlib
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from duo_killer import Checkout, main
import duo_killer
import checkout_options as options


REGION = {'d': {'state': '四川', 'city': '成都', 'district': '锦江区', 'countryCode': 'CN',
                 'stateOptions': [{'value': '四川', 'label': '四川'}, {'value': '上海', 'label': '上海'}],
                 'cityOptions': [{'value': '成都', 'label': '成都'}],
                 'districtOptions': [{'value': '锦江区', 'label': '锦江区'}]},
          'a': {'Selectstate': {}, 'Selectcity': {}, 'Selectdistrict': {}}}
CONTACT = {'lastName': '测试', 'firstName': '用户', 'emailAddress': 'test@example.invalid', 'fullDaytimePhone': '13800000000'}
CONFIG = {'contact': CONTACT, 'delivery': {'state': '四川', 'city': '成都', 'district': '锦江区'}, 'address': {'street': '测试街道 1 号'}}
INVOICE = {'d': {'selectFapiao': 'e_personal_fdf'}, 'ePersonalFapiao': {'d': {'invoiceHeader': ''}}}


def shipment():
    return {'deliveryLocation': {'address': {'provinceCityDistrictTabsForCheckout': deepcopy(REGION)}},
            'shipmentGroups': {'c': ['group'], 'group': {'shipmentOptionsGroups': {'c': ['option'], 'option': {
                'shippingOptions': {'d': {'selectShippingOption': 'A8', 'options': [{'value': 'A8', 'label': '标准配送'}]}}}}}}}


def fulfillment_model(mixed=True):
    pickup = {'pickup': {'storeLocator': {'d': {'searchInput': '成都', 'selectStore': 'R580'},
        'address': {'stateCitySelectorForCheckout': deepcopy(REGION)},
        'searchResults': {'d': {'retailStores': [
            {'storeId': 'R580', 'storeName': '测试门店', 'storeDisabled': False,
             'availability': {'lineItemAvailability': [{'partName': '商品一', 'availabilityQuote': '今天可取货'},
                                                        {'partName': '商品二', 'availabilityQuote': '不可取货'}]}},
            {'storeId': 'R999', 'storeName': '不可选门店', 'storeDisabled': True}]}}}}}
    if mixed:
        pickup['deliveryForced'] = shipment()
    return {'checkout': {'d': {'page': 'Fulfillment'}, 'fulfillment': {
        'fulfillmentOptions': {'d': {'selectFulfillmentLocation': 'HOME', 'options': [
            {'value': 'HOME', 'title': '为我送货'}, {'value': 'RETAIL', 'title': '我要取货'}]}},
        'deliveryTab': {'delivery': shipment()}, 'pickupTab': pickup}}}


def contact_model(mixed=True):
    return {'checkout': {'d': {'page': 'PickupContact'}, 'pickupContact': {
        'a': {'continueFromPickupContactToShipping' if mixed else 'continueFromPickupContactToBilling': {}},
        'selfPickupContact': {'selfContact': {'address': {'d': {}, 'verificationModule': {
            'd': {'verificationToken': '', 'authRequired': False}}}}}, 'eFapiaoSelector': deepcopy(INVOICE)}}}


def pickup_time_model():
    model = fulfillment_model(mixed=False)
    options.value(model, 'checkout.fulfillment.pickupTab.pickup')['timeSlot'] = {
        'd': {'storeId': 'R580', 'enableTimeSlotWindows': True}, 'dateTimeSlots': {'d': {
            'date': '2026-10-14', 'timeSlotValue': '',
            'pickUpDates': [{'dayOfMonth': '14', 'date': '2026-10-14'},
                            {'dayOfMonth': '15', 'date': '2026-10-15'}],
            'timeSlotWindows': [{'14': [{'timeSlotValue': '14-10:15-10:30', 'Label': '10:15 – 10:30',
                'SlotId': 'test-slot-14', 'signKey': 'test-sign-14', 'timeZone': 'Asia/Shanghai',
                'checkInStart': '10:15 AM', 'checkInEnd': '10:30 AM'}]},
                {'15': [{'timeSlotValue': '15-11:00-11:15', 'Label': '11:00 – 11:15',
                'SlotId': 'test-slot-15', 'signKey': 'test-sign-15', 'timeZone': 'Asia/Shanghai',
                'checkInStart': '11:00 AM', 'checkInEnd': '11:15 AM'}]}]}}}
    return model


def shipping_model(invoice=False):
    address = {'d': {'selectAddress': 'address-0', 'radioAddresses': [
        {'value': 'address-0', 'isSavedAddress': True, 'addressLine1': '测试旧地址'}, {'value': 'newAddr'}]},
        'newAddress': {'address': {'d': {}, 'copyContact': {'d': {}},
            'stateCitySelectorForCheckout': deepcopy(REGION)}}}
    # HAR 新地址地区选择只有 Selectstate/Selectcity，区县随继续表单提交。
    address['newAddress']['address']['stateCitySelectorForCheckout']['a'].pop('Selectdistrict')
    node = {'a': {'continueFromShippingToBilling': {}}, 'addressSelector': address, 'addressNotification': {'address': {'d': {'emailAddress': 'test@example.invalid'}}}}
    if invoice:
        node['eFapiaoSelector'] = deepcopy(INVOICE)
    return {'checkout': {'d': {'page': 'Shipping'}, 'shipping': node}}


class FakeFlow(Checkout):
    def __init__(self, model, mixed=True):
        super().__init__(None)
        self.model = deepcopy(model)
        self.calls = []
        self.mixed = mixed

    def action(self, module, name, data=None):
        self.calls.append((module, name, deepcopy(data)))
        if name == 'selectFulfillmentLocationAction':
            self.require(module)['d']['selectFulfillmentLocation'] = data[module + '.selectFulfillmentLocation']
        elif name == 'select':
            self.require(module)['d']['selectStore'] = data[module + '.selectStore']
        elif name == 'continueFromFulfillmentToPickupContact':
            self.model = contact_model(self.mixed)
        elif name in ('continueFromFulfillmentToShipping', 'continueFromPickupContactToShipping'):
            self.model = shipping_model()
        elif name in ('continueFromShippingToBilling', 'continueFromShippingToUnknown', 'continueFromPickupContactToBilling'):
            self.model = {'checkout': {'d': {'page': 'Billing'}, 'billing': {}}}
        elif name.startswith('Select'):
            node = self.require(module)['d']
            for key in ('state', 'city', 'district'):
                node[key] = data.get(module + '.' + key, '')
            if name == 'Selectstate' and node['state'] == '上海':
                node['cityOptions'] = [{'value': '上海', 'label': '上海'}]
            if name == 'Selectcity' and node['city'] == '上海':
                node['districtOptions'] = [{'value': '浦东新区', 'label': '浦东新区'}]


class CheckoutOptionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        network = patch.object(options.requests.Session, 'get', side_effect=options.requests.Timeout)
        network.start()
        self.addCleanup(network.stop)
        self.path = Path(self.temp.name) / 'checkout.json'
        options.save_config(self.path, deepcopy(CONFIG))
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def test_mixed_pickup_follows_har_and_keeps_required_delivery(self):
        flow = FakeFlow(fulfillment_model())
        with patch('builtins.input', side_effect=['2', '', '1', '']):
            options.fulfillment(flow, self.path)
            options.shipping(flow, self.path)
        self.assertEqual([name for _, name, _ in flow.calls], [
            'selectFulfillmentLocationAction', 'search', 'select',
            'continueFromFulfillmentToPickupContact', 'continueFromPickupContactToShipping', 'continueFromShippingToBilling'])
        submitted = flow.calls[3][2]
        self.assertEqual(submitted['checkout.fulfillment.fulfillmentOptions.selectFulfillmentLocation'], 'RETAIL')
        self.assertTrue(any('deliveryForced' in k and k.endswith('.selectShippingOption') for k in submitted))
        self.assertEqual(flow.calls[4][2]['checkout.pickupContact.selfPickupContact.selfContact.address.fullDaytimePhone'], CONTACT['fullDaytimePhone'])
        self.assertEqual(set(flow.calls[5][2]), {'checkout.shipping.addressNotification.address.emailAddress',
                                               'checkout.shipping.addressSelector.selectAddress'})
        self.assertEqual(flow.model['checkout']['d']['page'], 'Billing')

    def test_prefilled_city_and_store_select_exact_available_store(self):
        flow = FakeFlow(fulfillment_model(mixed=False), mixed=False)
        preferences = {'mode': 'RETAIL', 'city': '成都', 'store': '测试门店'}
        with patch.object(options.interaction, 'handler', SimpleNamespace(checkout_preferences=preferences, stage=lambda *_: None)), \
                patch('builtins.input', side_effect=AssertionError('预设完整时不应再次询问')):
            options.fulfillment(flow, self.path)
        self.assertEqual([name for _, name, _ in flow.calls], [
            'selectFulfillmentLocationAction', 'search', 'select',
            'continueFromFulfillmentToPickupContact', 'continueFromPickupContactToBilling'])
        self.assertEqual(flow.calls[2][2]['checkout.fulfillment.pickupTab.pickup.storeLocator.selectStore'], 'R580')

    def test_official_apple_store_name_matches_checkout_name(self):
        model = fulfillment_model(mixed=False)
        region = options.value(model, 'checkout.fulfillment.pickupTab.pickup.storeLocator.address.stateCitySelectorForCheckout.d')
        region.update(state='江苏', city='无锡', district='惠山区', provinceCityDistrict='江苏 无锡 惠山区')
        stores = options.value(model, 'checkout.fulfillment.pickupTab.pickup.storeLocator.searchResults.d.retailStores')
        stores[0]['storeId'] = 'R574'
        stores[0]['storeName'] = '无锡恒隆广场'
        flow = FakeFlow(model, mixed=False)
        preferences = {'mode': 'RETAIL', 'city': '无锡', 'store': 'Apple 无锡恒隆广场'}
        with patch.object(options.interaction, 'handler', SimpleNamespace(checkout_preferences=preferences, stage=lambda *_: None)), \
                patch('builtins.input', side_effect=AssertionError('门店应自动匹配')):
            options.fulfillment(flow, self.path)
        self.assertEqual(flow.calls[2][2]['checkout.fulfillment.pickupTab.pickup.storeLocator.selectStore'], 'R574')
        locator = 'checkout.fulfillment.pickupTab.pickup.storeLocator'
        self.assertEqual(flow.calls[1][2][locator + '.searchInput'], '江苏 无锡 惠山区')

    def test_pickup_city_change_updates_region_before_search(self):
        flow = FakeFlow(fulfillment_model(mixed=False), mixed=False)
        locator = 'checkout.fulfillment.pickupTab.pickup.storeLocator'
        region = locator + '.address.stateCitySelectorForCheckout'
        with patch('builtins.input', side_effect=AssertionError('完整地区无需询问')):
            data = options.pickup_search_data(flow, locator, '上海市',
                {'state': '上海', 'city': '上海市', 'district': '浦东新区'})
        self.assertEqual(data[locator + '.searchInput'], '上海 浦东新区')
        self.assertEqual(data[region + '.city'], '上海')
        self.assertEqual(flow.calls[0][2][region + '.city'], '')
        self.assertEqual(flow.calls[0][2][region + '.district'], '')

    def test_pickup_search_preserves_full_region_from_apple(self):
        flow = FakeFlow(fulfillment_model(mixed=False), mixed=False)
        locator = 'checkout.fulfillment.pickupTab.pickup.storeLocator'
        data = options.pickup_search_data(flow, locator, '成都市', {})
        self.assertEqual(data[locator + '.searchInput'], '四川 成都 锦江区')
        self.assertEqual(flow.calls, [])

    def test_pickup_time_required_fields_reach_continue_payload(self):
        flow = FakeFlow(pickup_time_model(), mixed=False)
        preferences = {'mode': 'RETAIL', 'city': '成都', 'store': '测试门店'}
        handler = SimpleNamespace(checkout_preferences=preferences, stage=lambda *_: None,
            choose=lambda label, choices, default: default)
        with patch.object(options.interaction, 'handler', handler):
            options.fulfillment(flow, self.path)
        payload = next(data for _, name, data in flow.calls if name == 'continueFromFulfillmentToPickupContact')
        prefix = 'checkout.fulfillment.pickupTab.pickup.timeSlot.dateTimeSlots.'
        self.assertEqual(payload[prefix + 'date'], '2026-10-14')
        self.assertEqual(payload[prefix + 'dayRadio'], '14')
        self.assertEqual(payload[prefix + 'timeSlotValue'], '14-10:15-10:30')
        self.assertEqual(payload[prefix + 'timeSlotId'], 'test-slot-14')
        self.assertEqual(payload[prefix + 'signKey'], 'test-sign-14')
        self.assertEqual(payload[prefix + 'startTime'], '10:15 AM')
        self.assertEqual(payload[prefix + 'isRecommended'], 'false')

    def test_pickup_time_automatically_uses_earliest_date_even_with_later_default(self):
        flow = FakeFlow(pickup_time_model(), mixed=False)
        data = options.value(flow.model, 'checkout.fulfillment.pickupTab.pickup.timeSlot.dateTimeSlots.d')
        data['date'] = '2026-10-15'
        data['pickUpDates'].reverse()
        handler = SimpleNamespace(stage=lambda *_: None)
        with patch.object(options.interaction, 'handler', handler):
            payload = options.pickup_time_data(flow, 'R580')
        prefix = 'checkout.fulfillment.pickupTab.pickup.timeSlot.dateTimeSlots.'
        self.assertEqual(payload[prefix + 'date'], '2026-10-14')
        self.assertEqual(payload[prefix + 'signKey'], 'test-sign-14')
        self.assertEqual(payload[prefix + 'timeSlotId'], 'test-slot-14')

    def test_pickup_time_rejects_another_store(self):
        flow = FakeFlow(pickup_time_model(), mixed=False)
        with self.assertRaisesRegex(ValueError, '门店不一致'):
            options.pickup_time_data(flow, 'R574')
        self.assertEqual(flow.calls, [])

    def test_pickup_asks_for_required_identity_suffix_and_submits_correct_field(self):
        model = contact_model(mixed=False)
        model['checkout']['pickupContact']['selfPickupContact']['nationalIdSelf'] = {
            'd': {'enabled': True, 'nationalIdSelf': ''}}
        flow = FakeFlow(model, mixed=False)
        config = deepcopy(CONFIG)
        with patch.object(options, 'ask', return_value='123x') as ask:
            options.pickup_contact(flow, config, self.path)
        ask.assert_called_once()
        data = flow.calls[0][2]
        self.assertEqual(data['checkout.pickupContact.selfPickupContact.nationalIdSelf.nationalIdSelf'], '123X')
        self.assertNotIn('checkout.pickupContact.selfPickupContact.selfContact.address.nationalIdLast4', data)
        self.assertEqual(options.read_config(self.path)['contact']['nationalIdLast4'], '123X')

    def test_pickup_reuses_saved_identity_suffix_without_prompt(self):
        model = contact_model(mixed=False)
        model['checkout']['pickupContact']['selfPickupContact']['nationalIdSelf'] = {'d': {'enabled': True}}
        flow = FakeFlow(model, mixed=False)
        config = deepcopy(CONFIG)
        config['contact']['nationalIdLast4'] = '1234'
        with patch.object(options, 'ask', side_effect=AssertionError('已配置时不再询问')):
            options.pickup_contact(flow, config, self.path)
        self.assertEqual(flow.model['checkout']['d']['page'], 'Billing')

    def test_pickup_earliest_time_does_not_depend_on_returned_order(self):
        flow = FakeFlow(pickup_time_model(), mixed=False)
        data = options.value(flow.model, 'checkout.fulfillment.pickupTab.pickup.timeSlot.dateTimeSlots.d')
        later = deepcopy(data['timeSlotWindows'][0]['14'][0])
        later.update(timeSlotValue='14-14:00-14:15', SlotId='later', signKey='later-sign')
        data['timeSlotWindows'][0]['14'].insert(0, later)
        with patch.object(options, 'choose', side_effect=AssertionError('时段不能要求用户选择')):
            payload = options.pickup_time_data(flow, 'R580')
        self.assertEqual(payload['checkout.fulfillment.pickupTab.pickup.timeSlot.dateTimeSlots.timeSlotId'], 'test-slot-14')

    def test_pickup_time_empty_windows_prevent_continuation(self):
        model = pickup_time_model()
        options.value(model, 'checkout.fulfillment.pickupTab.pickup.timeSlot.dateTimeSlots.d')['timeSlotWindows'] = []
        flow = FakeFlow(model, mixed=False)
        handler = SimpleNamespace(checkout_preferences={'mode': 'RETAIL', 'city': '成都', 'store': '测试门店'},
                                  stage=lambda *_: None)
        with patch.object(options.interaction, 'handler', handler), self.assertRaisesRegex(ValueError, '没有可用取货签到时段'):
            options.fulfillment(flow, self.path)
        self.assertFalse(any(name.startswith('continue') for _, name, _ in flow.calls))

    def test_disabled_prefilled_store_reports_unavailable(self):
        flow = FakeFlow(fulfillment_model(mixed=False), mixed=False)
        preferences = {'mode': 'RETAIL', 'city': '成都', 'store': 'Apple 不可选门店'}
        with patch.object(options.interaction, 'handler', SimpleNamespace(checkout_preferences=preferences, stage=lambda *_: None)), \
                patch('time.sleep'), self.assertRaisesRegex(ValueError, '存在，但当前商品暂不可'):
            options.fulfillment(flow, self.path)
        self.assertFalse(any(name == 'select' for _, name, _ in flow.calls))

    def test_store_search_error_does_not_claim_store_is_missing(self):
        model = fulfillment_model(mixed=False)
        locator = options.value(model, 'checkout.fulfillment.pickupTab.pickup.storeLocator')
        locator['searchResults']['d'] = {}
        locator['d']['isStoreSearchErrorPresent'] = True
        locator['address']['stateCitySelectorForCheckout']['d'].update(
            state='江苏', city='无锡', district='惠山区', provinceCityDistrict='江苏 无锡 惠山区')
        flow = FakeFlow(model, mixed=False)
        preferences = {'mode': 'RETAIL', 'city': '无锡', 'store': 'Apple 无锡恒隆广场'}
        with patch.object(options.interaction, 'handler', SimpleNamespace(checkout_preferences=preferences, stage=lambda *_: None)), \
                patch('time.sleep') as pause, self.assertRaisesRegex(ValueError, '当前商品可能无法在该地区取货'):
            options.fulfillment(flow, self.path)
        self.assertEqual(sum(name == 'search' for _, name, _ in flow.calls), 3)
        self.assertEqual(pause.call_count, 2)
        self.assertFalse(any(name == 'select' for _, name, _ in flow.calls))

    def test_unavailable_prefilled_store_refreshes_without_submitting(self):
        flow = FakeFlow(fulfillment_model(mixed=False), mixed=False)
        preferences = {'mode': 'RETAIL', 'city': '成都', 'store': '不存在的门店'}
        with patch.object(options.interaction, 'handler', SimpleNamespace(checkout_preferences=preferences, stage=lambda *_: None)), \
                patch('time.sleep') as pause, self.assertRaisesRegex(ValueError, '未找到'):
            options.fulfillment(flow, self.path)
        self.assertEqual(sum(name == 'search' for _, name, _ in flow.calls), 30)
        self.assertEqual(pause.call_count, 29)
        self.assertFalse(any(name.startswith('continue') for _, name, _ in flow.calls))

    def test_home_and_saved_address(self):
        flow = FakeFlow(fulfillment_model())
        with patch('builtins.input', side_effect=['', '']):
            options.fulfillment(flow, self.path)
            options.shipping(flow, self.path)
        self.assertEqual([n for _, n, _ in flow.calls], ['continueFromFulfillmentToShipping', 'continueFromShippingToBilling'])
        self.assertFalse(any('pickup' in key for key in flow.calls[0][2]))

    def test_selected_delivery_time_is_sent_in_continuation(self):
        model = fulfillment_model()
        path = 'checkout.fulfillment.deliveryTab.delivery.shipmentGroups.group.shipmentOptionsGroups.option.shippingOptions'
        options.value(model, path)['d']['options'] = [
            {'value': 'A8', 'deliveryQuote': '明天', 'deliveryTimeSlot': '白天', 'shippingCost': '免费'},
            {'value': 'TD', 'deliveryQuote': '明天', 'deliveryTimeSlot': '15:00 - 17:00', 'shippingCost': '免费'}]
        flow = FakeFlow(model)
        with patch('builtins.input', side_effect=['', '2']), contextlib.redirect_stdout(io.StringIO()) as output:
            options.fulfillment(flow, self.path)
        self.assertIn('明天 / 15:00 - 17:00 / 免费', output.getvalue())
        self.assertEqual(flow.calls[-1][2][path + '.selectShippingOption'], 'TD')

    def test_pure_pickup_skips_shipping_address(self):
        flow = FakeFlow(fulfillment_model(mixed=False), mixed=False)
        with patch('builtins.input', side_effect=['2', '', '1']):
            options.fulfillment(flow, self.path)
            options.shipping(flow, self.path)
        self.assertEqual(flow.calls[-1][1], 'continueFromPickupContactToBilling')
        self.assertFalse(any('deliveryForced' in key for key in flow.calls[-2][2]))

    def test_new_address_payload_and_personal_invoice(self):
        flow = FakeFlow(shipping_model(invoice=True))
        flow.require('checkout.shipping.addressSelector')['d']['radioAddresses'] = [{'value': 'newAddr'}]
        with patch('builtins.input', side_effect=AssertionError('本地地址完整时不应询问')):
            options.shipping(flow, self.path)
        payload = flow.calls[-1][2]
        prefix = 'checkout.shipping.addressSelector.newAddress'
        self.assertEqual(payload['checkout.shipping.addressSelector.selectAddress'], 'newAddr')
        self.assertEqual(payload[prefix + '.address.street'], CONFIG['address']['street'])
        self.assertEqual(payload[prefix + '.address.copyContact.fullDaytimePhone'], CONTACT['fullDaytimePhone'])
        self.assertEqual(payload[prefix + '.address.stateCitySelectorForCheckout.district'], '锦江区')
        self.assertEqual(payload[prefix + '.saveToAddressBook'], 'true')
        self.assertIn('checkout.shipping.eFapiaoSelector.selectFapiao', payload)
        self.assertNotIn('checkout.shipping.addressContactPhone.address.mobilePhone', payload)

    def test_saved_api_address_and_contacts_ignore_local_presets(self):
        flow = FakeFlow(shipping_model())
        flow.require('checkout.shipping')['addressContactPhone'] = {'address': {
            'd': {'mobilePhone': '13900000000'}, 'b': {'mobilePhone': {'submit': True}}}}
        with patch.object(options, 'read_config', side_effect=AssertionError('已有地址不能读取本地预设')), \
                patch('builtins.input', return_value='1') as prompt:
            options.shipping(flow, self.path)
        prompt.assert_called_once()
        payload = flow.calls[-1][2]
        self.assertEqual(payload['checkout.shipping.addressSelector.selectAddress'], 'address-0')
        self.assertEqual(payload['checkout.shipping.addressContactPhone.address.mobilePhone'], '13900000000')
        self.assertFalse(any('newAddress' in k for k in payload))

    def test_api_selected_then_default_address_priority(self):
        for selected, expected in (('address-0', 'address-0'), ('newAddr', 'address-1')):
            with self.subTest(selected=selected):
                flow = FakeFlow(shipping_model())
                selector = flow.require('checkout.shipping.addressSelector')['d']
                selector['selectAddress'] = selected
                selector['radioAddresses'].append({'value': 'address-1', 'addressLine1': '官网默认地址',
                                                   'isSavedAddress': True, 'isDefaultAddr': True})
                with patch('builtins.input', return_value='') as prompt:
                    options.shipping(flow, self.path)
                prompt.assert_called_once()
                self.assertEqual(flow.calls[-1][2]['checkout.shipping.addressSelector.selectAddress'], expected)

    def test_quick_address_can_override_existing_api_address(self):
        flow = FakeFlow(shipping_model())
        flow.require('checkout.shipping')['addressContactPhone'] = {'address': {
            'd': {'mobilePhone': '13900000000'}, 'b': {'mobilePhone': {'submit': True}}}}
        with patch('builtins.input', side_effect=['2']) as prompt, \
                patch.object(options, 'configure_address') as configure, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            options.shipping(flow, self.path)
        prompt.assert_called_once()
        configure.assert_not_called()
        self.assertIn('1. 使用官网已有的地址', output.getvalue())
        self.assertIn('2. 使用本地快捷地址进行配置', output.getvalue())
        payload = flow.calls[-1][2]
        self.assertEqual(payload['checkout.shipping.addressSelector.selectAddress'], 'newAddr')
        self.assertEqual(payload['checkout.shipping.addressSelector.newAddress.address.street'], CONFIG['address']['street'])
        self.assertEqual(payload['checkout.shipping.addressNotification.address.emailAddress'], CONTACT['emailAddress'])
        self.assertEqual(payload['checkout.shipping.addressContactPhone.address.mobilePhone'], CONTACT['fullDaytimePhone'])

    def test_quick_address_choice_configures_missing_local_preset(self):
        self.path.unlink()
        flow = FakeFlow(shipping_model())
        with patch('builtins.input', side_effect=['2']), \
                patch.object(options, 'configure_address', side_effect=lambda path: options.save_config(path, CONFIG)) as configure:
            options.shipping(flow, self.path)
        configure.assert_called_once_with(self.path)
        self.assertEqual(flow.calls[-1][2]['checkout.shipping.addressSelector.selectAddress'], 'newAddr')

    def test_checkout_quick_address_forwards_explicit_auth_file(self):
        self.path.unlink()
        custom_auth = self.path.parent / 'work.json'
        flow = FakeFlow(shipping_model())
        with patch('builtins.input', side_effect=['2']), \
                patch.object(options, 'configure_address',
                             side_effect=lambda path, auth: options.save_config(path, CONFIG)) as configure:
            options.shipping(flow, self.path, custom_auth)
        configure.assert_called_once_with(self.path, custom_auth)

    def test_disabled_new_address_is_not_offered(self):
        flow = FakeFlow(shipping_model())
        flow.require('checkout.shipping.addressSelector')['d']['radioAddresses'][1]['disabled'] = True
        with patch('builtins.input', side_effect=['']), contextlib.redirect_stdout(io.StringIO()) as output:
            options.shipping(flow, self.path)
        self.assertNotIn('2. 使用本地快捷地址', output.getvalue())
        self.assertEqual(flow.calls[-1][2]['checkout.shipping.addressSelector.selectAddress'], 'address-0')

    def test_cancel_address_source_selection_does_not_submit(self):
        flow = FakeFlow(shipping_model())
        before = self.path.read_bytes()
        with patch('builtins.input', side_effect=EOFError), self.assertRaises(EOFError):
            options.shipping(flow, self.path)
        self.assertEqual(flow.calls, [])
        self.assertEqual(self.path.read_bytes(), before)

    def test_multiple_api_addresses_without_default_need_selection(self):
        flow = FakeFlow(shipping_model())
        selector = flow.require('checkout.shipping.addressSelector')['d']
        selector['selectAddress'] = 'newAddr'
        selector['radioAddresses'].append({'value': 'address-1', 'addressLine1': '另一地址', 'isSavedAddress': True})
        with patch('builtins.input', side_effect=['1', '2']) as prompt:
            options.shipping(flow, self.path)
        self.assertEqual(prompt.call_count, 2)
        self.assertEqual(flow.calls[-1][2]['checkout.shipping.addressSelector.selectAddress'], 'address-1')

    def test_delivery_region_prefers_api_even_if_local_region_differs(self):
        config = deepcopy(CONFIG)
        config['delivery'] = {'state': '上海', 'city': '上海', 'district': '浦东新区'}
        options.save_config(self.path, config)
        flow = FakeFlow(fulfillment_model())
        with patch('builtins.input', return_value='') as prompt:
            options.fulfillment(flow, self.path)
        prompt.assert_called_once()  # 仅收货方式，不重复询问官网已有地区。
        payload = flow.calls[-1][2]
        district = next(val for key, val in payload.items() if key.endswith('.district'))
        self.assertEqual(district, '锦江区')

    def test_region_change_uses_returned_options_and_does_not_send_old_children(self):
        flow = FakeFlow({'region': deepcopy(REGION)})
        data = options.region_data(flow, 'region', {'state': '上海市', 'city': '上海市', 'district': '浦东新区'})
        self.assertEqual(data['region.district'], '浦东新区')
        self.assertEqual(flow.calls[0][2]['region.city'], '')
        self.assertEqual(flow.calls[0][2]['region.district'], '')
        self.assertEqual([n for _, n, _ in flow.calls], ['Selectstate', 'Selectcity', 'Selectdistrict'])

    def test_county_label_uniquely_matches_api_district_without_prompt(self):
        flow = FakeFlow({'region': deepcopy(REGION)})
        flow.model['region']['d'].update(district='双流区', districtOptions=[{'value': '双流区', 'label': '双流区'}])
        with patch('builtins.input', side_effect=AssertionError('唯一匹配时不能要求输入')):
            data = options.region_data(flow, 'region', {**CONFIG['delivery'], 'district': '双流县'})
        self.assertEqual(data['region.district'], '双流区')
        self.assertEqual(flow.calls, [])

    def test_district_exact_match_wins_over_suffix_match(self):
        flow = FakeFlow({'region': deepcopy(REGION)})
        flow.model['region']['d'].update(district='示例县', districtOptions=[
            {'value': '示例区', 'label': '示例区'}, {'value': '示例县', 'label': '示例县'}])
        with patch('builtins.input', side_effect=AssertionError('精确匹配不能要求输入')):
            data = options.region_data(flow, 'region', {**CONFIG['delivery'], 'district': '示例县'})
        self.assertEqual(data['region.district'], '示例县')

    def test_ambiguous_or_unmatched_district_requires_confirmation(self):
        for desired in ('示例', '其他县'):
            with self.subTest(desired=desired):
                flow = FakeFlow({'region': deepcopy(REGION)})
                flow.model['region']['d'].update(district='示例区', districtOptions=[
                    {'value': '示例区', 'label': '示例区'}, {'value': '示例县', 'label': '示例县'}])
                with patch('builtins.input', return_value='2') as prompt:
                    data = options.region_data(flow, 'region', {**CONFIG['delivery'], 'district': desired})
                prompt.assert_called_once()
                self.assertEqual(data['region.district'], '示例县')

    def test_required_contact_verification_stops_before_submission(self):
        flow = FakeFlow(contact_model())
        flow.require('checkout.pickupContact.selfPickupContact.selfContact.address')['verificationModule']['d']['authRequired'] = True
        with self.assertRaisesRegex(ValueError, '手机验证'):
            options.pickup_contact(flow, deepcopy(CONFIG), self.path)
        self.assertEqual(flow.calls, [])

    def test_store_menu_can_switch_to_delivery_with_saved_api_address(self):
        for availability in ('available', 'disabled', 'empty'):
            with self.subTest(availability=availability):
                model = fulfillment_model()
                stores = options.value(model, 'checkout.fulfillment.pickupTab.pickup.storeLocator.searchResults.d.retailStores')
                if availability == 'disabled':
                    for store in stores:
                        store['storeDisabled'] = True
                elif availability == 'empty':
                    stores.clear()
                flow = FakeFlow(model)
                # 搜索之后切换配送必须读取服务器更新后的配送模型。
                original_action = flow.action
                def action(module, name, data=None):
                    original_action(module, name, data)
                    if name == 'selectFulfillmentLocationAction' and data[module + '.selectFulfillmentLocation'] == 'HOME':
                        shipping_path = 'checkout.fulfillment.deliveryTab.delivery.shipmentGroups.group.shipmentOptionsGroups.option.shippingOptions'
                        flow.require(shipping_path)['d'] = {'selectShippingOption': 'NEW', 'options': [{'value': 'NEW'}]}
                flow.action = action
                with patch('builtins.input', side_effect=['2', '', '2' if availability == 'available' else '', '1']) as prompt, \
                        contextlib.redirect_stdout(io.StringIO()) as output:
                    options.fulfillment(flow, self.path)
                    options.shipping(flow, self.path)
                self.assertEqual(prompt.call_count, 4)
                self.assertIn('为我送货（不选门店，改为配送）', output.getvalue())
                self.assertEqual([name for _, name, _ in flow.calls], [
                    'selectFulfillmentLocationAction', 'search', 'selectFulfillmentLocationAction',
                    'continueFromFulfillmentToShipping', 'continueFromShippingToBilling'])
                payload = flow.calls[3][2]
                self.assertEqual(payload['checkout.fulfillment.fulfillmentOptions.selectFulfillmentLocation'], 'HOME')
                self.assertTrue(any(k.endswith('.selectShippingOption') and v == 'NEW' for k, v in payload.items()))
                self.assertFalse(any('pickup' in k or 'selectStore' in k or 'searchInput' in k for k in payload))
                self.assertEqual(flow.calls[-1][2]['checkout.shipping.addressSelector.selectAddress'], 'address-0')
                self.assertEqual(flow.model['checkout']['d']['page'], 'Billing')

    def test_rejected_switch_to_delivery_stops_before_continuation(self):
        flow = FakeFlow(fulfillment_model())
        original_action = flow.action
        def action(module, name, data=None):
            if name == 'selectFulfillmentLocationAction' and data[module + '.selectFulfillmentLocation'] == 'HOME':
                return  # 服务端拒绝切换，仍然返回 RETAIL。
            original_action(module, name, data)
        flow.action = action
        with patch('builtins.input', side_effect=['2', '', '2']), \
                self.assertRaisesRegex(ValueError, '未确认切换为送货'):
            options.fulfillment(flow, self.path)
        self.assertFalse(any(name.startswith('continue') for _, name, _ in flow.calls))

    def test_no_available_store_and_delivery_disabled_stops(self):
        model = fulfillment_model()
        model['checkout']['fulfillment']['fulfillmentOptions']['d']['options'][0]['disabled'] = True
        stores = options.value(model, 'checkout.fulfillment.pickupTab.pickup.storeLocator.searchResults.d.retailStores')
        for store in stores:
            store['storeDisabled'] = True
        flow = FakeFlow(model)
        with patch('builtins.input', side_effect=['1', '']), self.assertRaisesRegex(ValueError, '没有可选项'):
            options.fulfillment(flow, self.path)
        self.assertEqual([n for _, n, _ in flow.calls], ['selectFulfillmentLocationAction', 'search'])

    def test_contact_rejection_does_not_proceed_to_shipping(self):
        flow = FakeFlow(contact_model())
        flow.action = lambda *args: None  # 模拟业务 200，但仍停留在表单，未通过校验。
        with self.assertRaises(ValueError):
            options.pickup_contact(flow, deepcopy(CONFIG), self.path)

    def test_address_rejection_stops_before_billing(self):
        flow = FakeFlow(shipping_model())
        flow.action = lambda *args: None
        with patch('builtins.input', return_value='2'), self.assertRaisesRegex(ValueError, '未通过服务端校验'):
            options.shipping(flow, self.path)

    def test_config_cli_is_local_preserves_other_settings_and_permissions(self):
        config = deepcopy(CONFIG)
        config['other'] = 'keep'
        options.save_config(self.path, config)
        with patch.object(duo_killer, 'ROOT', self.path.parent), patch('builtins.input', side_effect=[''] * 8), \
                patch.object(duo_killer, 'authenticate', side_effect=AssertionError('不能登录')):
            main(['-address'])
        self.assertEqual(options.read_config(self.path)['other'], 'keep')
        if os.name != 'nt':  # Windows uses inherited per-user ACLs, not POSIX mode bits.
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.path.parent / 'auth.json').exists())

    def test_cancelled_config_does_not_write_partial_address(self):
        original = self.path.read_text()
        with patch('builtins.input', side_effect=['新姓氏', EOFError()]), self.assertRaises(EOFError):
            options.configure_address(self.path)
        self.assertEqual(self.path.read_text(), original)

    def test_first_region_uses_apple_defaults_without_retyping(self):
        options.save_config(self.path, {'contact': CONTACT})
        with patch.object(options, 'current_region', return_value=CONFIG['delivery']) as lookup, \
                patch('builtins.input', side_effect=[''] * 7 + ['测试街道']) as prompt:
            options.configure_address(self.path)
        lookup.assert_called_once_with(self.path.parent / 'auth.json')
        self.assertIn('[四川]', prompt.call_args_list[4].args[0])
        self.assertEqual(options.read_config(self.path)['delivery'], CONFIG['delivery'])

    def test_region_lookup_uses_explicit_auth_file(self):
        custom_auth = self.path.parent / 'accounts' / 'work.json'
        options.save_config(self.path, {'contact': CONTACT})
        with patch.object(options, 'current_region', return_value=CONFIG['delivery']) as lookup, \
                patch('builtins.input', side_effect=[''] * 7 + ['测试街道']):
            options.configure_address(self.path, custom_auth)
        lookup.assert_called_once_with(custom_auth)

    def test_lookup_failure_uses_saved_region_and_changing_province_clears_city_defaults(self):
        with patch.object(options, 'current_region', return_value={}), \
                patch('builtins.input', side_effect=[''] * 4 + ['上海', '上海', '浦东新区', '新的详细地址']) as prompt:
            options.configure_address(self.path)
        self.assertNotIn('[成都]', prompt.call_args_list[5].args[0])
        self.assertNotIn('[锦江区]', prompt.call_args_list[6].args[0])
        self.assertEqual(options.read_config(self.path)['delivery']['state'], '上海')

    def test_api_region_overrides_local_defaults_without_reusing_old_street(self):
        remote = {'state': '上海', 'city': '上海', 'district': '浦东新区'}
        with patch.object(options, 'current_region', return_value=remote) as lookup, \
                patch('builtins.input', side_effect=[''] * 7 + ['新的详细地址']) as prompt:
            options.configure_address(self.path)
        lookup.assert_called_once()
        self.assertIn('[上海]', prompt.call_args_list[4].args[0])
        self.assertNotIn(CONFIG['address']['street'], prompt.call_args_list[7].args[0])
        self.assertEqual(options.read_config(self.path)['delivery'], remote)

    def test_tty_default_can_be_deleted_without_being_restored(self):
        with patch.object(options.sys.stdin, 'isatty', return_value=True), \
                patch.object(options.sys.stdout, 'isatty', return_value=True), \
                patch('prompt_toolkit.prompt', return_value='') as editor, \
                patch('builtins.input', return_value='上海') as prompt:
            self.assertEqual(options.ask('省份', '四川'), '上海')
        self.assertEqual(prompt.call_count, 1)
        self.assertEqual(prompt.call_args_list[0].args[0], '👉 省份：')
        editor.assert_called_once_with('👉 省份：', default='四川')

    def test_tty_choice_handles_arrow_keys_and_default(self):
        from prompt_toolkit.application import create_app_session
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput

        entries = [('saved', '使用官网地址'), ('quick', '使用快捷地址'), ('other', '其他地址')]
        cases = [('saved', '\x1b[B\r', 'quick'), ('quick', '\x1b[A\r', 'saved'),
                 ('quick', '\r', 'quick'), (None, '\r', 'saved')]
        for default, keys, expected in cases:
            with self.subTest(default=default, keys=repr(keys)), create_pipe_input() as pipe, \
                    create_app_session(input=pipe, output=DummyOutput()), \
                    patch.object(options.sys.stdin, 'isatty', return_value=True), \
                    patch.object(options.sys.stdout, 'isatty', return_value=True), \
                    patch('builtins.input', side_effect=AssertionError('终端选择不能要求输入序号')):
                pipe.send_text(keys)
                self.assertEqual(options.choose('地址来源', entries, default), expected)

    def test_tty_choice_cancel_never_returns_an_option(self):
        from prompt_toolkit.application import create_app_session
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput

        for key, error in (('\x03', KeyboardInterrupt), ('\x04', EOFError)):
            with self.subTest(key=repr(key)), create_pipe_input() as pipe, \
                    create_app_session(input=pipe, output=DummyOutput()), \
                    patch.object(options.sys.stdin, 'isatty', return_value=True), \
                    patch.object(options.sys.stdout, 'isatty', return_value=True), \
                    self.assertRaises(error):
                pipe.send_text(key)
                options.choose('地址来源', [('saved', '官网地址'), ('quick', '快捷地址')])

    def test_non_tty_choice_keeps_numeric_input_and_default(self):
        with patch.object(options.sys.stdin, 'isatty', return_value=False), \
                patch('builtins.input', side_effect=['9', '2']) as prompt:
            self.assertEqual(options.choose('地址来源', [('saved', '官网地址'), ('quick', '快捷地址')]), 'quick')
        self.assertEqual(prompt.call_count, 2)
        with patch.object(options.sys.stdin, 'isatty', return_value=False), patch('builtins.input', return_value=''):
            self.assertEqual(options.choose('地址来源', [('saved', '官网地址'), ('quick', '快捷地址')], 'quick'), 'quick')

    def test_tty_editor_cancel_does_not_fall_back_to_default(self):
        with patch.object(options.sys.stdin, 'isatty', return_value=True), \
                patch.object(options.sys.stdout, 'isatty', return_value=True), \
                patch('prompt_toolkit.prompt', side_effect=EOFError) as editor, \
                patch('builtins.input') as fallback, self.assertRaises(EOFError):
            options.ask('省份', '四川')
        fallback.assert_not_called()

    def test_region_lookup_failure_still_allows_manual_configuration(self):
        options.save_config(self.path, {'contact': CONTACT})
        with patch.object(options, 'current_region', return_value={}), \
                patch('builtins.input', side_effect=[''] * 4 + ['四川', '成都', '锦江区', '测试街道']):
            options.configure_address(self.path)
        self.assertEqual(options.read_config(self.path)['delivery'], CONFIG['delivery'])

    def test_region_api_ignores_server_default_and_handles_network_failure(self):
        import requests
        for default_location in (True, False):
            with self.subTest(default_location=default_location), patch.object(requests.Session, 'get') as get:
                get.return_value.json.return_value = {'head': {'status': '200'}, 'body': {'content': {
                    'defaultLocation': default_location, 'address': {**CONFIG['delivery'], 'street': 'must-not-use'}}}}
                result = options.current_region(self.path.parent / 'auth.json')
                self.assertEqual(result, {} if default_location else CONFIG['delivery'])
                self.assertEqual(get.call_args.kwargs, {'timeout': (3, 5), 'allow_redirects': False})
        with patch.object(requests.Session, 'get', side_effect=requests.Timeout):
            self.assertEqual(options.current_region(self.path.parent / 'auth.json'), {})


if __name__ == '__main__':
    unittest.main()


class DirectShippingFormTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'checkout.json'
        options.save_config(self.path, deepcopy(CONFIG))

    def flow(self):
        flow = FakeFlow(shipping_model())
        node = flow.require('checkout.shipping')
        node['a'] = {'continueFromShippingToUnknown': {}}
        node['addressSelector']['d'] = {'selectAddress': 'newAddr'}
        node['addressSelector']['savedAddresses'] = {}
        for section, key, old in [('addressContactEmail', 'emailAddress', 'old@example.invalid'),
                                  ('addressContactPhone', 'mobilePhone', '13900000000')]:
            node[section] = {'address': {'d': {key: old, 'was': {key: old}},
                'b': {key: {'submit': {'op': '!EQ', 'args': [{'val': '_'}, {'get': 'd.was.' + key}]}}}}}
        return flow

    def test_direct_form_without_radio_options_reaches_billing(self):
        flow = self.flow()
        with patch.object(options, 'ask', side_effect=AssertionError('unexpected prompt')):
            options.shipping(flow, self.path)
        payload = flow.calls[-1][2]
        self.assertEqual(flow.calls[-1][1], 'continueFromShippingToUnknown')
        self.assertEqual(payload['checkout.shipping.addressSelector.selectAddress'], 'newAddr')
        self.assertEqual(payload['checkout.shipping.addressContactEmail.address.emailAddress'], CONTACT['emailAddress'])
        self.assertEqual(payload['checkout.shipping.addressContactPhone.address.mobilePhone'], CONTACT['fullDaytimePhone'])
        self.assertEqual(options.value(flow.model, 'checkout.d.page'), 'Billing')

    def test_direct_form_requires_explicit_current_new_address(self):
        flow = self.flow()
        flow.require('checkout.shipping.addressSelector')['d']['selectAddress'] = 'unknown'
        with self.assertRaisesRegex(ValueError, '没有可用的收货地址'):
            options.shipping(flow, self.path)
        self.assertEqual(flow.calls, [])

    def test_explicitly_disabled_new_address_is_not_enabled_by_fallback(self):
        flow = self.flow()
        flow.require('checkout.shipping.addressSelector')['d']['radioAddresses'] = [{'value': 'newAddr', 'disabled': True}]
        with self.assertRaisesRegex(ValueError, '没有可用的收货地址'):
            options.shipping(flow, self.path)
        self.assertEqual(flow.calls, [])

    def test_ambiguous_continue_action_stops(self):
        flow = self.flow()
        flow.require('checkout.shipping')['a']['continueFromShippingToBilling'] = {}
        with self.assertRaisesRegex(ValueError, '唯一可识别'):
            options.shipping(flow, self.path)
        self.assertFalse(any(name.startswith('continueFromShipping') for _, name, _ in flow.calls))

    def test_unchanged_masked_contact_is_not_resubmitted(self):
        node = {'d': {'was': {'mobilePhone': '138****0000'}}, 'b': {'mobilePhone': {
            'submit': {'op': '!EQ', 'args': [{'val': '_'}, {'get': 'd.was.mobilePhone'}]}}}}
        self.assertFalse(options.should_submit_contact(node, 'mobilePhone', '138****0000'))
        self.assertTrue(options.should_submit_contact(node, 'mobilePhone', '13800000000'))
        node['b']['mobilePhone']['submit'] = {'op': 'UNKNOWN'}
        with self.assertRaisesRegex(ValueError, '规则无法识别'):
            options.should_submit_contact(node, 'mobilePhone', '13800000000')


class PreservePickupIdentityTests(unittest.TestCase):
    def test_shipping_keeps_saved_pickup_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'checkout.json'
            config = deepcopy(CONFIG)
            config['contact']['nationalIdLast4'] = '123X'
            options.save_config(path, config)
            flow = FakeFlow(shipping_model())
            with patch.object(options, 'choose', return_value='newAddr'):
                options.shipping(flow, path)
            self.assertEqual(options.read_config(path)['contact']['nationalIdLast4'], '123X')
            self.assertFalse(any('nationalIdLast4' in key for key in flow.calls[-1][2]))

    def test_product_without_identity_requirement_preserves_it_for_next_purchase(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'checkout.json'
            config = deepcopy(CONFIG)
            config['contact']['nationalIdLast4'] = '123X'
            options.save_config(path, config)
            options.pickup_contact(FakeFlow(contact_model(False), mixed=False), config, path)
            saved = options.read_config(path)
            self.assertEqual(saved['contact']['nationalIdLast4'], '123X')
            flow = FakeFlow(contact_model(False), mixed=False)
            flow.require('checkout.pickupContact.selfPickupContact')['nationalIdSelf'] = {'d': {'enabled': True}}
            with patch.object(options, 'ask', side_effect=AssertionError('must use saved suffix')):
                options.pickup_contact(flow, saved, path)
            self.assertEqual(flow.calls[-1][2]['checkout.pickupContact.selfPickupContact.nationalIdSelf.nationalIdSelf'], '123X')


class CrossCityPickupTests(unittest.TestCase):
    def flow(self):
        flow = FakeFlow(fulfillment_model(False), mixed=False)
        path = 'checkout.fulfillment.pickupTab.pickup.storeLocator.address.stateCitySelectorForCheckout'
        node = flow.require(path)['d']
        node.update(state='江苏', city='无锡', district='梁溪区', provinceCityDistrict='江苏 无锡 梁溪区',
                    stateOptions=[{'value':'江苏'}], cityOptions=[{'value':'无锡'}, {'value':'苏州'}])
        original = flow.action
        def action(module, name, data=None):
            original(module, name, data)
            if name == 'Selectcity':
                flow.require(path)['d'].update(district='', districtOptions=[{'value':'姑苏区'}, {'value':'吴中区'}])
        flow.action = action
        return flow, path

    def test_cross_city_search_selects_official_district_without_prompt(self):
        flow, path = self.flow()
        locator = path.removesuffix('.address.stateCitySelectorForCheckout')
        with patch.object(options, 'choose', side_effect=AssertionError('unexpected prompt')):
            data = options.pickup_search_data(flow, locator, '苏州', {'state':'江苏','city':'无锡','district':'惠山区'})
        self.assertEqual(data[locator+'.searchInput'], '江苏 苏州 姑苏区')
        self.assertEqual(data[locator+'.selectStore'], '')

    def test_delivery_does_not_guess_district(self):
        flow, path = self.flow()
        with patch.object(options, 'choose', side_effect=ValueError('district required')):
            with self.assertRaisesRegex(ValueError, 'district required'):
                options.region_data(flow, path, {'state':'江苏','city':'苏州'})

    def test_explicit_pickup_district_is_respected(self):
        flow, path = self.flow()
        locator = path.removesuffix('.address.stateCitySelectorForCheckout')
        with patch.object(options, 'choose', side_effect=AssertionError('unexpected prompt')):
            data = options.pickup_search_data(flow, locator, '苏州', {'state':'江苏','city':'苏州','district':'吴中区'})
        self.assertEqual(data[locator+'.searchInput'], '江苏 苏州 吴中区')

class PickupProvinceResolutionTests(unittest.TestCase):
    def test_blank_municipality_uses_fixed_city_and_live_districts(self):
        flow = FakeFlow(fulfillment_model(False), mixed=False)
        locator = 'checkout.fulfillment.pickupTab.pickup.storeLocator'
        path = locator + '.address.stateCitySelectorForCheckout'
        node = flow.require(path)
        node['d'].update(state='', city='', district='', stateOptions=[{'value':'上海'}, {'value':'江苏'}], cityOptions=[], districtOptions=[])
        calls = []
        def action(module, name, payload):
            calls.append(name)
            if name == 'Selectstate':
                # Exact shape observed live: city fixed, no cityOptions; state not echoed.
                node['d'].update(city='上海', cityOptions=[], districtOptions=[{'value':'黄浦区'}, {'value':'普陀区'}])
        flow.action = action
        with patch.object(options, 'choose', side_effect=AssertionError('unexpected prompt')):
            data = options.pickup_search_data(flow, locator, '上海市', {'state':'江苏','city':'无锡','district':'惠山区'})
        self.assertEqual(data[locator+'.searchInput'], '上海 黄浦区')
        self.assertEqual(data[path+'.city'], '上海')
        self.assertEqual(calls, ['Selectstate', 'Selectdistrict'])

    def test_directory_maps_cross_province_city_without_using_saved_home(self):
        current = {'state':'江苏', 'cityOptions':[{'value':'苏州'}], 'stateOptions':[{'value':'江苏'}, {'value':'浙江'}]}
        store = {'__typename':'RgdsStore','address':{'city':'杭州','stateName':'浙江'}}
        document = '<script id="__NEXT_DATA__">'+json.dumps({'stores':[store,store]})+'</script>'
        response = SimpleNamespace(text=document, raise_for_status=lambda:None)
        with patch.object(duo_killer, 'request_get', return_value=response):
            self.assertEqual(options.pickup_state(SimpleNamespace(session=object()), current, '杭州市'), '浙江')

    def test_ambiguous_or_unknown_city_does_not_guess_province(self):
        current = {'stateOptions':[{'value':'江苏'}, {'value':'浙江'}]}
        stores = [{'__typename':'RgdsStore','address':{'city':'测试市','stateName':state}} for state in ('江苏','浙江')]
        response = SimpleNamespace(text='<script id="__NEXT_DATA__">'+json.dumps(stores)+'</script>', raise_for_status=lambda:None)
        with patch.object(duo_killer, 'request_get', return_value=response):
            for city in ('测试市', '不存在'):
                with self.assertRaisesRegex(ValueError, '无法从官网门店目录'):
                    options.pickup_state(SimpleNamespace(session=object()), current, city)

    def test_wrong_city_never_silently_uses_current_default(self):
        flow = FakeFlow(fulfillment_model(False), mixed=False)
        path = 'checkout.fulfillment.pickupTab.pickup.storeLocator.address.stateCitySelectorForCheckout'
        with self.assertRaisesRegex(ValueError, '指定提货城市'):
            options.region_data(flow, path, {'state':'四川','city':'不存在'}, pickup_search=True)
