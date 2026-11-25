"""Unit tests for the read helpers added for live CLI backends."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session

from media_quotes.db.repository import _run_uuid, eval_recent_failures, read_audit_events
from media_quotes.db.tables import eval_runs_table


def test_eval_recent_failures_counts_consecutive_failures() -> None:
    session = MagicMock()
    session.execute.return_value.scalars.return_value.all.return_value = [False, False, True]
    assert eval_recent_failures(session, "synthetic-v1") == 2
    session.execute.return_value.scalars.return_value.all.return_value = [True, False]
    assert eval_recent_failures(session, "synthetic-v1") == 0


def _eval_session(*rows: dict[str, Any]) -> Session:
    engine = create_engine("sqlite://")
    eval_runs_table.create(engine)
    session = Session(engine)
    for row in rows:
        session.execute(insert(eval_runs_table).values(**row))
    session.commit()
    return session


def _eval_row(
    name: str, *, passed: bool, metrics: dict[str, Any], created_at: datetime, report_date: date
) -> dict[str, Any]:
    return {
        "eval_id": _run_uuid(name),
        "gold_set_version": "synthetic-v1",
        "run_id": f"eval-{name}",
        "report_date": report_date,
        "metrics": metrics,
        "thresholds": {},
        "passed": passed,
        "created_at": created_at,
    }


def test_eval_recent_failures_orders_by_created_at_not_report_date() -> None:
    now = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
    session = _eval_session(
        _eval_row(
            "new-created-old-report",
            passed=False,
            metrics={},
            created_at=now,
            report_date=date(2026, 11, 1),
        ),
        _eval_row(
            "old-created-new-report",
            passed=True,
            metrics={},
            created_at=now - timedelta(days=1),
            report_date=date(2026, 11, 24),
        ),
    )
    # A backfilled failure is the newest decision despite its older report_date.
    assert eval_recent_failures(session, "synthetic-v1") == 1
    session.close()


def test_eval_recent_failures_ignores_dev_split() -> None:
    now = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
    session = _eval_session(
        _eval_row(
            "dev-split",
            passed=False,
            metrics={"evaluated_split": "dev"},
            created_at=now,
            report_date=date(2026, 11, 25),
        ),
        _eval_row(
            "full-failure",
            passed=False,
            metrics={},
            created_at=now - timedelta(days=1),
            report_date=date(2026, 11, 24),
        ),
        _eval_row(
            "full-pass",
            passed=True,
            metrics={"evaluated_split": "all"},
            created_at=now - timedelta(days=2),
            report_date=date(2026, 11, 23),
        ),
    )
    # The dev run neither counts nor breaks the streak of full evaluations.
    assert eval_recent_failures(session, "synthetic-v1") == 1
    session.close()


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
