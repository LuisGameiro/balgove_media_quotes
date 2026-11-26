"""Correlated offline run log inspection."""

from __future__ import annotations

import json
from typing import Any, cast

from ..config import Settings
from ..text_utils import require_safe_id
from .common import EXIT_OK


def _do_logs(settings: Settings, *, run_id: str, trace_id: str | None) -> int:  # pragma: no cover
    require_safe_id(run_id, "run_id")
    if settings.persist_to_postgres:
        from ..db.repository import read_audit_events
        from ..pipeline import default_session_context

        with default_session_context(settings) as session:
            events = read_audit_events(session, run_id, trace_id=trace_id)
        for event in events:
            details = event.get("details") or {}
            license_ref = (
                details.get("license_reference", "(none)")
                if isinstance(details, dict)
                else "(none)"
            )
            print(
                f"{event.get('step', 'unknown')} {event.get('status', 'unknown')} "
                f"source={event.get('entity_id') or event.get('entity_type') or '(none)'} "
                f"trace={event.get('trace_id', '(none)')} license_reference={license_ref}"
            )
        if not events:
            print(f"no audit events for run_id={run_id} trace_id={trace_id or '(all)'}")
        return EXIT_OK
    path = settings.logs_dir / f"{run_id}.jsonl"
    if not path.is_file():
        print(f"no JSON log for run_id={run_id}; offline runs write logs to {settings.logs_dir}")
        return EXIT_OK
    shown = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            event = cast(dict[str, Any], json.loads(line))
            if trace_id and event.get("trace_id") != trace_id:
                continue
            shown += 1
            license_ref = event.get("license_reference", "(none)")
            print(
                f"{event.get('step', 'unknown')} {event.get('status', 'unknown')} "
                f"source={event.get('source_id', '(none)')} "
                f"trace={event.get('trace_id', '(none)')} license_reference={license_ref}"
            )
    if not shown:
        print(f"no log events for run_id={run_id} trace_id={trace_id or '(all)'}")
    return EXIT_OK
