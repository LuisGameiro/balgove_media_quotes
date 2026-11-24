"""Unit tests for the read helpers added for live CLI backends."""

from __future__ import annotations

from unittest.mock import MagicMock

from media_quotes.db.repository import eval_recent_failures, read_audit_events


def test_eval_recent_failures_counts_consecutive_failures() -> None:
    session = MagicMock()
    session.execute.return_value.scalars.return_value.all.return_value = [False, False, True]
    assert eval_recent_failures(session, "synthetic-v1") == 2
    session.execute.return_value.scalars.return_value.all.return_value = [True, False]
    assert eval_recent_failures(session, "synthetic-v1") == 0


def test_read_audit_events_maps_rows() -> None:
    session = MagicMock()
    session.execute.return_value.mappings.return_value = [
        {
            "step": "verify",
            "status": "verified",
            "entity_type": "quote",
            "entity_id": "Q1",
            "trace_id": "t1",
            "details": {"license_reference": "LIC"},
        }
    ]
    events = read_audit_events(session, "run-1", trace_id="t1")
    assert events[0]["step"] == "verify"
    assert events[0]["details"]["license_reference"] == "LIC"
