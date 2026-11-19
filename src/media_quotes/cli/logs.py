"""Correlated offline run log inspection."""

from __future__ import annotations

import json
from typing import Any, cast

from ..config import Settings
from .common import EXIT_OK


def _do_logs(settings: Settings, *, run_id: str, trace_id: str | None) -> int:  # pragma: no cover
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
                f"{event['step']} {event['status']} source={event['source_id']} "
                f"trace={event['trace_id']} license_reference={license_ref}"
            )
    if not shown:
        print(f"no log events for run_id={run_id} trace_id={trace_id or '(all)'}")
    return EXIT_OK
