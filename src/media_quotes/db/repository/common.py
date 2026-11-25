"""Small helpers shared by the database repository aggregates."""

from __future__ import annotations

import json
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5


def _jsonable(value: Any) -> Any:
    """Convert dates, UUIDs and models into JSONB-safe Python values."""
    return json.loads(json.dumps(value, default=str))


def _run_uuid(run_id: str) -> UUID:
    """Derive a stable UUID from the string ids used by the CLI."""
    return uuid5(NAMESPACE_URL, run_id)


def _inserted_count(result: object, attempted: int) -> int:
    """psycopg reports -1 for INSERT .. ON CONFLICT DO NOTHING; fall back to attempted."""
    rowcount = getattr(result, "rowcount", None)
    return rowcount if isinstance(rowcount, int) and rowcount >= 0 else attempted
