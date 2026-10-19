"""Application settings. Everything is read from environment variables (see .env.example); nothing secret is hardcoded."""

import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SAMPLES_DIR = FIXTURES_DIR / "samples"

IS_SERVERLESS = bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


def _default_db_url() -> str:
    # On serverless the only writable path is /tmp. A real deployment must set DATABASE_URL (Postgres).
    if IS_SERVERLESS:
        return "sqlite:////tmp/leadgen.db"
    return f"sqlite:///{BACKEND_DIR / 'data' / 'app.db'}"


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def normalise_db_url(url: str) -> str:
    """Accept postgres:// and postgresql:// URLs (Neon/Vercel style), pin the psycopg v3 driver and append
    sslmode=require when absent. Local hosts and single-label Docker service names (e.g. ``db``) are left without
    SSL so the Compose stack keeps working."""
    url = url.strip()
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg://" + url[len(prefix) :]
    if url.startswith("postgresql") and "sslmode=" not in url:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if host and host not in _LOCAL_HOSTS and "." in host:
            query = parse_qsl(parts.query, keep_blank_values=True) + [("sslmode", "require")]
            url = urlunsplit(parts._replace(query=urlencode(query)))
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    app_mode: str = "demo"  # demo: synthetic data, mock providers, "Reset demo" enabled. No other mode sends anything.
    database_url: str = _default_db_url()
    # Demo-only signing key for session tokens. Override in any shared deployment.
    secret_key: str = "dev-only-insecure-key-change-me"
    token_ttl_minutes: int = 720
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3100,http://127.0.0.1:3100"
    cors_origin_regex: str | None = None  # e.g. https://.*\.vercel\.app

    # Providers. "mock" is the default and the only one exercised by the test suite.
    llm_provider: str = "mock"  # mock | ollama | anthropic
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5-5"
    dns_provider: str = "mock"  # mock | dnspython (optional extra)
    email_verifier: str = "mock"
    email_sender: str = "dry_run"  # only dry_run is implemented: writes to the outbox table, never sends

    # Workflow engine: request-driven ticks with a wall-clock budget (serverless friendly).
    tick_budget_seconds: float = 7.0
    run_lease_seconds: float = 30.0
    retry_base_seconds: float = 2.0

    # Bootstrap on first request: run migrations and seed if the DB is empty (idempotent, advisory-locked on Postgres).
    auto_bootstrap: bool = True
    seed_profile: str = "basic"  # basic | full

    @property
    def db_url(self) -> str:
        return normalise_db_url(self.database_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()
