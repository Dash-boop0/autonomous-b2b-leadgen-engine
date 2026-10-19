#!/usr/bin/env bash
# One-command local run without Docker (macOS / Linux / WSL).
# Creates the Python 3.12 venv, installs deps, migrates + seeds SQLite, starts the API and the web app.
# Ports: API_PORT (default 8000, falls back to 8100 if busy), WEB_PORT (default 3000, falls back to 3100).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing dependency: $1 ($2)"; exit 1; }; }
need uv "https://docs.astral.sh/uv/"
need npm "Node.js 20+ (22 recommended)"

port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1; }
pick_port() { local want="$1" alt="$2"; if port_busy "$want"; then echo "$alt"; else echo "$want"; fi; }

API_PORT="${API_PORT:-$(pick_port 8000 8100)}"
WEB_PORT="${WEB_PORT:-$(pick_port 3000 3100)}"
if port_busy "$API_PORT"; then echo "Port $API_PORT is busy; set API_PORT to a free port."; exit 1; fi
if port_busy "$WEB_PORT"; then echo "Port $WEB_PORT is busy; set WEB_PORT to a free port."; exit 1; fi

echo "==> Backend setup (Python 3.12 via uv)"
cd "$ROOT/backend"
[ -d .venv ] || uv venv --python 3.12 .venv
uv pip install -q --python .venv/bin/python -r requirements.txt "pytest>=8,<9" "ruff>=0.6"
if [ "${RESET_DB:-0}" = "1" ]; then rm -f data/app.db; fi
.venv/bin/python -m app.seed --profile "${SEED_PROFILE:-basic}"

echo "==> Frontend setup"
cd "$ROOT/frontend"
[ -d node_modules ] || npm ci --no-audit --no-fund

export CORS_ORIGINS="http://localhost:${WEB_PORT},http://127.0.0.1:${WEB_PORT}"
export NEXT_PUBLIC_API_URL="http://localhost:${API_PORT}"

echo "==> Starting API on :${API_PORT} and web on :${WEB_PORT}"
cd "$ROOT/backend"
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT" &
API_PID=$!
cd "$ROOT/frontend"
WEB_PORT="$WEB_PORT" npm run dev &
WEB_PID=$!
trap 'kill $API_PID $WEB_PID 2>/dev/null || true' EXIT INT TERM
echo ""
echo "  Web:  http://localhost:${WEB_PORT}   (sign in as operator / demo)"
echo "  API:  http://localhost:${API_PORT}/docs"
wait
