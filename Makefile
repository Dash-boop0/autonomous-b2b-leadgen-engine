# Common tasks. `make dev` is the one-command local run (no Docker).
.PHONY: setup dev seed reset-db test test-backend test-frontend lint ci docker-up docker-down e2e-postgres

PY := backend/.venv/bin/python

setup:
	cd backend && ([ -d .venv ] || uv venv --python 3.12 .venv) && uv pip install -q --python .venv/bin/python -r requirements.txt "pytest>=8,<9" "ruff>=0.6"
	cd frontend && npm ci --no-audit --no-fund

dev:
	./scripts/dev.sh

seed:
	cd backend && .venv/bin/python -m app.seed

reset-db:
	rm -f backend/data/app.db
	cd backend && .venv/bin/python -m app.seed --profile $${SEED_PROFILE:-basic}

test: test-backend test-frontend

test-backend:
	cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pytest

test-frontend:
	cd frontend && npm run typecheck && npm run lint -- --max-warnings=0 && npm test && npm run build

lint:
	cd backend && .venv/bin/ruff check .
	cd frontend && npm run lint

ci: test

docker-up:
	docker compose up --build

docker-down:
	docker compose down -v --rmi local

# Runs the API e2e test through the Vercel entry point against the Compose Postgres (published on 127.0.0.1:5442).
e2e-postgres:
	docker compose up -d db
	cd backend && TEST_APP_ENTRY=api.index TEST_DATABASE_URL=postgresql://leadgen:leadgen@127.0.0.1:$${POSTGRES_HOST_PORT:-5442}/leadgen .venv/bin/pytest tests/test_e2e_api.py tests/test_acceptance.py
