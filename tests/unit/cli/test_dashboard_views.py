"""E2 — dashboard coverage and review data come from the latest canonical audit report."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from media_quotes.dashboard import _latest_audit, coverage_by_day_matrix, daily_source_coverage


def write_audit(
    audit_dir: Path, payload: dict[str, object], day: str = "audit-2026-11-19.json"
) -> None:
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / day).write_text(json.dumps(payload), encoding="utf-8")


def test_latest_audit_selects_newest_report(tmp_path: Path) -> None:
    assert _latest_audit(tmp_path) == {}
    write_audit(
        tmp_path,
        {
            "provenance": "live_feed",
            "coverage": [{"source_id": "reuters", "status": "blocked"}],
            "review_queue": [{"quote_id": "Q"}],
        },
        day="audit-2026-11-18.json",
    )
    write_audit(
        tmp_path,
        {
            "provenance": "live_feed",
            "coverage": [{"source_id": "bbc", "status": "blocked"}],
            "review_queue": [{"quote_id": "Q2", "state": "pending"}],
        },
        day="audit-2026-11-19.json",
    )
    latest = _latest_audit(tmp_path)
    assert latest["coverage"] == [{"source_id": "bbc", "status": "blocked"}]
    assert latest["review_queue"] == [{"quote_id": "Q2", "state": "pending"}]


def test_audit_report_includes_review_queue_key(tmp_path: Path) -> None:
    from media_quotes.cli.audit import _audit_report
    from media_quotes.cli.common import _parse_report_date
    from media_quotes.config import Settings

    settings = Settings(_env_file=None)
    report = _audit_report(
        settings,
        report_date=_parse_report_date(None, settings),
        run_id="r",
        records=[],
        coverage=[],
        gaps=[],
        events=[],
        rejected=0,
    )
    assert report["review_queue"] == []


def test_live_audit_wins_over_fixture_audit(tmp_path: Path) -> None:
    # A fixture report and a canonical live report for the same topic/day coexist.
    write_audit(
        tmp_path / "fixtures",
        {
            "topic_id": "ukraine-war",
            "provenance": "fixture",
            "coverage": [{"source_id": "reuters", "quotes": 1}],
        },
    )
    write_audit(
        tmp_path,
        {
            "topic_id": "ukraine-war",
            "provenance": "live_feed",
            "report_date": "2026-11-19",
            "coverage": [{"source_id": "bbc", "quotes": 9}],
        },
    )
    assert _latest_audit(tmp_path)["provenance"] == "live_feed"
    assert daily_source_coverage(tmp_path) == [
        {"date": "2026-11-19", "source_id": "bbc", "quotes": 9}
    ]

    # Topic isolation still holds on the live tree.
    assert _latest_audit(tmp_path, topic_id="ai-policy") == {}


def test_canonical_audit_tree_skips_legacy_payloads(tmp_path: Path) -> None:
    write_audit(
        tmp_path,
        {
            "topic_id": "ukraine-war",
            "report_date": "2026-11-18",
            "coverage": [{"source_id": "legacy-no-provenance", "quotes": 1}],
        },
        day="audit-2026-11-18.json",
    )
    write_audit(
        tmp_path,
        {
            "topic_id": "ukraine-war",
            "report_date": "2026-11-19",
            "provenance": "fixture",
            "coverage": [{"source_id": "legacy-fixture", "quotes": 2}],
        },
        day="audit-2026-11-19.json",
    )
    assert _latest_audit(tmp_path) == {}
    assert daily_source_coverage(tmp_path) == []

    # Fixtures remain a fallback and are not filtered there.
    fixture_payload = {
        "topic_id": "ukraine-war",
        "report_date": "2026-11-17",
        "coverage": [{"source_id": "fixture-source", "quotes": 3}],
    }
    write_audit(tmp_path / "fixtures", fixture_payload, day="audit-2026-11-17.json")
    assert _latest_audit(tmp_path)["report_date"] == "2026-11-17"
    assert daily_source_coverage(tmp_path) == [
        {"date": "2026-11-17", "source_id": "fixture-source", "quotes": 3}
    ]


def test_scoped_runs_are_not_mistaken_for_canonical_audits(tmp_path: Path) -> None:
    write_audit(
        tmp_path / "runs" / "archive",
        {
            "topic_id": "ukraine-war",
            "report_date": "2026-11-20",
            "coverage": [{"source_id": "archive-news", "quotes": 7}],
        },
        day="audit-2026-11-20.json",
    )
    write_audit(
        tmp_path,
        {
            "topic_id": "ukraine-war",
            "report_date": "2026-11-19",
            "provenance": "live_feed",
            "coverage": [{"source_id": "reuters", "quotes": 2}],
        },
        day="audit-2026-11-19.json",
    )
    assert _latest_audit(tmp_path)["report_date"] == "2026-11-19"
    assert daily_source_coverage(tmp_path) == [
        {"date": "2026-11-19", "source_id": "reuters", "quotes": 2}
    ]


def test_daily_source_coverage_does_not_double_count(tmp_path: Path) -> None:
    for day, quotes in (("2026-11-18", 2), ("2026-11-19", 3)):
        write_audit(
            tmp_path,
            {
                "topic_id": "ukraine-war",
                "report_date": day,
                "provenance": "live_feed",
                "coverage": [{"source_id": "guardian", "quotes": quotes}],
            },
            day=f"audit-{day}.json",
        )
        write_audit(
            tmp_path / "fixtures",
            {
                "topic_id": "ukraine-war",
                "report_date": day,
                "coverage": [{"source_id": "guardian", "quotes": 100}],
            },
            day=f"audit-{day}.json",
        )

    records = daily_source_coverage(tmp_path)
    assert records == [
        {"date": "2026-11-18", "source_id": "guardian", "quotes": 2},
        {"date": "2026-11-19", "source_id": "guardian", "quotes": 3},
    ]
    matrix = coverage_by_day_matrix(records)
    assert matrix == [
        {
            "source_id": "guardian",
            "2026-11-19": 3,
            "2026-11-18": 2,
            "total_quotes": 5,
        }
    ]


def test_daily_source_coverage_skips_files_outside_recent_window(tmp_path: Path) -> None:
    today = date.today()
    old_day = (today - timedelta(days=90)).isoformat()
    fresh_day = today.isoformat()
    write_audit(
        tmp_path,
        {
            "topic_id": "ukraine-war",
            "report_date": old_day,
            "provenance": "live_feed",
            "coverage": [{"source_id": "stale-source", "quotes": 99}],
        },
        day=f"audit-{old_day}.json",
    )
    write_audit(
        tmp_path,
        {
            "topic_id": "ukraine-war",
            "report_date": fresh_day,
            "provenance": "live_feed",
            "coverage": [{"source_id": "fresh-source", "quotes": 3}],
        },
        day=f"audit-{fresh_day}.json",
    )

    assert daily_source_coverage(tmp_path) == [
        {"date": fresh_day, "source_id": "fresh-source", "quotes": 3}
    ]


def test_daily_source_coverage_fails_open_on_malformed_filename(tmp_path: Path) -> None:
    write_audit(
        tmp_path,
        {
            "topic_id": "ukraine-war",
            "report_date": "2020-01-01",
            "provenance": "live_feed",
            "coverage": [{"source_id": "handwritten", "quotes": 1}],
        },
        day="audit-handwritten.json",
    )
    assert daily_source_coverage(tmp_path) == [
        {"date": "2020-01-01", "source_id": "handwritten", "quotes": 1}
    ]
