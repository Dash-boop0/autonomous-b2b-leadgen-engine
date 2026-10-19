# One-command local run on Windows PowerShell (no Docker). Requires uv and Node.js 20+.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
function Test-Port($p) { try { $c = New-Object Net.Sockets.TcpClient; $c.Connect("127.0.0.1", $p); $c.Close(); return $true } catch { return $false } }
$ApiPort = if ($env:API_PORT) { $env:API_PORT } elseif (Test-Port 8000) { 8100 } else { 8000 }
$WebPort = if ($env:WEB_PORT) { $env:WEB_PORT } elseif (Test-Port 3000) { 3100 } else { 3000 }

Set-Location "$Root\backend"
if (-not (Test-Path ".venv")) { uv venv --python 3.12 .venv }
uv pip install -q --python .venv\Scripts\python.exe -r requirements.txt "pytest>=8,<9" "ruff>=0.6"
.venv\Scripts\python.exe -m app.seed --profile basic

Set-Location "$Root\frontend"
if (-not (Test-Path "node_modules")) { npm ci --no-audit --no-fund }

$env:CORS_ORIGINS = "http://localhost:$WebPort,http://127.0.0.1:$WebPort"
$env:NEXT_PUBLIC_API_URL = "http://localhost:$ApiPort"
$env:WEB_PORT = "$WebPort"
$api = Start-Process -PassThru -NoNewWindow -WorkingDirectory "$Root\backend" ".venv\Scripts\uvicorn.exe" "app.main:app --host 127.0.0.1 --port $ApiPort"
$web = Start-Process -PassThru -NoNewWindow -WorkingDirectory "$Root\frontend" "npx.cmd" "next dev -p $WebPort"
Write-Host "Web: http://localhost:$WebPort  API: http://localhost:$ApiPort/docs  (Ctrl+C to stop)"
try { Wait-Process -Id $api.Id, $web.Id } finally { Stop-Process -Id $api.Id, $web.Id -ErrorAction SilentlyContinue }
