"""Same-model alternatives from live Apple product and pickup responses."""
import json
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import requests
import interaction
from shop_catalog import HEADERS, PART, text


class PickupUnavailable(ValueError):
    def __init__(self, store_id):
        super().__init__('指定门店当前配置不可取货：' + store_id)
        self.store_id = store_id


def capacity_gb(value):
    match = re.fullmatch(r'(\d+)\s*(gb|tb)', value, re.I)
    if not match:
        raise ValueError('官网容量格式无法识别，未自动换配置。')
    return int(match[1]) * (1024 if match[2].lower() == 'tb' else 1)


def variants_from_html(html, requested):
    match = re.search(r'\bproductSelectionData:\s*', html)
    if not match:
        raise ValueError('官网未提供可验证的机型配置表，未自动换配置。')
    try:
        data, _ = json.JSONDecoder().raw_decode(html[match.end():])
        products, display = data['products'], data['displayValues']
        originals = [p for p in products if p.get('partNumber', '').upper() == requested]
        if len(originals) != 1:
            raise ValueError('原 SKU 未在官网配置表中唯一匹配，未自动换配置。')
        original = originals[0]
        family, screen = original.get('familyType'), original.get('dimensionScreensize')
        if not isinstance(family, str) or not family.startswith('iphone') or not screen:
            raise ValueError('只支持具有明确机型标识的 iPhone 配置，未自动换配置。')
        rows = []
        for product in products:
            sku = product.get('partNumber', '').upper()
            if (not PART.fullmatch(sku) or product.get('familyType') != family
                    or product.get('dimensionScreensize') != screen
                    or product.get('isCarrierDevice') or product.get('comingSoon') or product.get('getReady')
                    or any(product.get(k) != original.get(k) for k in ('carrierPolicyPart', 'carrierPolicyType'))):
                continue
            capacity = product['dimensionCapacity']
            price_info = display['prices'][product['fullPrice']]
            if sku not in price_info.get('validProducts', []):
                continue
            price = Decimal(str(price_info['amountBeforeTradeIn']))
            if not price.is_finite() or price <= 0:
                continue
            model_label = text(display['dimensionScreensize'][screen]['value'].split('<span', 1)[0])
            color = text(display['dimensionColor'][product['dimensionColor']]['value'])
            rows.append({'sku': sku, 'family': family, 'capacity_gb': capacity_gb(capacity),
                         'name': f'{model_label} {capacity.upper()} {color}', 'price': str(price)})
        original_row = next((r for r in rows if r['sku'] == requested), None)
        if original_row is None or len({r['sku'] for r in rows}) != len(rows) or len(rows) > 64:
            raise ValueError('配置表不完整或不唯一，未自动换配置。')
        return sorted(rows, key=lambda r: (r['sku'] != requested,
            r['capacity_gb'] != original_row['capacity_gb'],
            abs(r['capacity_gb'] - original_row['capacity_gb']), Decimal(r['price']), r['sku']))
    except (KeyError, TypeError, InvalidOperation, json.JSONDecodeError) as error:
        raise ValueError('官网配置表格式无法识别，未自动换配置。') from error


def fetch_variants(request_get, requested):
    with requests.Session() as public:
        public.headers.update(HEADERS)
        response = request_get(public, '读取同机型配置', 'https://www.apple.com.cn/shop/product/' + requested, safe_retry=True)
        response.raise_for_status()
        parsed = urlsplit(response.url)
        if parsed.scheme != 'https' or parsed.hostname != 'www.apple.com.cn':
            raise ValueError('商品页返回非预期地址，未自动换配置。')
        return variants_from_html(response.text, requested)


def available_skus(body, store_id, requested):
    try:
        if str(body['head']['status']) != '200':
            raise ValueError('官网库存业务响应失败，未把它当作无货。')
        stores = body['body']['content']['pickupMessage']['stores']
        matches = [s for s in stores if s.get('storeNumber') == store_id]
        if len(matches) != 1:
            raise ValueError('库存响应未唯一匹配指定门店，未切换门店。')
        availability = matches[0]['partsAvailability']
        available = []
        for sku in requested:
            part = availability[sku]
            state = part.get('pickupDisplay')
            if state not in ('available', 'unavailable', 'ineligible'):
                raise ValueError('官网库存状态未知，未把它当作无货。')
            if part.get('partNumber') != sku:
                raise ValueError('官网库存 SKU 不匹配，已停止。')
            if state == 'available' and part.get('storePickEligible') is True:
                available.append(sku)
        return available
    except (KeyError, TypeError) as error:
        raise ValueError('官网库存响应不完整，未把它当作无货。') from error


def choose_available(session, request_get, candidates, store_id, attempted):
    remaining = [row for row in candidates if row['sku'] not in attempted]
    for offset in range(0, len(remaining), 8):
        batch = remaining[offset:offset + 8]
        params = {'fae': 'true', 'store': store_id, 'searchNearby': 'false', 'mt': 'regular', 'pl': 'true'}
        params.update({f'parts.{index}': row['sku'] for index, row in enumerate(batch)})
        response = request_get(session, '查询同机型门店库存',
                               'https://www.apple.com.cn/shop/fulfillment-messages', params=params, safe_retry=True)
        response.raise_for_status()
        available = available_skus(response.json(), store_id, [r['sku'] for r in batch])
        for row in batch:
            if row['sku'] in available:
                return row
    return None


def replace_cart(flow, old_sku, new_sku, quantity):
    # This operation runs only before an order submission, and verifies each mutation.
    from duo_killer import BASE_URL, model_items, cart_item, field, add_to_bag
    flow.page(BASE_URL + '/shop/bag', '换配置前核对购物袋')
    if model_items(flow.require('shoppingCart').get('items', {})) != [(old_sku, quantity)]:
        raise ValueError('购物袋已被更改或含其他商品，未自动替换。')
    item_id, item = cart_item(flow, old_sku)
    if not field(item, 'a.delete.url'):
        raise ValueError('当前购物袋没有商品移除入口，未自动替换。')
    flow.action('shoppingCart.items.' + item_id, 'delete')
    if model_items(flow.require('shoppingCart').get('items', {})):
        raise ValueError('原配置移除未确认，已停止；请检查购物袋，不要重复下单。')
    interaction.report_stage('原配置已移除，正在加入替代配置：' + new_sku)
    add_to_bag(new_sku, quantity=quantity, set_total=True, flow=flow)
    if model_items(flow.require('shoppingCart').get('items', {})) != [(new_sku, quantity)]:
        raise ValueError('替代配置数量未确认，已停止。')
