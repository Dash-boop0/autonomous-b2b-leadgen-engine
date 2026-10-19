from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import inspect


def to_dict(obj: Any, exclude: set[str] | None = None) -> dict:
    if obj is None:
        return {}
    exclude = exclude or set()
    out = {}
    for col in inspect(obj).mapper.column_attrs:
        if col.key in exclude:
            continue
        v = getattr(obj, col.key)
        out[col.key] = v.isoformat() if isinstance(v, datetime) else v
    return out
