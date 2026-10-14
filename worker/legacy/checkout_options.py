"""根据结账页面实时模型选择配送、自提与收货地址。"""
import json
import os
import re
import sys
import tempfile

import requests
import interaction
import release_wait


def value(node, path, default=None):
    for key in path.split('.'):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def store_key(name):
    """Match the retail page's optional Apple prefix without changing the store identity."""
    name = re.sub(r'^apple\s+', '', str(name).strip(), flags=re.IGNORECASE)
    return re.sub(r'\s+', '', name).casefold()


def read_config(path):
    config = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if not isinstance(config, dict):
        raise ValueError('checkout.json 必须是 JSON 对象。')
    return config


def template_policy():
    return getattr(interaction.handler, 'template_policy', None)


def save_config(path, config):
    fd, name = tempfile.mkstemp(prefix='.checkout-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            json.dump(config, file, ensure_ascii=False, indent=2)
            file.write('\n')
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def ask(label, default='', pattern=None):
    if interaction.handler is not None:
        while True:
            answer = interaction.handler.text(label, default=default).strip()
            if answer and (pattern is None or re.fullmatch(pattern, answer)):
                return answer
            interaction.report_stage('输入为空或格式不正确，请重新输入')
            default = ''
    while True:
        if default and sys.stdin.isatty() and sys.stdout.isatty():
            from prompt_toolkit import prompt
            answer = prompt('👉 ' + label + '：', default=default).strip()
        else:
            answer = input(f'👉 {label}' + (f' [{default}]' if default else '') + '：').strip() or default
        if answer and (pattern is None or re.fullmatch(pattern, answer)):
            return answer
        default = ''  # 用户清空预填内容后，不再偷偷恢复旧默认值。
        print('输入为空或格式不正确，请重新输入。')


def choose(label, options, default=None):
    if not options:
        raise ValueError(label + '：没有可选项，已停止。')
    if interaction.handler is not None:
        selected = interaction.handler.choose(label, options, default)
        if selected not in [key for key, _ in options]:
            raise ValueError('所选选项已失效，请重新开始当前操作。')
        return selected
    if sys.stdin.isatty() and sys.stdout.isatty():
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.shortcuts import choice

        keys = KeyBindings()

        @keys.add('c-d')
        def cancel(event):
            event.app.exit(exception=EOFError())

        return choice('👉 ' + label + '（↑↓ 选择，回车确认，Ctrl+C/D 取消）',
                      options=options, default=default, key_bindings=keys)
    print('\n' + label)
    for index, (key, title) in enumerate(options, 1):
        print(f'{index}. {title}')
    default_index = next((i for i, (key, _) in enumerate(options, 1) if key == default), None)
    while True:
        answer = input('👉 请选择序号' + (f'（回车选 {default_index}）' if default_index else '') + '：').strip()
        if not answer and default_index:
            return options[default_index - 1][0]
        if answer.isdecimal() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1][0]
        print('请输入有效序号。')


def contact(config):
    saved = config.get('contact', {})
    result = {}
    for key, label, pattern in (
        ('lastName', '收件/取货人姓氏', None), ('firstName', '名字', None),
        ('emailAddress', '通知邮箱', r'[^\s@]+@[^\s@]+\.[^\s@]+'),
        ('fullDaytimePhone', '手机号码', r'\d{11,12}'),
    ):
        old = saved.get(key, '')
        result[key] = old if old and (pattern is None or re.fullmatch(pattern, old)) else ask(label, pattern=pattern)
    return result


