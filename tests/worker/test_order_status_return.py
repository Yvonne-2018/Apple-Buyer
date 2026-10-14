import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import duo_killer as d


class OrderStatusReturnTests(unittest.TestCase):
    def test_rejected_pickup_shows_apple_message_without_repeating_order(self):
        flow = SimpleNamespace(url='https://secure11.www.apple.com.cn/shop/checkout',
            model={'checkout': {'d': {'page': 'Fulfillment'}, 'fulfillment': {
                'fulfillmentMessages': {'d': {'messages': {'errors': [
                    '你所选择的“送货与取货”选项已不再为本订单提供。请在下方选择一个新选项。']}}}}}}, action=Mock())
        with self.assertRaisesRegex(ValueError, '选项已不再为本订单提供'):
            d.finish_payment(flow)
        flow.action.assert_not_called()

    def test_unrecognized_return_is_not_treated_as_rejection_or_success(self):
        flow = SimpleNamespace(url='https://secure11.www.apple.com.cn/shop/checkout', model={}, action=Mock())
        with self.assertRaisesRegex(ValueError, '未取得订单处理状态'):
            d.finish_payment(flow)
        flow.action.assert_not_called()
