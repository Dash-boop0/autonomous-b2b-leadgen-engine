"""FastAPI application. Stateless per request (serverless-friendly): no background threads are needed for
correctness; workflows advance through request-driven ticks."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import approvals, bootstrap
from .adapters.sources import AdapterError
from .config import get_settings
from .routers import campaigns, core, data, flows
from .system import EmergencyStopActive

logging.basicConfig(level=logging.INFO)


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="Autonomous B2B Lead-gen Engine",
        version="0.1.0",
        description="Dry-run, mock-provider lead-generation engine with synthetic data.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in s.cors_origins.split(",") if o.strip()],
        allow_origin_regex=s.cors_origin_regex,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _bootstrap(request: Request, call_next):
        if request.method != "OPTIONS" and request.url.path not in ("/health", "/api/health"):
            try:
                bootstrap.ensure_ready()
            except Exception as e:  # noqa: BLE001
                logging.exception("bootstrap failed")
                return JSONResponse({"detail": f"Database not ready: {e.__class__.__name__}"}, status_code=503)
        return await call_next(request)

    @app.exception_handler(approvals.ApprovalError)
    async def _apr(_: Request, e: approvals.ApprovalError):
        return JSONResponse({"detail": str(e)}, status_code=403)

    @app.exception_handler(EmergencyStopActive)
    async def _stop(_: Request, e: EmergencyStopActive):
        return JSONResponse({"detail": str(e)}, status_code=423)

    @app.exception_handler(LookupError)
    async def _nf(_: Request, e: LookupError):
        return JSONResponse({"detail": str(e)}, status_code=404)

    @app.exception_handler(AdapterError)
    async def _adapter(_: Request, e: AdapterError):
        return JSONResponse({"detail": str(e)}, status_code=400)

    @app.exception_handler(PermissionError)
    async def _perm(_: Request, e: PermissionError):
        return JSONResponse({"detail": str(e)}, status_code=403)

    for r in (core.router, data.router, flows.router, campaigns.router):
        app.include_router(r)
    return app


app = create_app()
