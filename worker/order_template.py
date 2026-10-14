"""Validated purchase choices. Session IDs, signed slots and credentials never live in a template."""
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
import re
from zoneinfo import ZoneInfo


def money(value, *, positive=False):
    if not isinstance(value, str) or not re.fullmatch(r'\d{1,9}(?:\.\d{1,2})?', value.strip()):
        raise ValueError('金额须为不超过两位小数的人民币数值。')
    result = Decimal(value.strip())
    if positive and result <= 0:
        raise ValueError('订单总额上限须大于零。')
    return result


def sku(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Z0-9]{7}(?:/A)?', value.strip().upper()):
        raise ValueError('SKU 格式不正确。')
    value = value.strip().upper()
    return value if value.endswith('/A') else value + '/A'


def compact(value):
    return re.sub(r'\s+', '', str(value)).casefold()


def validate_template(raw):
    if not isinstance(raw, dict) or raw.get('schema_version') != 1:
        raise ValueError('下单模板版本不兼容。')
    data = deepcopy(raw)
    errors = []
    for group in ('contact', 'delivery', 'address', 'shipping', 'pickup', 'invoice'):
        if not isinstance(data.get(group, {}), dict):
            errors.append(group + ' 配置格式')
        data[group] = data.get(group) if isinstance(data.get(group), dict) else {}
    def required(group, key, label, pattern=None):
        source = data.get(group, {}) if group else data
        value = source.get(key, '') if isinstance(source, dict) else ''
        if not isinstance(value, str) or not value.strip() or len(value) > 500 or (pattern and not re.fullmatch(pattern, value.strip())):
            errors.append(label)
            return ''
        source[key] = value.strip()
        return source[key]
    required(None, 'name', '模板名称')
    try:
        data['sku'] = sku(data.get('sku'))
    except ValueError:
        errors.append('商品 SKU')
    if type(data.get('quantity')) is not int or not 1 <= data['quantity'] <= 999:
        errors.append('购买数量（1–999）')
    try:
        money(data.get('max_total'), positive=True)
    except ValueError:
        errors.append('订单总额上限')
    for key, label, pattern in (
        ('lastName', '姓氏', None), ('firstName', '名字', None),
        ('emailAddress', '通知邮箱', r'[^\s@*]+@[^\s@]+\.[^\s@]+'),
        ('fullDaytimePhone', '手机号码', r'\d{11,12}'),
    ):
        required('contact', key, label, pattern)
    required('invoice', 'header', '个人电子发票抬头')
    if type(data.get('wait_for_release')) is not bool:
        errors.append('等待到货开关')
    alternatives = data.get('fallback_skus', [])
    try:
        if not isinstance(alternatives, list) or len(alternatives) > 30:
            raise ValueError()
        data['fallback_skus'] = list(dict.fromkeys(sku(item) for item in alternatives))
    except ValueError:
        errors.append('替代 SKU 列表')
    if data.get('mode') == 'HOME':
        if alternatives:
            errors.append('送货模板暂不支持自动替换 SKU')
        # Fulfillment needs the region before Apple exposes saved address choices.
        for key, label in (('state', '省份'), ('city', '城市'), ('district', '区县')):
            required('delivery', key, label)
        if data.get('address_source') == 'local':
            required('address', 'street', '详细地址')
        elif data.get('address_source') == 'saved':
            required(None, 'saved_address', '官网地址的完整地区与街道')
        else:
            errors.append('地址来源')
        shipping = data.get('shipping', {})
        if not isinstance(shipping, dict) or shipping.get('strategy') not in ('cheapest', 'earliest', 'specified'):
            errors.append('配送服务规则')
        elif shipping['strategy'] == 'specified':
            required('shipping', 'service', '指定配送服务名称或代码')
        try:
            money(shipping.get('max_fee'))
        except (ValueError, AttributeError):
            errors.append('运费上限')
    elif data.get('mode') == 'RETAIL':
        required('pickup', 'city', '提货城市')
        if data['pickup'].get('store_ids'):
            from retail_stores import validate_store_ids
            try:
                data['pickup']['store_ids'] = validate_store_ids(data['pickup'].get('city', ''), data['pickup']['store_ids'])
                data['pickup']['store'] = data['pickup']['store_ids'][0]
            except ValueError as error:
                errors.append(str(error))
        elif data['pickup'].get('store_ids', []) != []:
            errors.append('门店列表格式')
        else:
            required('pickup', 'store', '指定门店')  # 兼容既有单门店模板。
        suffix = data['contact'].get('nationalIdLast4', '')
        if suffix and (not isinstance(suffix, str) or not re.fullmatch(r'[A-Za-z0-9]{4}', suffix.strip())):
            errors.append('提货证件后四位')
        pickup = data.get('pickup', {})
        if pickup.get('time_mode') == 'window':
            try:
                start, end = (date.fromisoformat(pickup.get(key, '')) for key in ('date_from', 'date_to'))
                times = [pickup.get(key, '') for key in ('time_from', 'time_to')]
                if (start > end or any(not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', t) for t in times)
                        or times[0] >= times[1]):
                    raise ValueError()
            except (ValueError, TypeError):
                errors.append('取货日期和时间范围')
        elif pickup.get('time_mode') != 'earliest':
            errors.append('取货时间规则')
    else:
        errors.append('收货方式')
    if errors:
        raise ValueError('模板需补齐或修正：' + '、'.join(errors) + '。未开始下单。')
    return data


def shipping_fee(option):
    value = option.get('shippingCost')
    if isinstance(value, bool):
        raise ValueError('配送服务运费无法识别，未自动选择。')
    if isinstance(value, (int, float)):
        value = str(value)
    if isinstance(value, str):
        value = value.strip()
        if value in ('免费', '免费送货', 'Free', 'FREE'):
            return Decimal(0)
        value = re.sub(r'^(?:RMB|CNY|人民币|¥|￥)\s*', '', value).removesuffix('元').strip().replace(',', '')
        try:
            return money(value)
        except ValueError:
            pass
    raise ValueError('配送服务运费无法识别，请使用官网当前可核验的服务。')


def delivery_rank(option):
    quote = str(option.get('deliveryQuote', ''))
    today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
    dates = re.findall(r'\d{4}-\d{2}-\d{2}', quote)
    if dates:
        day = max(date.fromisoformat(item) for item in dates)
    else:
        matches = re.findall(r'(\d{1,2})月(\d{1,2})日', quote)
        if matches:
            values = [date(today.year, int(m), int(d)) for m, d in matches]
            values = [v.replace(year=v.year + 1) if v < today else v for v in values]
            day = max(values)
        else:
            relative = [offset for label, offset in (('今天', 0), ('明天', 1), ('后天', 2)) if label in quote]
            if not relative:
                raise ValueError('官网送达日期无法比较，请将模板改为指定服务或最低运费。')
            day = today + timedelta(days=max(relative))
    times = re.findall(r'((?:[01]?\d|2[0-3]):[0-5]\d)', str(option.get('deliveryTimeSlot', '')))
    return day, max(t.zfill(5) for t in times) if times else '23:59'


class TemplatePolicy:
    def __init__(self, raw):
        self.data = validate_template(raw)
        self.max_total = money(self.data['max_total'], positive=True)
        self.shipping_fee = Decimal(0)

    def preferences(self):
        d = self.data
        return {'mode': d['mode'], 'city': d['pickup'].get('city', '') if d['mode'] == 'RETAIL' else '',
                'store': d['pickup'].get('store', '') if d['mode'] == 'RETAIL' else '',
                'store_ids': d['pickup'].get('store_ids', []) if d['mode'] == 'RETAIL' else [],
                'auto_submit': True, 'wait_for_release': d['wait_for_release'],
                'fallback_configurations': bool(d['fallback_skus'])}

    def shipping_option(self, options):
        rule = self.data['shipping']
        rows = [o for o in options if o.get('value') and not o.get('disabled')]
        if rule['strategy'] == 'specified':
            wanted = compact(rule['service'])
            rows = [o for o in rows if wanted in (compact(o['value']), compact(o.get('label', '')), compact(o.get('title', '')))]
            if len(rows) != 1:
                raise ValueError('模板指定配送服务未唯一匹配官网当前选项。')
        priced = [(shipping_fee(o), o) for o in rows]
        priced = [(fee, o) for fee, o in priced if self.shipping_fee + fee <= money(rule['max_fee'])]
        if not priced:
            raise ValueError('当前没有符合模板运费上限的配送服务。')
        if rule['strategy'] == 'earliest':
            fee, selected = min(priced, key=lambda row: (delivery_rank(row[1]), row[0], str(row[1]['value'])))
        else:
            fee, selected = min(priced, key=lambda row: (row[0], str(row[1]['value'])))
        self.shipping_fee += fee
        return selected['value']

    def filter_candidates(self, candidates):
        allowed = [self.data['sku'], *self.data['fallback_skus']]
        rank = {part: i for i, part in reversed(list(enumerate(allowed)))}
        return sorted([r for r in candidates if r['sku'] in rank
                       and Decimal(r['price']) * self.data['quantity'] <= self.max_total],
                      key=lambda r: rank[r['sku']])

    def allows_slot(self, day, slot):
        rule = self.data['pickup']
        if rule['time_mode'] == 'earliest':
            return True
        match = re.fullmatch(r'\d{1,2}-(\d{1,2}:\d{2})-(\d{1,2}:\d{2})', slot['timeSlotValue'])
        if not match:
            raise ValueError('官网取货时段无法与模板时间范围比较。')
        start, end = (value.zfill(5) for value in match.groups())
        return rule['date_from'] <= day['date'] <= rule['date_to'] and rule['time_from'] <= start < end <= rule['time_to']

    def verify_fulfillment(self, flow):
        if getattr(flow, 'template_mode', None) != self.data['mode']:
            raise ValueError('收货方式未通过模板核验，未提交订单。')
        if self.data['mode'] == 'HOME':
            street = getattr(flow, 'template_address', '')
            if not street:
                raise ValueError('收货地址尚未通过模板核验，未提交订单。')
            from checkout_options import value
            actual = value(flow.model, 'checkout.companionBar.orderDetails.shippingCompanionBar.address.d.street', '')
            if actual and compact(actual) != compact(street):
                raise ValueError('订单复核地址与模板不一致，未提交订单。')
