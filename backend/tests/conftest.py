"""Test fixtures. Each test gets a fresh SQLite file DB (migrated with Alembic and seeded with the basic profile).
Set TEST_DATABASE_URL to a PostgreSQL URL to run the same tests against Postgres (tables are wiped first)."""

from __future__ import annotations

import importlib
import os

import pytest
from fastapi.testclient import TestClient

from app import bootstrap, db
from app.config import get_settings


def _configure(url: str, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("SEED_PROFILE", "basic")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("RETRY_BASE_SECONDS", "0")
    get_settings.cache_clear()
    db.configure(url)
    bootstrap.reset_flag()


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"
    _configure(url, monkeypatch)
    if url.startswith("postgres"):
        from app.seed import wipe

        bootstrap.migrate()
        s = db.session_factory()()
        wipe(s)
        s.close()
    bootstrap.ensure_ready()
    yield url
    db.engine().dispose()


@pytest.fixture()
def session(fresh_db):
    s = db.session_factory()()
    yield s
    s.close()


@pytest.fixture()
def client(fresh_db):
    entry = os.environ.get("TEST_APP_ENTRY", "app.main")  # "api.index" exercises the Vercel entry point
    mod = importlib.import_module(entry)
    with TestClient(mod.app) as c:
        yield c


class Api:
    def __init__(self, client: TestClient):
        self.c = client
        self.tokens: dict[str, str] = {}

    def h(self, user: str) -> dict:
        if user not in self.tokens:
            r = self.c.post("/api/auth/login", json={"username": user, "password": "demo"})
            assert r.status_code == 200, r.text
            self.tokens[user] = r.json()["token"]
        return {"Authorization": f"Bearer {self.tokens[user]}"}

    def get(self, path: str, user: str = "viewer", **kw):
        return self.c.get(path, headers=self.h(user), **kw)

    def post(self, path: str, user: str = "operator", **kw):
        return self.c.post(path, headers=self.h(user), **kw)

    def ok(self, r, code: int = 200):
        assert r.status_code == code, f"{r.status_code}: {r.text}"
        return r.json()

    # ---- workflow helpers
    def pending(self, action: str) -> dict:
        items = self.ok(self.get("/api/approvals?status=pending"))
        match = [a for a in items if a["action"] == action]
        assert match, f"no pending {action}: {items}"
        return match[0]

    def approve(self, action: str, user: str = "reviewer") -> dict:
        a = self.pending(action)
        return self.ok(
            self.post(
                f"/api/approvals/{a['id']}/decide", user=user, json={"decision": "approved", "reason": "looks right"}
            )
        )

    def active_icp(self) -> dict:
        return next(i for i in self.ok(self.get("/api/icp")) if i["status"] == "approved")

    def upload(self, kind: str) -> dict:
        from app.config import SAMPLES_DIR

        content = (SAMPLES_DIR / f"{kind}.csv").read_bytes()
        return self.ok(
            self.post("/api/sources/csv", data={"kind": kind}, files={"file": (f"{kind}.csv", content, "text/csv")})
        )

    def run(self, workflow: str, inp: dict, user: str = "operator") -> dict:
        return self.ok(self.post(f"/api/workflows/{workflow}/runs", user=user, json={"input": inp}))["run"]


@pytest.fixture()
def api(client) -> Api:
    return Api(client)


def build_pipeline(api: Api) -> dict:
    """ICP approval -> CSV + registry discovery -> contacts -> campaign approval -> queued. Returns ids."""
    api.approve("icp.activate")
    icp = api.active_icp()
    acc_up, con_up = api.upload("accounts"), api.upload("contacts")
    r1 = api.run(
        "icp_to_accounts",
        {"icp_version_id": icp["id"], "adapters": ["csv_upload", "mock_registry"], "csv_upload_id": acc_up["id"]},
    )
    assert r1["status"] == "completed", r1
    r2 = api.run(
        "account_to_contacts",
        {"icp_version_id": icp["id"], "adapters": ["csv_upload", "licensed_contacts"], "csv_upload_id": con_up["id"]},
    )
    assert r2["status"] == "completed", r2
    created = api.ok(
        api.post("/api/campaigns", json={"name": "Test campaign (synthetic)", "icp_version_id": icp["id"]})
    )
    assert created["run"]["status"] == "waiting_approval", created["run"]
    return {"icp": icp, "campaign_id": created["campaign"]["id"], "runs": [r1, r2, created["run"]]}
