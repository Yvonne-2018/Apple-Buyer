"""Offline integration test against the packaged HTTP server and actual bundled worker."""
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


def launch(package, data):
    executable = package / ('apple-buyer-web.exe' if os.name == 'nt' else 'apple-buyer-web')
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
    process = subprocess.Popen([str(executable), '--no-open', '--port', '0', '--data-dir', str(data)],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               encoding='utf-8', creationflags=flags)
    lines = queue.Queue()
    def collect():
        for line in process.stdout:
            lines.put(line)
    threading.Thread(target=collect, daemon=True).start()
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            line = lines.get(timeout=15)
            match = re.search(r'(http://127\.0\.0\.1:\d+)/#access=([a-f0-9]+)', line)
            if match:
                return process, match.group(1), match.group(2)
            if process.poll() is not None:
                break
        raise AssertionError('本地 Web 未启动')
    except BaseException:
        stop(process)
        raise


def stop(process):
    if process.poll() is None:
        process.send_signal(signal.CTRL_BREAK_EVENT if os.name == 'nt' else signal.SIGTERM)
        try:
            process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main(package):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with tempfile.TemporaryDirectory(prefix='apple-buyer-web-check-') as directory:
        data = Path(directory)
        process, origin, token = launch(package, data)
        try:
            def request(path, body=None, headers=None, expected=200):
                h = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', 'Origin': origin}
                h.update(headers or {})
                req = urllib.request.Request(origin + path, data=None if body is None else json.dumps(body).encode(), headers=h)
                try:
                    response = opener.open(req, timeout=10)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    raw = response.read()
                    assert response.status == expected, (path, response.status, expected)
                    if response.headers.get('Content-Type', '').startswith('application/json'):
                        return json.loads(raw)
                    return raw

            def call(command, **args):
                return request('/api/' + command, args)

            def wait(task_id):
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    task = next(t for t in call('snapshot')['tasks'] if t['id'] == task_id)
                    if task['status'] in ('succeeded', 'failed', 'cancelled'):
                        assert task['status'] == 'succeeded', task['stage']
                        return task
                    time.sleep(.1)
                raise AssertionError('业务任务未完成')

            assert b'<html' in request('/')
            request('/api/snapshot', {}, {'Authorization': ''}, 401)
            request('/api/add_account', {'email': 'blocked@example.test'}, {'Origin': 'https://foreign.test'}, 403)
            request('/api/snapshot', {}, {'Host': 'rebind.test'}, 403)
            request('/worker/apple-buyer-worker', expected=404)
            request('/../accounts.json', expected=404)
            assert call('snapshot')['accounts'] == []
            a = call('add_account', name='测试 A', email='a@example.test')
            b = call('add_account', name='测试 B', email='b@example.test')
            assert a['pending'] is True and b['pending'] is True
            request('/api/start_task', {'accountId': a['id'], 'action': 'purchase', 'params': {}}, expected=400)
            call('save_preset', accountId=a['id'], sku='MLH74FE', quantity=3)
            request('/api/add_account', {'email': a['email']}, expected=400)
            request('/api/start_task', {'accountId': a['id'], 'action': 'unknown', 'params': {}}, expected=400)

            # Connect before task creation; terminal events must arrive without page polling.
            observed = queue.Queue()
            events = opener.open(urllib.request.Request(origin + '/api/events', headers={'Authorization': 'Bearer ' + token}), timeout=30)
            def collect_events():
                try:
                    while line := events.readline():
                        if line.startswith(b'data:'):
                            observed.put(json.loads(line[5:]))
                except (OSError, ValueError) as error:
                    observed.put({'status': 'stream_error', 'message': type(error).__name__})
            threading.Thread(target=collect_events, daemon=True).start()

            address = {'contact': {'lastName': '测试', 'firstName': '用户', 'emailAddress': a['email'], 'fullDaytimePhone': '13800000000'},
                       'delivery': {'state': '四川', 'city': '成都', 'district': '锦江'}, 'address': {'street': '离线测试地址'}}
            first = call('start_task', accountId=a['id'], action='address_save', params=address)
            second = call('start_task', accountId=b['id'], action='address_read', params={})
            assert wait(first)['result']['config']['address'] == address['address']
            assert wait(second)['result']['config'] == {}
            seen = set()
            deadline = time.monotonic() + 10
            while len(seen) < 2 and time.monotonic() < deadline:
                event = observed.get(timeout=10)
                assert event['status'] != 'stream_error', event
                if event['status'] == 'succeeded':
                    seen.add(event['id'])
            assert {first, second} <= seen
            assert not (data / 'accounts' / b['id'] / 'checkout.json').exists()
            # Validate the single-workspace lock without sharing live application state.
            duplicate = subprocess.run([str(package / ('apple-buyer-web.exe' if os.name == 'nt' else 'apple-buyer-web')),
                                        '--no-open', '--port', '0', '--data-dir', str(data)], capture_output=True, timeout=10)
            assert duplicate.returncode != 0
            stop(process)
            assert process.returncode == 0, '退出时未正常清理'
            events.close()
            process, origin, token = launch(package, data)
            restored = call('snapshot')
            assert len(restored['accounts']) == 2
            assert all(account['pending'] for account in restored['accounts'])
            assert restored['accounts'][0]['preset_quantity'] == 3
            assert any(t['id'] == first for t in restored['tasks'])
            # Remove only synthetic accounts after restart (no worker still closing).
            call('remove_account', accountId=a['id'])
            remaining = call('snapshot')
            assert [row['id'] for row in remaining['accounts']] == [b['id']]
            assert not any(row['account_id'] == a['id'] for row in remaining['tasks'])
            assert not (data / 'accounts' / a['id']).exists()
            assert (data / 'accounts' / b['id']).exists()
            request('/api/remove_account', {'accountId': '../outside'}, expected=400)
            stop(process)
            process, origin, token = launch(package, data)
            assert [row['id'] for row in call('snapshot')['accounts']] == [b['id']]
            call('remove_account', accountId=b['id'])
            assert call('snapshot')['accounts'] == []
            print('本地 Web 检查通过：访问隔离、真实 worker、账号隔离、实时事件、单实例、移除账号与重启持久化。未请求 Apple。')
        finally:
            stop(process)


if __name__ == '__main__':
    main(Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / 'local-web-release')
