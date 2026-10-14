import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
python = root / '.venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
if not python.exists():
    raise SystemExit('请先运行 python scripts/setup_worker.py。')
backend = 'keyring.backends.Windows' if sys.platform == 'win32' else 'keyring.backends.macOS'
subprocess.run([str(python), '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir',
    '--name', 'apple-buyer-worker', '--distpath', str(root/'worker/dist'), '--workpath', str(root/'worker/build'),
    '--specpath', str(root/'worker/build'), '--paths', str(root/'worker/legacy'),
    '--add-data', str(root/'worker/data') + ':data',
    '--collect-all', 'playwright', '--collect-data', 'tzdata', '--hidden-import', backend,
    '--hidden-import', 'srp._pysrp', '--hidden-import', 'qrcode.image.pure',
    str(root/'worker/main.py')], cwd=root, check=True)
destination = root/'src-tauri/resources/worker'
if destination.exists():
    shutil.rmtree(destination)
shutil.copytree(root/'worker/dist/apple-buyer-worker', destination)
print('已内置 Python 与依赖：', destination)
