"""Apple 中国大陆直营店静态目录；实时可取状态仍由当前结账响应决定。"""
import json
from pathlib import Path

CATALOG = json.loads((Path(__file__).parent / 'data/apple-retail-cn.json').read_text(encoding='utf-8'))
STORES = {store['id']: store for store in CATALOG['stores']}


def city_name(value):
    return str(value).strip().removesuffix('市')


def validate_store_ids(city, store_ids):
    if (not isinstance(store_ids, list) or not store_ids or len(store_ids) > len(STORES)
            or any(not isinstance(s, str) for s in store_ids) or len(set(store_ids)) != len(store_ids)):
        raise ValueError('请按优先顺序选择至少一家门店，门店列表不能重复。')
    if any(s not in STORES or city_name(STORES[s]['city']) != city_name(city) for s in store_ids):
        raise ValueError('所选门店须来自所选城市的 Apple 中国大陆直营店列表。')
    return list(store_ids)
