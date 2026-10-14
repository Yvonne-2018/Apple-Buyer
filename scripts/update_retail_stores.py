"""从 Apple 官方目录刷新中国大陆直营店选项（不包含库存或营业状态）。"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

SOURCE = 'https://www.apple.com.cn/retail/storelist/'
TARGET = Path(__file__).resolve().parents[1] / 'worker/data/apple-retail-cn.json'


def parse_directory(document):
    match = re.search(r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', document, re.S)
    if not match:
        raise ValueError('官网目录缺少结构化数据，未更新本地文件。')
    groups = json.loads(match[1])['props']['pageProps']['storeList']
    mainland = [g for g in groups if g.get('locale') == 'zh_CN']
    if len(mainland) != 1:
        raise ValueError('无法唯一识别中国大陆目录，未更新本地文件。')
    # 页面同时带有全球及重复的展示数据，只读取 zh_CN 的 state.store。
    stores = []
    for state in mainland[0]['state']:
        for row in state['store']:
            address = row['address']
            store = dict(id=row['id'], name=row['name'].strip(), city=address['city'].strip(),
                         state=address['stateName'].strip(),
                         address=' '.join(address[k].strip() for k in ('address1', 'address2') if address.get(k)),
                         url='https://www.apple.com.cn/retail/' + row['slug'] + '/')
            if (not re.fullmatch(r'R\d{3}', store['id']) or not all(store.values())
                    or store['state'] != state['name']):
                raise ValueError('门店数据不完整，未更新本地文件。')
            stores.append(store)
    if not stores or len({s['id'] for s in stores}) != len(stores) or len({s['url'] for s in stores}) != len(stores):
        raise ValueError('门店列表为空或有重复，未更新本地文件。')
    return stores


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--html', type=Path, help='使用已下载的官方目录 HTML')
    args = parser.parse_args()
    if args.html:
        document = args.html.read_text(encoding='utf-8')
    else:
        with urlopen(Request(SOURCE, headers={'User-Agent': 'Mozilla/5.0'}), timeout=30) as response:
            if response.url.rstrip('/') != SOURCE.rstrip('/'):
                raise ValueError('官网目录重定向至非预期地址，未更新。')
            document = response.read().decode('utf-8')
    stores = parse_directory(document)
    result = dict(schema_version=1, region='CN', scope='中国大陆', source_url=SOURCE,
                  retrieved_at=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat(), stores=stores)
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'已保存 {len(stores)} 家直营店，覆盖 {len({s["city"] for s in stores})} 个城市：{TARGET}')


if __name__ == '__main__':
    main()
