"""读取 Apple 中国商店推荐位及其公开 SKU，不读取登录配置。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html import unescape
import json
import re
import unicodedata
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import requests

STORE_URL = "https://www.apple.com.cn/store"
DUO_URL = "https://www.apple.com.cn/shop/buy-iphone/iphone-duo"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",
           "Accept-Language": "zh-CN,zh;q=0.9"}
PART = re.compile(r"[A-Z0-9]{5,}/[A-Z0-9]+", re.I)


def text(value):
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", value or "")).split())


def objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)


def product_url(value):
    url = urljoin(STORE_URL, value or "")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "www.apple.com.cn"
            or not parsed.path.startswith("/shop/")):
        raise ValueError("推荐商品返回了非预期链接。")
    return url


def recommendations(html):
    result, seen = [], set()
    for _, script in re.findall(r"<script\b([^>]*)>(.*?)</script>", html, re.S | re.I):
        if "pageLevelData.slots.push" not in script:
            continue
        match = re.search(r"\bcards:\s*", script)
        if not match:
            continue
        cards, _ = json.JSONDecoder().raw_decode(script[match.end():].lstrip())
        shelf = re.search(r"shelfTitleA11yText:\s*`([^`]*)`", script)
        section = text(shelf[1]) if shelf else "推荐商品"
        for card in objects(cards):
            if card.get("view") == "recommendationCard":
                for product in card.get("products", []):
                    sku = product.get("part", "").upper()
                    if not PART.fullmatch(sku) or sku in seen:
                        continue
                    price = card.get("dictionaries", {}).get("price", {}).get(product.get("price"), {})
                    seen.add(sku)
                    result.append({"kind": "product", "section": section, "name": text(product.get("title")),
                                   "url": product_url(product.get("productDetailsUrl")),
                                   "sku_status": "resolved", "stock_status": "not_queried",
                                   "skus": [{"sku": sku, "name": text(product.get("title")),
                                             "price": price.get("currentPrice", {}).get("raw_amount"),
                                             "currency": price.get("priceCurrency", "CNY"),
                                             "specifications": product.get("dimensions", {})}]})
            link = card.get("textLink", {})
            # 带系列标识的推荐卡才展开，导航、服务和营销分类链接不进入商品列表。
            marker = link.get("omnitureData", {}).get("partNumber")
            if not marker or not card.get("headline"):
                continue
            url = product_url(link.get("url"))
            if not re.match(r"/shop/buy-[^/]+/[^/]+", urlsplit(url).path) or url in seen:
                continue
            seen.add(url)
            result.append({"kind": "series", "section": section, "name": text(card["headline"]),
                           "url": url, "price_text": text(card.get("subheadline")),
                           "sku_status": "pending", "stock_status": "not_queried", "skus": []})
    if not result:
        raise ValueError("页面没有可解析的推荐商品；可能是页面结构变化或返回了错误页。")
    return result


def series_skus(html):
    result, seen = [], set()
    for attrs, script in re.findall(r"<script\b([^>]*)>(.*?)</script>", html, re.S | re.I):
        if not re.search(r'\bid\s*=\s*[\"\']metrics[\"\']', attrs):
            continue
        for item in objects(json.loads(script)):
            sku = item.get("partNumber", "").upper()
            # 只读商品 metrics，避免混入页底推荐配件和非 SKU 的营销标识。
            if not PART.fullmatch(sku) or sku in seen or not item.get("name"):
                continue
            seen.add(sku)
            result.append({"sku": sku, "name": text(item["name"]),
                           "price": item.get("price", {}).get("fullPrice"), "currency": "CNY"})
    return result


def fetch_public(request_get, url):
    # 每个请求独立使用匿名会话；不共享或写回 auth.json。
    with requests.Session() as session:
        session.headers.update(HEADERS)
        response = request_get(session, "查询商店推荐", url, safe_retry=True, allow_redirects=False)
        if response.status_code != 200:
            raise ValueError(f"商品页查询失败：HTTP {response.status_code}")
        return response.text


def duo_status(html):
    selection, header = {}, {}
    for _, script in re.findall(r"<script\b([^>]*)>(.*?)</script>", html, re.S | re.I):
        if "window.PRODUCT_SELECTION_BOOTSTRAP" in script:
            match = re.search(r"\bproductSelectionData:\s*", script)
            if match:
                selection, _ = json.JSONDecoder().raw_decode(script[match.end():])
        match = re.search(r"window\.pageLevelData\.productHeader\s*=\s*", script)
        if match:
            header, _ = json.JSONDecoder().raw_decode(script[match.end():])
    metrics = {item["sku"]: item for item in series_skus(html)}
    items, seen = [], set()
    for product in selection.get("products", []):
        sku = product.get("partNumber", "").upper()
        if product.get("familyType") != "iphoneduo" or not PART.fullmatch(sku) or sku in seen:
            continue
        seen.add(sku)
        soon = product.get("comingSoon")
        # comingSoon=false 只表示不再标记为即将发售，不能证明加购或库存正常。
        blocked = soon is True
        items.append({**metrics.get(sku, {"sku": sku, "name": "iPhone Duo"}),
                      "coming_soon": soon if isinstance(soon, bool) else None,
                      "can_add_to_bag": False if blocked else None,
                      "purchase_status": "not_yet_open" if blocked else "unknown",
                      "reason": "官网将此 SKU 标记为即将发售，暂未开放加购。" if blocked else
                                "页面没有明确的加购许可；不能仅凭 comingSoon=false 或 SKU 存在判断可加购。",
                      "status_source": "PRODUCT_SELECTION_BOOTSTRAP.productSelectionData.products",
                      "stock_status": "not_queried"})
    if not items:
        raise ValueError("Duo 选购页未提供可解析的 SKU 状态；可能是页面结构变化或返回了错误页。")
    return {"success": True, "source": DUO_URL, "filter": "duo",
            "queried_at": datetime.now(timezone.utc).isoformat(),
            "sku_count": len(items), "partial": any(x["can_add_to_bag"] is None for x in items),
            "purchase_message": text(header.get("header")),
            "note": "只读检查官网加购限制，不实际加购。can_add_to_bag=false 表示明确未开放，null 表示无法确认；库存未查询。",
            "items": items}


def query_duo(request_get):
    return duo_status(fetch_public(request_get, DUO_URL))


def format_duo_table(result):
    rows = [["SKU", "型号 / 配置", "价格", "加购状态"]]
    for item in result.get("items", []):
        allowed = item.get("can_add_to_bag")
        status = "可加购" if allowed is True else "未开放" if allowed is False else "未知"
        price = item.get("price")
        amount = f"{float(price):,.2f} {item.get('currency', 'CNY')}" if price is not None else "未知"
        rows.append([item["sku"].removesuffix("/A"), item["name"], amount, status])

    def width(value):
        return sum(0 if unicodedata.combining(c) else
                   2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in value)

    widths = [max(width(row[i]) for row in rows) for i in range(4)]
    border = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    lines = [f"iPhone Duo · {len(rows) - 1} 个 SKU", result.get("purchase_message", ""), "", border]
    for index, row in enumerate(rows):
        lines.append("| " + " | ".join(value + " " * (w - width(value))
                                       for value, w in zip(row, widths)) + " |")
        if index == 0:
            lines.append(border)
    queried_at = result.get("queried_at")
    query_time = (datetime.fromisoformat(queried_at).astimezone(ZoneInfo("Asia/Shanghai"))
                  .strftime("%Y-%m-%d %H:%M:%S") if queried_at else "未知")
    lines.extend([border, "加购状态为官网只读判断；未知表示无法确认。库存未查询。",
                  f"查询时间：{query_time}（北京时间）"])
    return "\n".join(lines)


def query_store(request_get, phone_only=False):
    def fetch(url):
        return fetch_public(request_get, url)

    items = recommendations(fetch(STORE_URL))
    if phone_only:
        # 按选购路径筛选，避免把名称中含 iPhone 的保护壳等配件当作手机。
        items = [item for item in items if item["kind"] == "series"
                 and urlsplit(item["url"]).path.startswith("/shop/buy-iphone/")]

    def resolve(item):
        try:
            item["skus"] = series_skus(fetch(item["url"]))
            item["sku_status"] = "resolved" if item["skus"] else "unresolved"
            if not item["skus"]:
                item["error"] = "选购页未提供可解析的 SKU，需在页面选择配置。"
        except (requests.RequestException, ValueError, KeyError, TypeError):
            item["sku_status"] = "unresolved"
            item["error"] = "选购页请求或数据解析失败；保留推荐商品链接。"

    series = [item for item in items if item["kind"] == "series"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(resolve, series))
    return {"success": True, "source": STORE_URL, "filter": "phone" if phone_only else "all",
            "queried_at": datetime.now(timezone.utc).isoformat(),
            "count": len(items), "sku_count": len({s["sku"] for item in items for s in item["skus"]}),
            "partial": any(item["sku_status"] != "resolved" for item in items),
            "note": "仅收集官网推荐商品；系列下的 SKU 是可选配置。库存未查询，不代表有货。",
            "items": items}
