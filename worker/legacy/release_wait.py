"""Cancellable read-only polling before purchase mutations."""
import json
import re
import time
from itertools import count

import requests
import interaction


class AddRejected(str):
    """Server explicitly returned addedToBag=false, not an ambiguous timeout."""


def enabled():
    return (getattr(interaction.handler, 'checkout_preferences', {}) or {}).get('wait_for_release') is True


def attempts(limit):
    return count() if enabled() else iter(range(limit))


def pause(message):
    interaction.report_stage(message + '；2 秒后刷新，可随时取消')
    waiter = getattr(interaction.handler, 'wait_retry', None)
    if callable(waiter):
        waiter(2)
    else:
        time.sleep(2)


def unavailable_reason(html, part):
    match = re.search(r'\bproductSelectionData:\s*', html)
    if not match:
        # Accessories and other product pages need not contain an iPhone matrix.
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(html[match.end():])
        products = data['products']
        if not isinstance(products, list):
            raise ValueError('invalid products')
        rows = [p for p in products if p.get('partNumber', '').upper() == part]
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise ValueError('商品配置数据无法识别，已停止，未尝试加购。') from error
    if not rows:
        return '官网尚未列出该 SKU（也可能是 SKU 填写错误）'
    if len(rows) != 1:
        raise ValueError('商品 SKU 匹配不唯一，已停止。')
    if rows[0].get('comingSoon') is True or rows[0].get('getReady') is True:
        return '官网显示商品尚未开放购买'
    return None


def product_page(session, request_get, url, part):
    while True:
        try:
            response = request_get(session, '商品页', url,
                                   headers={'Cache-Control': 'no-cache'})
        except (requests.ConnectionError, requests.Timeout):
            if not enabled():
                raise
            pause('商品状态查询网络异常，保留任务')
            continue
        if enabled() and response.status_code in (404, 500, 502, 503, 504):
            code = response.status_code
            response.close()
            pause(f'商品状态查询 HTTP {code}，尚不能确认可购买')
            continue
        response.raise_for_status()
        reason = unavailable_reason(response.text, part) if enabled() else None
        if reason is None:
            return response
        response.close()
        pause(reason)
