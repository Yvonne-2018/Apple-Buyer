"""Exercise the packaged executable without credentials, browsers or network calls."""
import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

root=Path(__file__).resolve().parents[1]
exe=Path(sys.argv[1]) if len(sys.argv)>1 else root/'src-tauri/resources/worker'/('apple-buyer-worker.exe' if sys.platform=='win32' else 'apple-buyer-worker')
with tempfile.TemporaryDirectory() as folder:
    account_id,task_id=str(uuid.uuid4()),str(uuid.uuid4())
    process=subprocess.Popen([str(exe),'--data-dir',folder,'--account-id',account_id,'--task-id',task_id],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
    try:
        process.stdin.write(json.dumps(dict(account_id=account_id,task_id=task_id,account='fixture@example.test',action='address_read',params={}))+'\n')
        process.stdin.flush()
        event=json.loads(process.stdout.readline())
        assert event['type']=='result', event
        assert event['result']=={'success':True,'config':{}}, event
        assert process.wait(timeout=20)==0, process.stderr.read()
        print('内置业务进程检查通过；无网络请求。')
    finally:
        if process.poll() is None:process.kill()
