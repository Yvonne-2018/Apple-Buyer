import unittest
from copy import deepcopy
from unittest.mock import Mock
import requests
import duo_killer as d


def item(sku, qty=1, deletable=True):
    return {'d':{'sku':sku},'itemQuantity':{'d':{'quantity':qty}},'a':{'delete':{'url':'/shop/bag/delete'}} if deletable else {}}


class CartCleanupTests(unittest.TestCase):
    def flow(self, items):
        f=d.Checkout(None);f.model={'shoppingCart':{'items':deepcopy(items)}}
        f.page=Mock()
        def delete(path,name):
            self.assertEqual(name,'delete')
            del f.model['shoppingCart']['items'][path.rsplit('.',1)[1]]
        f.action=Mock(side_effect=delete)
        return f

    def test_only_extra_items_removed_and_target_quantity_preserved(self):
        f=self.flow({'item-target':item('AAAAACH/A',2),'item-old':item('BBBBBCH/A'),'item-extra':item('CCCCCCH/A')})
        d.clean_purchase_cart(f,'AAAAACH/A')
        self.assertEqual(d.model_items(f.model['shoppingCart']['items']),[('AAAAACH/A',2)])
        self.assertEqual(f.action.call_count,2)
        self.assertEqual(f.page.call_count,2)

    def test_clean_bag_has_no_extra_network_request(self):
        for items in ({},{'item-target':item('AAAAACH/A')}):
            f=self.flow(items);d.clean_purchase_cart(f,'AAAAACH/A')
            f.action.assert_not_called();f.page.assert_not_called()

    def test_all_old_items_removed_when_target_not_yet_added(self):
        f=self.flow({'item-old':item('BBBBBCH/A')})
        d.clean_purchase_cart(f,'AAAAACH/A')
        self.assertEqual(f.model['shoppingCart']['items'],{})

    def test_missing_action_stops_before_any_deletion(self):
        f=self.flow({'item-one':item('BBBBBCH/A'),'item-two':item('CCCCCCH/A',deletable=False)})
        with self.assertRaisesRegex(ValueError,'移除入口'):d.clean_purchase_cart(f,'AAAAACH/A')
        f.action.assert_not_called()

    def test_uncertain_delete_is_read_back_never_repeated(self):
        f=self.flow({'item-old':item('BBBBBCH/A')})
        f.action.side_effect=requests.Timeout()
        with self.assertRaisesRegex(ValueError,'移除未确认'):d.clean_purchase_cart(f,'AAAAACH/A')
        f.action.assert_called_once();f.page.assert_called_once()

    def test_timeout_after_success_is_confirmed_by_fresh_bag(self):
        f=self.flow({'item-old':item('BBBBBCH/A')});f.action.side_effect=requests.Timeout()
        f.page.side_effect=lambda *a:f.model['shoppingCart'].update(items={})
        d.clean_purchase_cart(f,'AAAAACH/A');f.action.assert_called_once()

    def test_duplicate_target_is_not_deleted(self):
        f=self.flow({'item-a':item('AAAAACH/A'),'item-b':item('AAAAACH/A'),'item-old':item('BBBBBCH/A')})
        with self.assertRaisesRegex(ValueError,'多个配置'):d.clean_purchase_cart(f,'AAAAACH/A')
        f.action.assert_not_called()
