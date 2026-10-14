#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if command -v python3 >/dev/null 2>&1; then PYTHON_RUN=python3
elif command -v python >/dev/null 2>&1; then PYTHON_RUN=python
else echo '开发启动需要 Python 3.10+；普通使用请打开打包好的 Apple-Buyer 应用。'; exit 1; fi
if ! command -v pnpm >/dev/null 2>&1; then echo '请通过 Corepack 启用 pnpm，再运行本脚本。'; exit 1; fi
"$PYTHON_RUN" scripts/setup_worker.py
pnpm install --frozen-lockfile
mkdir -p src-tauri/resources/worker
exec pnpm desktop
