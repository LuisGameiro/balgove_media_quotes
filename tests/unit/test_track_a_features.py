"""Unit tests for Track A assignment compliance features.

Covers:
1. Live-blob ingestion bridge (BlobCache sidecar metadata + mq run --from-blobs).
2. Tier-1 6-hourly Dagster schedule and --tier filtering.
3. Bounded executable historical backfill (mq backfill --execute + budget gate).
4. Multi-day per-source coverage view in dashboard.py.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("dagster")

from media_quotes import cli
from media_quotes.adapters import BlobCache
from media_quotes.config import Settings
from media_quotes.dashboard import coverage_by_day_matrix, daily_source_coverage
from media_quotes.orchestration import (
    TIER1_SCHEDULE_CRON,
    build_definitions,
)
from media_quotes.pipeline import run_daily_pipeline

_LIVE_HTML = b"""<!doctype html>
<html lang="en">
<head>
  <title>Ukraine air defence update after summit</title>
  <meta name="author" content="Jane Reporter">
  <meta property="article:published_time" content="2026-11-23T08:00:00Z">
</head>
<body>
  <article>
    <p>Ukraine officials met European allies in Kyiv to coordinate air defence deliveries.</p>
    <p>"Air defence deliveries will accelerate this winter," Defence Minister Andrii Kovalenko
    said at the Kyiv briefing.</p>
    <p>Further NATO aid packages and Russia sanctions coordination remain under review.</p>
  </article>
