$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$python = if (Get-Command python -ErrorAction SilentlyContinue) { 'python' } elseif (Get-Command python3 -ErrorAction SilentlyContinue) { 'python3' } else { throw '开发环境需要 Python 3.10+。普通用户请安装 Apple-Buyer 安装包。' }
if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) { throw '请通过 Corepack 启用 pnpm。' }
& $python scripts/setup_worker.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
pnpm install --frozen-lockfile
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
New-Item -ItemType Directory -Force src-tauri/resources/worker | Out-Null
pnpm desktop
exit $LASTEXITCODE
