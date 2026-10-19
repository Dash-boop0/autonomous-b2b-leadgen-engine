"""Vercel Python serverless entry point (ASGI). vercel.json rewrites every path here; FastAPI routes by the
original path (/health, /api/...). The first request runs migrations and seeds if the database is empty."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app  # noqa: E402

__all__ = ["app"]
