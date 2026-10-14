import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import interaction
import duo_killer as d
import checkout_options as o
import variant_fallback as v
from test_checkout_options import FakeFlow, fulfillment_model, CONFIG


class VariantFallbackTests(unittest.TestCase):
    def catalog(self):
        products = []
        prices = {}
        for sku, family, screen, capacity, color, amount in [
            ('AAAAACH/A','iphone18pro','small','256gb','red',9999),
            ('BBBBBCH/A','iphone18pro','small','256gb','blue',9999),
            ('CCCCCCH/A','iphone18pro','small','512gb','red',11999),
            ('DDDDDCH/A','iphone18promax','large','256gb','red',10999),
        ]:
            products.append(dict(partNumber=sku, familyType=family, dimensionScreensize=screen,
                                 dimensionCapacity=capacity, dimensionColor=color, fullPrice=sku))
            prices[sku] = {'validProducts':[sku], 'amountBeforeTradeIn':amount}
        return {'products':products, 'displayValues':{'prices':prices,
            'dimensionScreensize':{'small':{'value':'iPhone 18 Pro<span>screen</span>'},'large':{'value':'iPhone 18 Pro Max'}},
            'dimensionColor':{'red':{'value':'红色'}, 'blue':{'value':'蓝色'}}}}

    def test_exact_model_only_same_capacity_first_more_expensive_allowed(self):
        rows = v.variants_from_html('productSelectionData: '+json.dumps(self.catalog()),'AAAAACH/A')
        self.assertEqual([r['sku'] for r in rows],['AAAAACH/A','BBBBBCH/A','CCCCCCH/A'])
        self.assertGreater(Decimal(rows[2]['price']),Decimal(rows[0]['price']))

    def test_unverifiable_catalog_never_guesses_candidates(self):
        for html, sku in [('no data','AAAAACH/A'),('productSelectionData: '+json.dumps(self.catalog()),'MISSING/A')]:
            with self.assertRaises(ValueError):v.variants_from_html(html,sku)

    def stock(self, state='available', store='R574'):
        return {'head':{'status':'200'},'body':{'content':{'pickupMessage':{'stores':[
            {'storeNumber':store,'partsAvailability':{'AAAAACH/A':{'partNumber':'AAAAACH/A',
                'storePickEligible':True,'pickupDisplay':state}}}]}}}}

    def test_only_exact_store_and_explicit_available_are_accepted(self):
        self.assertEqual(v.available_skus(self.stock(),'R574',['AAAAACH/A']),['AAAAACH/A'])
        self.assertEqual(v.available_skus(self.stock('unavailable'),'R574',['AAAAACH/A']),[])
        for body in (self.stock('unknown'),self.stock(store='R999'),{},self.stock()):
            requested=['MISSING/A'] if body==self.stock() else ['AAAAACH/A']
            with self.assertRaises(ValueError):v.available_skus(body,'R574',requested)

    def test_inventory_failure_never_counts_as_no_stock(self):
        response=Mock();response.raise_for_status.side_effect=ValueError('HTTP failure')
        with self.assertRaisesRegex(ValueError,'HTTP failure'):
            v.choose_available(Mock(),Mock(return_value=response),[{'sku':'AAAAACH/A'}],'R574',set())

    def test_unavailable_checkout_triggers_before_refresh_sleep(self):
        model=fulfillment_model(mixed=False)
        stores=o.value(model,'checkout.fulfillment.pickupTab.pickup.storeLocator.searchResults.d.retailStores')
        stores[0]['storeDisabled']=True
        flow=FakeFlow(model,mixed=False)
        handler=SimpleNamespace(checkout_preferences={'mode':'RETAIL','city':'成都','store':'R580','fallback_configurations':True},stage=Mock())
        with tempfile.TemporaryDirectory() as temp,patch.object(interaction,'handler',handler),patch('time.sleep') as sleep:
            path=Path(temp)/'checkout.json';o.save_config(path,CONFIG)
            with self.assertRaises(v.PickupUnavailable) as error:o.fulfillment(flow,path)
            self.assertEqual(error.exception.store_id,'R580');sleep.assert_not_called()
            self.assertFalse(any(n.startswith('continue') for _,n,_ in flow.calls))

    def test_replacement_updates_target_price_and_receipt_before_submit(self):
        candidate={'sku':'BBBBBCH/A','name':'iPhone 18 Pro 512GB 蓝色','price':'11999'}
        flow=SimpleNamespace(session=Mock(),purchase_target=('AAAAACH/A',2))
        handler=SimpleNamespace(checkout_preferences={'fallback_configurations':True,'store':'无锡恒隆广场'},stage=Mock())
        with patch.object(interaction,'handler',handler),patch.object(d,'pay_bag',side_effect=[v.PickupUnavailable('R574'),{'success':True}]) as pay, \
             patch.object(v,'fetch_variants',return_value=[candidate]),patch.object(v,'choose_available',return_value=candidate),patch.object(v,'replace_cart') as replace:
            result=d.pay_with_alternatives(flow,'AAAAACH/A',2)
        replace.assert_called_once_with(flow,'AAAAACH/A','BBBBBCH/A',2)
        self.assertEqual(flow.purchase_target,('BBBBBCH/A',2))
        self.assertEqual(flow.replacement_price_limit,Decimal('23998'))
        self.assertEqual(handler.checkout_preferences['store'],'R574')
        self.assertTrue(result['configuration_changed']);self.assertEqual(result['requested_sku'],'AAAAACH/A')
        self.assertEqual(pay.call_count,2)

    def test_extra_cart_item_stops_before_delete(self):
        flow=Mock();flow.require.return_value={'items':{'one':{'d':{'sku':'AAAAACH/A'},'itemQuantity':{'d':{'quantity':1}}},
            'two':{'d':{'sku':'BBBBBCH/A'},'itemQuantity':{'d':{'quantity':1}}}}}
        with self.assertRaisesRegex(ValueError,'其他商品'):v.replace_cart(flow,'AAAAACH/A','CCCCCCH/A',1)
        flow.action.assert_not_called()

    def test_non_inventory_exception_is_never_retried_as_another_order(self):
        handler=SimpleNamespace(checkout_preferences={'fallback_configurations':True},stage=Mock())
        with patch.object(interaction,'handler',handler),patch.object(d,'pay_bag',side_effect=ValueError('提交结果未知')) as pay,patch.object(v,'choose_available') as choose:
            with self.assertRaisesRegex(ValueError,'提交结果未知'):d.pay_with_alternatives(Mock(),'AAAAACH/A',1)
            pay.assert_called_once();choose.assert_not_called()
