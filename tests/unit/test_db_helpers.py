"""Unit tests for the read helpers added for live CLI backends."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from media_quotes.db.repository import (
    _event_details,
    _inserted_count,
    _jsonable,
    _run_uuid,
    append_audit_events,
    eval_recent_failures,
    finish_pipeline_run,
    get_decision_cache,
    read_audit_events,
    sync_storage_partitions,
    upsert_decision_cache,
)
from media_quotes.db.repository.evaluation import DecisionCacheEntry


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


def test_read_audit_events_without_trace_filter() -> None:
    session = MagicMock()
    session.execute.return_value.mappings.return_value = []
    assert read_audit_events(session, "run-1") == []
    session.execute.assert_called_once()
    statement = str(session.execute.call_args.args[0])
    assert "audit_events.trace_id =" not in statement


def test_eval_recent_failures_rejects_non_positive_limit_and_handles_empty() -> None:
    session = MagicMock()
    session.execute.return_value.scalars.return_value.all.return_value = []
    assert eval_recent_failures(session, "synthetic-v1") == 0
    with pytest.raises(ValueError, match="limit must be positive"):
        eval_recent_failures(session, "synthetic-v1", limit=0)


def test_inserted_count_falls_back_for_negative_or_missing_rowcount() -> None:
    assert _inserted_count(MagicMock(rowcount=-1), 5) == 5
    assert _inserted_count(MagicMock(rowcount=3), 5) == 3
    assert _inserted_count(object(), 5) == 5


def test_jsonable_and_run_uuid_are_stable() -> None:
    value = {"when": date(2026, 11, 24), "id": UUID(int=42)}
    converted = _jsonable(value)
    assert converted == {"when": "2026-11-24", "id": str(UUID(int=42))}
    assert _run_uuid("run-1") == _run_uuid("run-1")
    assert _run_uuid("run-1") != _run_uuid("run-2")


def test_event_details_excludes_reserved_keys() -> None:
    details = _event_details(
        {"run_id": "r", "trace_id": "t", "step": "s", "status": "ok", "extra": 1}
    )
    assert details == {"extra": 1}


def test_finish_pipeline_run_rejects_invalid_status() -> None:
    session = MagicMock()
    with pytest.raises(ValueError, match="invalid terminal run status"):
        finish_pipeline_run(session, run_id="r", status="running", stats={})
    session.execute.assert_not_called()


def test_append_audit_events_empty_and_details_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    assert append_audit_events(session, []) == 0
    session.execute.assert_not_called()

    seen: list[object] = []
    monkeypatch.setattr(
        "media_quotes.db.repository.runs._jsonable",
        lambda value: seen.append(value) or value,
    )
    assert append_audit_events(session, [{"run_id": "r", "step": "collect", "note": "x"}]) == 1
    assert seen == [{"note": "x"}]


def test_sync_storage_partitions_returns_zero_without_months() -> None:
    session = MagicMock()
    session.execute.return_value.scalars.return_value.all.return_value = []
    assert sync_storage_partitions(session) == 0
    session.commit.assert_not_called()


def _cache_entry() -> DecisionCacheEntry:
    return DecisionCacheEntry(
        cache_key="k",
        model_snapshot="m",
        prompt_version="p",
        schema_hash="s",
        input_hash="i",
        output={},
    )


def test_decision_cache_round_trip_and_conflict() -> None:
    session = MagicMock()
    session.execute.return_value.rowcount = 0
    assert upsert_decision_cache(session, _cache_entry()) is False
    session.execute.return_value.rowcount = 1
    assert upsert_decision_cache(session, _cache_entry(), commit=False) is True

    session.execute.return_value.mappings.return_value.first.return_value = None
    assert get_decision_cache(session, "missing") is None
    session.execute.return_value.mappings.return_value.first.return_value = {"cache_key": "k"}
    assert get_decision_cache(session, "k") == {"cache_key": "k"}