def current_region(auth_path):
    """读取官网会话地区，不提交定位同意，不修改登录配置。"""
    from duo_killer import load_auth
    try:
        with requests.Session() as session:
            if auth_path.exists():
                load_auth(session, json.loads(auth_path.read_text(encoding='utf-8')))
            response = session.get('https://www.apple.com.cn/shop/address/location/update',
                                   timeout=(3, 5), allow_redirects=False)
            response.raise_for_status()
            data = response.json()
            content = value(data, 'body.content', {})
            if str(value(data, 'head.status')) != '200' or content.get('defaultLocation') is True:
                return {}
            address = content.get('address', {})
            return {key: address[key].strip() for key in ('state', 'city', 'district')
                    if isinstance(address.get(key), str) and address[key].strip()}
    except (requests.RequestException, OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def configure_address(path, auth_path=None):
    config = read_config(path)
    # 主动配置时每一项均可修改；结账时只补充缺项。
    old = config.get('contact', {})
    config['contact'] = {
        **old,
        'lastName': ask('收件/取货人姓氏', old.get('lastName', '')),
        'firstName': ask('名字', old.get('firstName', '')),
        'emailAddress': ask('通知邮箱', old.get('emailAddress', ''), r'[^\s@]+@[^\s@]+\.[^\s@]+'),
        'fullDaytimePhone': ask('手机号码', old.get('fullDaytimePhone', ''), r'\d{11,12}'),
    }
    saved_region = config.get('delivery', {})
    remote_region = current_region(auth_path or path.parent / 'auth.json')
    if remote_region.get('state'):
        old_region = remote_region
        print('默认地区来自 Apple 当前会话：' + ' / '.join(remote_region.values()) + '。可直接删除、修改；回车确认。')
    else:
        old_region = saved_region
        print('官网未返回当前地区，使用本地记录。' if old_region.get('state')
              else '官网未返回当前地区，请手动填写；可能尚未登录或没有地区记录。')
    defaults = dict(old_region)
    region = {}
    for key, label in (('state', '省/直辖市'), ('city', '城市'), ('district', '区/县')):
        region[key] = ask(label, defaults.get(key, ''))
        if region[key].rstrip('省市') != defaults.get(key, '').rstrip('省市'):
            for child in ('city', 'district') if key == 'state' else ('district',) if key == 'city' else ():
                defaults.pop(child, None)
    config['delivery'] = region
    same_region = all(region[k].rstrip('省市') == saved_region.get(k, '').rstrip('省市') for k in region)
    street_default = config.get('address', {}).get('street', '') if same_region else ''
    config['address'] = {'street': ask('详细地址（街道、楼栋、门牌）', street_default)}
    save_config(path, config)
    print(f'快捷地址已保存到 {path}，结账时可选择使用。')
    return {'success': True, 'config_file': str(path)}


def region_data(flow, path, wanted=None, *, prefer_current=False, pickup_search=False):
    wanted = wanted or {}
    if template_policy():
        prefer_current = False
    if prefer_current:
        current = flow.require(path)['d']
        if all(current.get(key) for key in ('state', 'city', 'district')):
            wanted = {key: current[key] for key in ('state', 'city', 'district')}
    selected = {}
    for key, label in (('state', '省份'), ('city', '城市'), ('district', '区县')):
        node = flow.require(path)
        data = node['d']
        options = [(o['value'], o.get('label', o['value'])) for o in data.get(key + 'Options', []) if o.get('value')]
        # Municipalities return a fixed city and districts, with no city dropdown.
        if key == 'city' and not options and data.get(key):
            options = [(data[key], data[key])]
        desired = wanted.get(key) or data.get(key)
        matches = list(dict.fromkeys(k for k, title in options if desired in (k, title)))
        if not matches and desired:
            # 优先精确匹配；当前省市范围内唯一的区/县名称才兼容后缀差异。
            suffix = r'[区县]$' if key == 'district' else r'[省市]$'
            normalized = re.sub(suffix, '', str(desired).strip())
            matches = list(dict.fromkeys(k for k, title in options
                                        if normalized in (re.sub(suffix, '', str(k).strip()),
                                                          re.sub(suffix, '', str(title).strip()))))
        if pickup_search and wanted.get(key) and len(matches) != 1:
            raise ValueError('指定提货' + label + '“' + str(wanted[key]) + '”无法匹配官网选项。')
        if template_policy() and not pickup_search and len(matches) != 1:
            raise ValueError('模板' + label + '无法唯一匹配官网选项，未继续结账。')
        if len(matches) == 1 and matches[0] != desired:
            print(f'{label}自动匹配：{desired} → {matches[0]}')
        elif (len(matches) != 1 and desired
              and not (pickup_search and key == 'district' and not wanted.get('district') and options)):
            print(f'配置中的{label}“{desired}”无法唯一匹配官网选项，请确认。')
        if (pickup_search and key == 'district' and not wanted.get('district')
                and len(matches) != 1 and options):
            chosen = options[0][0]
            interaction.report_stage('门店查询使用官网有效区县：' + str(chosen) + '；最终仍匹配指定门店')
        else:
            chosen = matches[0] if len(matches) == 1 else choose(label, options, data.get(key))
        selected[key] = chosen
        payload = {path + '.' + k: data.get(k, '') for k in ('state', 'city', 'district', 'provinceCityDistrict')}
        payload.update({path + '.' + k: v for k, v in selected.items()})
        payload[path + '.countryCode'] = 'CN'
        # Some checkout responses prefill the city after Selectstate but do not
        # populate its districts until Selectcity is submitted explicitly.
        load_pickup_districts = (pickup_search and key == 'city'
                                 and not data.get('districtOptions'))
        if (chosen != data.get(key) or load_pickup_districts) and 'Select' + key in node.get('a', {}):
            # 改省/市时不携带上一地区的下级选择。
            for child in ('city', 'district') if key == 'state' else ('district',) if key == 'city' else ():
                payload[path + '.' + child] = ''
            payload[path + '.provinceCityDistrict'] = ''
            flow.action(path, 'Select' + key, payload)
    return {**{path + '.' + key: val for key, val in selected.items()},
            path + '.countryCode': 'CN', path + '.provinceCityDistrict': ' '.join(
                v for i, v in enumerate(selected.values())
                if i == 0 or v != list(selected.values())[i - 1])}


def shipping_options(flow, groups_path):
    payload = {}
    for group in flow.require(groups_path).get('c', []):
        options_path = groups_path + '.' + group + '.shipmentOptionsGroups'
        for option in flow.require(options_path).get('c', []):
            path = options_path + '.' + option + '.shippingOptions'
            node = flow.require(path)['d']
            options = [(o['value'], o.get('label') or o.get('title') or
                        ' / '.join(str(o[k]) for k in ('deliveryQuote', 'deliveryTimeSlot', 'shippingCost') if o.get(k)) or o['value'])
                       for o in node.get('options', []) if o.get('value') and not o.get('disabled')]
            policy = template_policy()
            selected = policy.shipping_option(node.get('options', [])) if policy else (
                choose('配送服务', options, node.get('selectShippingOption')) if len(options) > 1 else options[0][0] if options else None)
            if selected is None:
                raise ValueError('当前商品没有可用配送服务。')
            payload[path + '.selectShippingOption'] = selected
    return payload


def invoice_data(flow, path):
    invoice = value(flow.model, path + '.eFapiaoSelector')
    if not invoice:
        return {}  # 混合自提的发票已在取货联系人页面填写。
    policy = template_policy()
    if value(invoice, 'd.selectFapiao') != 'e_personal_fdf' and not (policy and isinstance(invoice.get('ePersonalFapiao'), dict)):
        raise ValueError('当前仅支持个人电子发票，请先在官网选择。')
    return {path + '.eFapiaoSelector.selectFapiao': 'e_personal_fdf',
            path + '.eFapiaoSelector.ePersonalFapiao.invoiceHeader': policy.data['invoice']['header'] if policy else value(invoice, 'ePersonalFapiao.d.invoiceHeader', '')}


def verification(flow, path):
    node = value(flow.model, path + '.verificationModule.d', {})
    if node.get('authRequired') is True and not node.get('verificationToken'):
        raise ValueError('当前联系人需要手机验证，请先在官网完成验证；未继续结账。')
    return {path + '.verificationModule.verificationToken': node.get('verificationToken', '')} if node else {}


def pickup_contact(flow, config, config_path):
    path = 'checkout.pickupContact'
    address = path + '.selfPickupContact.selfContact.address'
    flow.require(address)
    payload = verification(flow, address)
    details = contact(config)
    payload.update({address + '.' + key: val for key, val in details.items()})
    identity_path = path + '.selfPickupContact.nationalIdSelf'
    identity = value(flow.model, identity_path + '.d', {})
    if identity.get('enabled') is True:
        last_four = config.get('contact', {}).get('nationalIdLast4') or identity.get('nationalIdSelf', '')
        if not isinstance(last_four, str) or not re.fullmatch(r'[A-Za-z0-9]{4}', last_four):
            last_four = ask('提货人身份证件号码后四位（无需完整号码）', pattern=r'[A-Za-z0-9]{4}')
        last_four = last_four.upper()
        payload[identity_path + '.nationalIdSelf'] = last_four
        details['nationalIdLast4'] = last_four
    config['contact'] = {**config.get('contact', {}), **details}
    save_config(config_path, config)
    payload.update(invoice_data(flow, path))
    actions = flow.require(path).get('a', {})
    action = next((a for a in ('continueFromPickupContactToShipping', 'continueFromPickupContactToBilling') if a in actions), None)
    if not action:
        raise ValueError('当前取货联系人页面没有支持的继续操作。')
    flow.action(path, action, payload)
    expected = 'Shipping' if action.endswith('Shipping') else 'Billing'
    if value(flow.model, 'checkout.d.page') != expected:
        raise ValueError('取货联系人未通过服务端校验，请检查姓名、手机及邮箱；未继续结账。')
    flow.require('checkout.shipping' if action.endswith('Shipping') else 'checkout.billing')


def home_delivery(flow, config):
    path = 'checkout.fulfillment'
    selector = path + '.fulfillmentOptions'
    payload = {selector + '.selectFulfillmentLocation': 'HOME'}
    if flow.require(selector)['d'].get('selectFulfillmentLocation') != 'HOME':
        flow.action(selector, 'selectFulfillmentLocationAction', payload)
    if flow.require(selector)['d'].get('selectFulfillmentLocation') != 'HOME':
        raise ValueError('服务端未确认切换为送货，已停止。')
    tab = path + '.deliveryTab.delivery'
    payload.update(region_data(flow, tab + '.deliveryLocation.address.provinceCityDistrictTabsForCheckout', config.get('delivery'), prefer_current=True))
    payload.update(shipping_options(flow, tab + '.shipmentGroups'))
    flow.action(path, 'continueFromFulfillmentToShipping', payload)
    flow.require('checkout.shipping')
    if template_policy():
        flow.template_mode = 'HOME'


def fulfillment(flow, config_path):
    config = read_config(config_path)
    preferences = getattr(interaction.handler, 'checkout_preferences', {}) or {}
    path = 'checkout.fulfillment'
    node = flow.require(path + '.fulfillmentOptions')['d']
    options = [(o['value'], o.get('title', o['value'])) for o in node.get('options', [])
               if o.get('value') in ('HOME', 'RETAIL') and not o.get('disabled')]
    preferred_mode = preferences.get('mode')
    if preferred_mode and preferred_mode not in [key for key, _ in options]:
        raise ValueError('当前订单不支持预设的收货方式，已停止。')
    mode = preferred_mode or choose('收货方式', options, 'HOME')
    if mode != node.get('selectFulfillmentLocation'):
        flow.action(path + '.fulfillmentOptions', 'selectFulfillmentLocationAction',
                    {path + '.fulfillmentOptions.selectFulfillmentLocation': mode})
    payload = {path + '.fulfillmentOptions.selectFulfillmentLocation': mode}
    if mode == 'HOME':
        home_delivery(flow, config)
        return
    locator = path + '.pickupTab.pickup.storeLocator'
    node = flow.require(locator)
    search = preferences.get('city') or ask('自提门店所在城市/地区', value(node, 'd.searchInput', '') or config.get('delivery', {}).get('city', ''))
    store_data = pickup_search_data(flow, locator, search, config.get('delivery', {}))
    ordered_ids = preferences.get('store_ids', [])
    if ordered_ids:
        from retail_stores import validate_store_ids
        ordered_ids = validate_store_ids(search, ordered_ids)
        store_data[locator + '.showAllStores'] = 'true'
        selected, time_data = ordered_pickup_selection(flow, locator, store_data, ordered_ids, preferences)
    else:
        selected, time_data = legacy_pickup_selection(flow, locator, store_data, preferences), None
    if selected == 'HOME':
        home_delivery(flow, config)
        return
    store_data[locator + '.selectStore'] = selected
    if time_data is None:
        time_data = select_pickup_store(flow, locator, store_data, selected)
    flow.verified_pickup_store = selected
    interaction.report_stage(f'已选中自提门店：{selected}，正在确认取货时间')
    payload.update(store_data)
    payload.update(time_data)
    forced = path + '.pickupTab.deliveryForced'
    if value(flow.model, forced + '.shipmentGroups'):
        if preferences.get('auto_submit'):
            raise ValueError('指定自提的订单包含必须配送的商品，自动下单已停止。')
        print('此订单包含必须配送的商品，继续配置送货地区和配送服务。')
        payload.update(region_data(flow, forced + '.deliveryLocation.address.provinceCityDistrictTabsForCheckout', config.get('delivery'), prefer_current=True))
        payload.update(shipping_options(flow, forced + '.shipmentGroups'))
    flow.action(path, 'continueFromFulfillmentToPickupContact', payload)
    if not isinstance(value(flow.model, 'checkout.pickupContact'), dict):
        raise ValueError('门店已选中，但 Apple 未接受取货安排，未进入联系人页；'
                         '请检查取货日期、时段是否仍可用。未提交订单。')
    flow.require('checkout.pickupContact')
    pickup_contact(flow, config, config_path)
    if template_policy():
        flow.template_mode = 'RETAIL'


def legacy_pickup_selection(flow, locator, store_data, preferences):
    path = 'checkout.fulfillment'
    wanted_store = preferences.get('store', '').strip()
    wanted_key = store_key(wanted_store)
    search_errors = 0
    store_attempts = 1 if template_policy() else 30
    for attempt in (release_wait.attempts(store_attempts) if wanted_store else range(3)):
        current_results = value(flow.model, locator + '.searchResults.d.retailStores', [])
        reuse = (attempt == 0 and wanted_store
                 and value(flow.model, locator + '.d.searchInput') == store_data[locator + '.searchInput']
                 and isinstance(current_results, list)
                 and len([s for s in current_results if (preferences.get('fallback_configurations') or not s.get('storeDisabled')) and wanted_key in
                          (store_key(s.get('storeId', '')), store_key(s.get('storeName', '')))]) == 1)
        if reuse:
            interaction.report_stage('复用本次结账返回的可选门店，跳过重复搜索')
        else:
            flow.action(locator, 'search', store_data)
        result = value(flow.model, locator + '.searchResults.d', {})
        stores = result.get('retailStores') if isinstance(result, dict) else None
        if not isinstance(stores, list):
            if value(flow.model, locator + '.d.isStoreSearchErrorPresent') is True:
                search_errors += 1
                if search_errors >= min(3, store_attempts) and not release_wait.enabled():
                    raise ValueError('Apple 连续未返回可选门店列表；已停止。'
                                     '当前商品可能无法在该地区取货，也可能是门店搜索失败。'
                                     '请以官网对该商品和门店显示的取货状态为准。')
                release_wait.pause(f'Apple 门店搜索返回错误，已重试 {search_errors} 次')
                continue
            raise ValueError('Apple 门店搜索响应缺少门店列表，可能是接口格式已变化；已停止。')
        named = [store for store in stores if wanted_key in
                 (store_key(store.get('storeId', '')), store_key(store.get('storeName', '')))] if wanted_store else []
        matches = [store for store in named if not store.get('storeDisabled')]
        if attempt == 0:
            print('门店查询地区：' + store_data[locator + '.searchInput'])
            print('Apple 返回门店：' + ('、'.join(
                f'{store.get("storeName", "未知")}（{store.get("storeId", "未知")}，'
                + ('当前不可取货' if store.get('storeDisabled') else '可选') + '）'
                for store in stores) or '无'))
        if (preferences.get('fallback_configurations') and len(named) == 1
                and named[0].get('storeDisabled') is True):
            from variant_fallback import PickupUnavailable
            raise PickupUnavailable(str(named[0]['storeId']))
        if not wanted_store or len(matches) == 1:
            break
        if release_wait.enabled() or attempt < store_attempts - 1:
            release_wait.pause(f'预设门店尚不可选，已刷新 {attempt + 1} 次')
    choices = []
    for store in stores:
        lines = value(store, 'availability.lineItemAvailability', [])
        status = '；'.join(str(line.get('partName', '商品')) + '：' + str(line.get('availabilityQuote', '未知')) for line in lines)
        title = str(store.get('storeName', store['storeId'])) + ' — ' + status
        if store.get('storeDisabled') is True:
            print('不可选：' + title)
        else:
            choices.append((store['storeId'], title))
    no_stores = not choices
    delivery_available = any(o.get('value') == 'HOME' and not o.get('disabled')
                             for o in flow.require(path + '.fulfillmentOptions')['d'].get('options', []))
    if delivery_available:
        choices.append(('HOME', '为我送货（不选门店，改为配送）'))
        if no_stores:
            print('当前没有可选自提门店，可以改为送货。')
    if wanted_store:
        if len(matches) != 1:
            if len(named) == 1 and named[0].get('storeDisabled'):
                raise ValueError(f'门店“{named[0].get("storeName", wanted_store)}”存在，但当前商品暂不可在该店自提。')
            if len(named) > 1:
                raise ValueError('预设门店名称对应多个门店，请填写具体门店编号。')
            available_names = '、'.join(str(store.get('storeName', '')) for store in stores[:8])
            raise ValueError(f'当前城市的门店搜索结果未找到“{wanted_store}”'
                             + (f'；返回的门店：{available_names}' if available_names else '；当前没有返回门店')
                             + '。请核对城市，或留空门店后在结账时选择。')
        selected = str(matches[0]['storeId'])
    else:
        selected = choose('选择自提门店或改为送货（取货状态由官网返回）', choices,
                          'HOME' if no_stores and delivery_available else None)
    return selected


class PickupSlotUnavailable(ValueError):
    pass


class PickupStoreUnavailable(ValueError):
    pass


def ordered_pickup_selection(flow, locator, store_data, store_ids, preferences):
    """按用户顺序核验实时选项；不将网络/格式错误视为门店无货。"""
    from variant_fallback import PickupUnavailable
    for attempt in release_wait.attempts(1):
        store_data[locator + '.selectStore'] = ''
        current = value(flow.model, locator + '.searchResults.d.retailStores', [])
        reuse = (attempt == 0 and value(flow.model, locator + '.d.searchInput') == store_data[locator + '.searchInput']
                 and value(flow.model, locator + '.d.isStoreSearchErrorPresent') is not True
                 and isinstance(current, list) and all(isinstance(s, dict) for s in current)
                 and all(any(s.get('storeId') == wanted and type(s.get('storeDisabled')) is bool for s in current) for wanted in store_ids))
        if reuse:
            interaction.report_stage('复用当前结账的所选城市门店状态，按配置顺序选店')
        else:
            flow.action(locator, 'search', store_data)
        result = value(flow.model, locator + '.searchResults.d', {})
        stores = result.get('retailStores') if isinstance(result, dict) else None
        if value(flow.model, locator + '.d.isStoreSearchErrorPresent') is True or not isinstance(stores, list):
            raise ValueError('Apple 门店搜索失败或缺少门店列表，未将接口异常当作无货。')
        if (any(not isinstance(s, dict) or not isinstance(s.get('storeId'), str) for s in stores)
                or len({s['storeId'] for s in stores}) != len(stores)):
            raise ValueError('Apple 门店搜索列表格式不完整或编号重复，已停止。')
        indexed = {s['storeId']: s for s in stores}
        # 先检查所有选中门店的状态，再开始任何选店操作。
        if any(type(indexed[s].get('storeDisabled')) is not bool for s in store_ids if s in indexed):
            raise ValueError('Apple 门店可选状态不明确，已停止。')
        for store_id in store_ids:
            store = indexed.get(store_id)
            if store is None or store['storeDisabled']:
                reason = '当前结果未提供该门店' if store is None else '当前商品不可取货'
                interaction.report_stage(f'门店 {store_id}：{reason}，尝试下一家所选门店')
                continue
            try:
                times = select_pickup_store(flow, locator, store_data, store_id)
            except (PickupSlotUnavailable, PickupStoreUnavailable):
                interaction.report_stage(f'门店 {store_id} 已不可选或没有符合规则的时段，尝试下一家所选门店')
                continue
            return store_id, times
        if preferences.get('fallback_configurations'):
            raise PickupUnavailable(store_ids[0], store_ids)
        if not release_wait.enabled():
            break
        release_wait.pause('所选门店均不可取货，等待后按原顺序重新查询')
    raise ValueError('所选门店均无可用取货安排，未提交订单。')


def select_pickup_store(flow, locator, store_data, selected):
    path = 'checkout.fulfillment'
    store_data[locator + '.selectStore'] = selected
    # Only reuse a selection and signed slots received in this checkout, never a disk cache.
    time_data = None
    slot_path = path + '.pickupTab.pickup.timeSlot'
    if (value(flow.model, locator + '.d.selectStore') == selected
            and value(flow.model, slot_path + '.d.storeId') == selected
            and value(flow.model, slot_path + '.d.enableTimeSlotWindows') is True):
        try:
            time_data = pickup_time_data(flow, selected)
        except ValueError:
            pass  # Refresh selection if its slot data is incomplete or no longer usable.
    if time_data is None:
        flow.action(locator, 'select', store_data)
    else:
        interaction.report_stage('官网已选中指定门店且返回有效时段，跳过重复选店请求')
    stores = value(flow.model, locator + '.searchResults.d.retailStores', [])
    if isinstance(stores, list) and any(isinstance(s, dict) and s.get('storeId') == selected and s.get('storeDisabled') is True for s in stores):
        raise PickupStoreUnavailable('官网在选店时返回该门店已不可选。')
    if value(flow.model, locator + '.d.selectStore') != selected:
        raise ValueError('服务端未确认所选自提门店，已停止。')
    return pickup_time_data(flow, selected) if time_data is None else time_data


def pickup_time_data(flow, store_id):
    path = 'checkout.fulfillment.pickupTab.pickup.timeSlot'
    node = value(flow.model, path, {})
    if value(node, 'd.enableTimeSlotWindows') is not True:
        if template_policy() and template_policy().data['pickup']['time_mode'] == 'window':
            raise PickupSlotUnavailable('官网当前未提供可核验的取货时段，无法满足模板时间范围。')
        return {}
    if str(value(node, 'd.storeId', '')) != store_id:
        raise ValueError('取货时段所属门店与所选门店不一致，已停止。')
    path += '.dateTimeSlots'
    data = flow.require(path).get('d', {})
    dates, windows = data.get('pickUpDates', []), data.get('timeSlotWindows', [])
    if not isinstance(dates, list) or not isinstance(windows, list):
        raise ValueError('Apple 取货时段格式无法识别，已停止。')
    available = []
    for day in dates:
        if not isinstance(day, dict):
            raise ValueError('Apple 取货日期格式无法识别，已停止。')
        day_id = str(day.get('dayOfMonth', ''))
        for group in windows:
            if not isinstance(group, dict) or not isinstance(group.get(day_id, []), list):
                raise ValueError('Apple 取货时段格式无法识别，已停止。')
            for slot in group.get(day_id, []):
                if (not isinstance(slot, dict) or (not slot.get('disabled') and slot.get('enabled') is not False
                        and not slot.get('isRestricted') and not all(slot.get(k) for k in ('timeSlotValue', 'SlotId', 'signKey')))):
                    raise ValueError('Apple 取货时段缺少签名或格式不完整，已停止。')
        slots = [slot for group in windows if isinstance(group, dict)
                 for slot in (group.get(day_id) or []) if isinstance(slot, dict)
                 and slot.get('timeSlotValue') and slot.get('SlotId') and slot.get('signKey')
                 and not slot.get('disabled') and slot.get('enabled') is not False
                 and not slot.get('isRestricted')
                 and (not template_policy() or template_policy().allows_slot(day, slot))]
        if day.get('date') and slots:
            available.append((day, slots))
    if not available:
        raise PickupSlotUnavailable('门店已选中，但当前没有可用取货签到时段；未提交订单，请稍后重试。')
    day, slots = min(available, key=lambda entry: entry[0]['date'])
    def start_time(slot):
        match = re.match(r'^\d{1,2}-(\d{1,2}):(\d{2})-', slot['timeSlotValue'])
        if not match:
            raise ValueError('Apple 返回的取货时段无法比较先后，已停止。')
        return tuple(map(int, match.groups()))
    slot = min(slots, key=start_time)
    selected_date, selected_slot = day['date'], slot['timeSlotValue']
    # 与官网日期/时段控件 onChange 写入的字段一致，签名值仅来自当前门店实时响应。
    fields = {'dayRadio': str(day['dayOfMonth']), 'date': day['date'],
              'timeSlotValue': slot['timeSlotValue'], 'timeSlotId': slot['SlotId'],
              'startTime': slot.get('checkInStart', ''), 'endTime': slot.get('checkInEnd', ''),
              'signKey': slot['signKey'], 'timeZone': slot.get('timeZone', ''),
              'displayStartTime': slot.get('displayStart', ''), 'displayEndTime': slot.get('displayEnd', ''),
              'timeSlotType': slot.get('timeSlotType') or '',
              'isRecommended': 'true' if slot.get('recommendationLabel') else 'false',
              'isRestricted': slot.get('isRestricted') or ''}
    interaction.report_stage(f'按取货规则选择时段：{selected_date} {slot.get("Label", selected_slot)}')
    return {path + '.' + key: val for key, val in fields.items()}


def region_name(name):
    return re.sub(r'[省市]$', '', str(name).strip())


def retail_city_states(document, city):
    """Read only store addresses from Apple's public retail directory."""
    match = re.search(r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', document, re.S)
    if not match:
        raise ValueError('官网门店目录缺少地区数据，请稍后重试。')
    states = set()
    def visit(node):
        if isinstance(node, dict):
            address = node.get('address')
            if (node.get('__typename') == 'RgdsStore' and isinstance(address, dict)
                    and region_name(address.get('city', '')) == region_name(city)
                    and address.get('stateName')):
                states.add(address['stateName'])
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)
    visit(json.loads(match[1]))
    return states


def pickup_state(flow, current, city):
    """Resolve the requested city, never reuse another city's province blindly."""
    options = current.get('stateOptions', [])
    direct = {o['value'] for o in options if o.get('value') and
              region_name(city) in (region_name(o['value']), region_name(o.get('label', '')))}
    if len(direct) == 1:
        return direct.pop()  # Municipalities such as Shanghai need no directory request.
    from retail_stores import STORES, city_name
    states = {s['state'] for s in STORES.values() if city_name(s['city']) == city_name(city)}
    matches = {o['value'] for o in options if o.get('value') and any(
        region_name(state) in (region_name(o['value']), region_name(o.get('label', ''))) for state in states)}
    if len(matches) == 1:
        return matches.pop()
    if current.get('state') and any(region_name(city) in
            (region_name(o.get('value', '')), region_name(o.get('label', '')))
            for o in current.get('cityOptions', []) if o.get('value')):
        return current['state']
    from duo_killer import request_get
    response = request_get(flow.session, '查询提货城市所属省份',
                           'https://www.apple.com.cn/retail/storelist/', safe_retry=True)
    response.raise_for_status()
    states = retail_city_states(response.text, city)
    matches = {o['value'] for o in options if o.get('value') and any(
        region_name(state) in (region_name(o['value']), region_name(o.get('label', '')))
        for state in states)}
    if len(matches) != 1:
        raise ValueError('无法从官网门店目录唯一确定提货城市“' + str(city) + '”所属省份，请检查城市名称。')
    return matches.pop()


def pickup_search_data(flow, locator, search, saved_region):
    """中国官网用完整省市区搜索，城市输入不能覆盖成单独的 searchInput。"""
    region = locator + '.address.stateCitySelectorForCheckout'
    current = value(flow.model, region + '.d')
    data = {locator + '.showAllStores': 'false', locator + '.selectStore': ''}
    if not isinstance(current, dict):
        data[locator + '.searchInput'] = search
        return data  # 兼容使用自由文本搜索、没有中国地区选择器的页面。
    normalize = lambda name: re.sub(r'[省市]$', '', str(name).strip())
    full_region = current.get('provinceCityDistrict') or ' '.join(
        str(current.get(k, '')) for k in ('state', 'city', 'district')).strip()
    complete = all(current.get(k) for k in ('state', 'city', 'district'))
    if complete and (normalize(search) == normalize(current.get('city', '')) or search == full_region):
        data.update({region + '.' + k: current.get(k, '')
                     for k in ('state', 'city', 'district', 'countryCode')})
        data[region + '.provinceCityDistrict'] = full_region
    else:
        # The purchase form specifies a pickup city and store, not a district.
        # A saved delivery district may be stale or absent from this account's
        # checkout options; use it only for delivery, never as a pickup constraint.
        same_saved_city = normalize(search) == normalize(saved_region.get('city', ''))
        wanted = {'state': saved_region.get('state', '')} if same_saved_city else {}
        wanted['city'] = search
        if not wanted.get('state'):
            wanted['state'] = pickup_state(flow, current, search)
        data.update(region_data(flow, region, wanted, pickup_search=True))
    # 官网 checkout.js 在地区选择结束后将 provinceCityDistrict 复制到 searchInput。
    data[locator + '.searchInput'] = data[region + '.provinceCityDistrict']
    return data


def should_submit_contact(node, key, proposed):
    rule = value(node, 'b.' + key + '.submit')
    if rule is True:
        return True
    if rule is None or rule is False:
        return False
    # Interpret only the exact changed-value rule observed in Apple's model.
    if rule == {'op': '!EQ', 'args': [{'val': '_'}, {'get': 'd.was.' + key}]}:
        return proposed != value(node, 'd.was.' + key, '')
    raise ValueError('官网联系人提交规则无法识别，已停止。')


def shipping(flow, config_path, auth_path=None):
    if value(flow.model, 'checkout.d.page') == 'Billing':
        flow.require('checkout.billing')
        return  # 纯自提没有收货地址步骤。
    path = 'checkout.shipping'
    node = flow.require(path)
    selector = node['addressSelector']['d']
    saved = [a for a in selector.get('radioAddresses', [])
             if a.get('isSavedAddress') and a.get('addressLine1') and a.get('value')
             and a['value'] != 'newAddr' and not a.get('disabled')]
    current = next((a for a in saved if a['value'] == selector.get('selectAddress')), None)
    defaults = [a for a in saved if a.get('isDefaultAddr') is True]
    if current is None and len(defaults) == 1:
        current = defaults[0]
    if current is None and len(saved) == 1:
        current = saved[0]
    can_add = any(a.get('value') == 'newAddr' and not a.get('disabled')
                  for a in selector.get('radioAddresses', []))
    # The current CN checkout renders the new-address form directly when there
    # are no saved addresses; radioAddresses is absent in that layout.
    if ('radioAddresses' not in selector and selector.get('selectAddress') == 'newAddr'
            and isinstance(value(node, 'addressSelector.newAddress.address'), dict)
            and value(node, 'addressSelector.newAddress.p.active') is not False
            and value(node, 'addressSelector.newAddress.p.visible') is not False):
        can_add = True
    policy = template_policy()
    if policy:
        if policy.data['address_source'] == 'local':
            if not can_add:
                raise ValueError('官网未提供模板地址填写入口，未改用其他地址。')
            selected = 'newAddr'
        else:
            from order_template import compact
            wanted = compact(policy.data['saved_address'])
            matches = [a for a in saved if compact(str(a.get('provinceCityDistrict', '')) + str(a['addressLine1'])) == wanted]
            if len(matches) != 1:
                raise ValueError('模板指定官网地址未唯一匹配，请核对完整地区与街道。')
            current = matches[0]
            selected = 'saved'
    elif saved:
        choices = [('saved', '使用官网已有的地址')]
        if can_add:
            choices.append(('newAddr', '使用本地快捷地址进行配置'))
        else:
            print('官网当前未提供可用的新增地址入口，只能使用已有地址。')
        selected = choose('选择送货地址来源', choices, 'saved')
    elif can_add:
        selected = 'newAddr'
        print('官网没有可用的已保存地址，使用本地快捷地址。')
    else:
        raise ValueError('官网没有可用的收货地址，也未提供新增地址入口，已停止。')
    if selected == 'saved':
        selected = current['value'] if current else choose('官网有多个地址且未指定默认，请选择',
                    [(a['value'], str(a.get('provinceCityDistrict', '')) + ' ' + str(a['addressLine1'])) for a in saved])
        current = next(a for a in saved if a['value'] == selected)
        print('使用官网已保存地址：' + str(current.get('provinceCityDistrict', '')) + ' ' + str(current['addressLine1']))
    payload = {path + '.addressSelector.selectAddress': selected}
    if selected == 'newAddr':
        config = read_config(config_path)
        if not config.get('address', {}).get('street') or not config.get('delivery'):
            if auth_path is None:
                configure_address(config_path)
            else:
                configure_address(config_path, auth_path)
            config = read_config(config_path)
        details = contact(config)
        config['contact'] = {**config.get('contact', {}), **details}
        save_config(config_path, config)
        address = path + '.addressSelector.newAddress.address'
        flow.require(address)
        payload.update(region_data(flow, address + '.stateCitySelectorForCheckout', config.get('delivery')))
        payload.update({address + '.' + k: details[k] for k in ('lastName', 'firstName')})
        payload.update({address + '.street': config['address']['street'], address + '.street2': config.get('address', {}).get('street2', ''),
                        address + '.copyContact.fullDaytimePhone': details['fullDaytimePhone'],
                        path + '.addressSelector.newAddress.saveToAddressBook': 'true'})
        email = details['emailAddress']
        print('收货地址：' + ' '.join(config['delivery'].values()) + ' ' + config['address']['street'])
    else:
        details = contact(read_config(config_path)) if policy else {}
        email = details.get('emailAddress') or value(flow.model, path + '.addressNotification.address.d.emailAddress', '')
    payload.update(invoice_data(flow, path))
    if value(flow.model, path + '.addressNotification.address') is not None:
        optional = value(flow.model, path + '.addressNotification.address.b.emailAddress.optional') is True
        payload[path + '.addressNotification.address.emailAddress'] = email if email or optional else ask('通知邮箱', pattern=r'[^\s@]+@[^\s@]+\.[^\s@]+')
    email_path = path + '.addressContactEmail.address'
    email_node = value(flow.model, email_path)
    if email_node:
        contact_email = details.get('emailAddress') or value(email_node, 'd.emailAddress', '')
        if should_submit_contact(email_node, 'emailAddress', contact_email):
            if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', contact_email) or '*' in contact_email:
                contact_email = ask('收货联系邮箱', pattern=r'[^\s@]+@[^\s@]+\.[^\s@]+')
            payload[email_path + '.emailAddress'] = contact_email
    phone_path = path + '.addressContactPhone.address'
    phone = value(flow.model, phone_path)
    if phone:
        payload.update(verification(flow, phone_path))
        number = (details.get('fullDaytimePhone') if selected == 'newAddr' or policy else value(phone, 'd.mobilePhone', '')) or ''
        if should_submit_contact(phone, 'mobilePhone', number):
            if not re.fullmatch(r'\d{11,12}', number):
                number = ask('收货手机号码', pattern=r'\d{11,12}')
            payload[phone_path + '.mobilePhone'] = number
    actions = flow.require(path).get('a', {})
    continuation = [name for name in ('continueFromShippingToBilling', 'continueFromShippingToUnknown') if name in actions]
    if len(continuation) != 1:
        raise ValueError('官网未提供唯一可识别的收货地址继续入口，已停止。')
    flow.action(path, continuation[0], payload)
    if value(flow.model, 'checkout.d.page') != 'Billing':
        raise ValueError('收货地址或联系方式未通过服务端校验，请检查后重试；未提交订单。')
    flow.require('checkout.billing')
    if policy:
        flow.template_address = (policy.data['address']['street'] if selected == 'newAddr' else current['addressLine1'])