</body>
</html>
"""


def _isolated_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        output_dir=tmp_path / "output",
        digest_dir=tmp_path / "output" / "digests",
        blob_dir=tmp_path / "output" / "raw",
        audit_dir=tmp_path / "output" / "audit",
        export_dir=tmp_path / "output" / "quotes",
        logs_dir=tmp_path / "output" / "logs",
        persist_to_postgres=False,
        llm_provider="local",
    )


def test_blob_cache_sidecar_metadata_roundtrip(tmp_path: Path) -> None:
    cache = BlobCache(tmp_path / "raw")
    digest, _ = cache.store("guardian", _LIVE_HTML)
    meta_path = cache.store_meta(
        "guardian",
        digest,
        {"url": "https://www.theguardian.com/world/2026/nov/23/ukraine-air-defence", "title": "T"},
    )
    assert meta_path.is_file()
    loaded = cache.read_meta("guardian", digest)
    assert loaded["url"] == "https://www.theguardian.com/world/2026/nov/23/ukraine-air-defence"
    assert cache.read_meta("guardian", "a" * 64) == {}
    assert cache.read_meta("bad/id", digest) == {}
    with pytest.raises(ValueError, match="invalid source_id"):
        cache.store_meta("bad/id", digest, {"url": "https://example.com"})


def test_live_blob_bridge_ingests_t0_cleared_and_claim_only_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _isolated_settings(tmp_path)
    cache = BlobCache(settings.blob_dir)

    # Store live blobs for a standard source and an enabled tier-4 claim-only source.
    g_digest, _ = cache.store("guardian", _LIVE_HTML)
    cache.store_meta(
        "guardian",
        g_digest,
        {
            "url": "https://www.theguardian.com/world/2026/nov/23/ukraine-air-defence",
            "title": "Ukraine air defence update after summit",
        },
    )
    rt_html = _LIVE_HTML.replace(
        b"Air defence deliveries will accelerate this winter",
        b"RT claims air defence deliveries will accelerate this winter",
    )
    rt_digest, _ = cache.store("rt", rt_html)
    cache.store_meta(
        "rt",
        rt_digest,
        {"url": "https://www.rt.com/news/ukraine-air-defence", "title": "RT item"},
    )

    result = run_daily_pipeline(
        settings,
        report_date=date(2026, 11, 23),
        source_mode="blobs",
    )
    guardian_records = [rec for rec in result.records if rec.source_id == "guardian"]
    rt_records = [rec for rec in result.records if rec.source_id == "rt"]
    assert len(guardian_records) >= 1
    assert guardian_records[0].notes == "live blob ingest"
    assert (
        guardian_records[0].article_url
        == "https://www.theguardian.com/world/2026/nov/23/ukraine-air-defence"
    )
    assert rt_records
    assert all(record.claim_only for record in rt_records)

    # Also verify CLI flag `mq run --from-blobs --tier 2`
    monkeypatch.setattr("media_quotes.cli.app.get_settings", lambda: settings)
    rc = cli.main(["run", "--date", "2026-11-23", "--from-blobs", "--tier", "2"])
    assert rc in (0, 2)


def test_tier1_six_hourly_schedule_and_tier_filter(tmp_path: Path) -> None:
    import dagster as dg

    defs = build_definitions()
    by_name = {schedule.name: schedule for schedule in defs.schedules}
    assert "tier1_six_hourly_schedule" in by_name
    t1_schedule = by_name["tier1_six_hourly_schedule"]
    assert t1_schedule.cron_schedule == TIER1_SCHEDULE_CRON == "0 */6 * * *"
    assert t1_schedule.default_status == dg.DefaultScheduleStatus.STOPPED

    scheduled = datetime(2026, 11, 23, 12, tzinfo=UTC)
    context = dg.build_schedule_context(
        repository_def=defs.get_repository_def(),
        scheduled_execution_time=scheduled,
    )
    req = t1_schedule._execution_fn.decorated_fn(context)
    assert req.partition_key == "2026-11-23"
    assert req.run_key == "2026-11-23-tier1-12"
    assert req.run_config["ops"]["daily_quotes_digest"]["config"]["tier"] == 1

    # Running pipeline with tier=1 processes only Tier-1 sources (reuters, bbc)
    settings = _isolated_settings(tmp_path)
    result = run_daily_pipeline(
        settings,
        report_date=date(2026, 11, 23),
        tier=1,
    )
    covered_sources = {entry["source_id"] for entry in result.coverage}
    assert covered_sources == {"reuters", "bbc"}


def test_backfill_execute_runs_date_range_and_respects_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = _isolated_settings(tmp_path)
    monkeypatch.setattr("media_quotes.cli.app.get_settings", lambda: settings)

    # Budget gate must block before execution when max-cost is too low
    rc_blocked = cli.main(
        [
            "backfill",
            "--from",
            "2026-11-18",
            "--to",
            "2026-11-19",
            "--max-cost",
            "0.01",
            "--execute",
        ]
    )
    assert rc_blocked == 3
    assert not (settings.digest_dir / "2026-11-18.md").exists()

    # With sufficient budget, --execute runs both partitions and produces digests/audits
    rc_ok = cli.main(
        [
            "backfill",
            "--from",
            "2026-11-18",
            "--to",
            "2026-11-19",
            "--max-cost",
            "500",
            "--execute",
        ]
    )
    out = capsys.readouterr().out
    assert rc_ok in (0, 2)
    assert "Backfill executed: 2 day(s)" in out
    assert (settings.digest_dir / "2026-11-18.md").is_file()
    assert (settings.digest_dir / "2026-11-19.md").is_file()


def test_dashboard_daily_source_coverage_matrix(tmp_path: Path) -> None:
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir(parents=True)
    for day, counts in (
        ("2026-11-22", [("reuters", 2), ("bbc", 1)]),
        ("2026-11-23", [("reuters", 3), ("guardian", 4)]),
    ):
        payload: dict[str, Any] = {
            "report_date": day,
            "coverage": [
                {"source_id": sid, "quotes": q, "discovered": q, "status": "ok"}
                for sid, q in counts
            ],
        }
        (audit_dir / f"audit-{day}.json").write_text(json.dumps(payload), encoding="utf-8")

    rows = daily_source_coverage(audit_dir)
    assert len(rows) == 4
    matrix = coverage_by_day_matrix(rows)
    by_sid = {row["source_id"]: row for row in matrix}
    assert by_sid["reuters"] == {
        "source_id": "reuters",
        "2026-11-23": 3,
        "2026-11-22": 2,
        "total_quotes": 5,
    }
    assert by_sid["bbc"]["total_quotes"] == 1
    assert by_sid["guardian"]["2026-11-22"] == 0
    assert by_sid["guardian"]["2026-11-23"] == 4
