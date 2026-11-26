"""Persistence failures are audited as failed runs in a separate short session."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from typing import Any

import pytest

from conftest import settings_for
from media_quotes.config import Settings
from media_quotes.pipeline import DailyPipelineResult, _persist_result


def _result() -> DailyPipelineResult:
    return DailyPipelineResult(
        run_id="run-1",
        report_date=date(2026, 11, 19),
        dry_run=False,
        records=(),
        coverage=(),
        gaps=(),
        events=(),
        rejected=0,
        rejected_total=0,
        status="succeeded",
    )


def test_persist_failure_marks_run_failed_in_fresh_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    import media_quotes.db.repository as repo

    settings: Settings = settings_for(tmp_path, persist_to_postgres=True)
    sessions: list[str] = []

    class Session:
        def commit(self) -> None:
            sessions.append("commit")

    @contextmanager
    def factory(_settings: Settings) -> Any:
        sessions.append("open")
        yield Session()

    def failing_start(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("db down")

    marked: list[dict[str, Any]] = []

    def fake_finish(session: Any, **kwargs: Any) -> None:
        marked.append(kwargs)
        session.commit()

    monkeypatch.setattr(repo, "start_pipeline_run", failing_start)
    monkeypatch.setattr(repo, "finish_pipeline_run", fake_finish)

    with pytest.raises(RuntimeError, match="Failed to persist pipeline results"):
        _persist_result(_result(), settings, pipeline="mq-run", session_context=factory)

    assert marked and marked[0]["status"] == "failed"
    assert marked[0]["run_id"] == "run-1"
    assert sessions.count("open") == 2  # original plus the separate failure-marker session
