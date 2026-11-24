"""Pass 2 wiring: live collection feeds the same verification pipeline as blobs.

The collector and fetch boundaries are mocked, so these tests never touch the network
and never read a fixture file.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import pytest

from media_quotes import cli
from media_quotes.adapters import BlobCache, FixtureSource
from media_quotes.cli.common import EXIT_FAILURE, _load_registry, _run_id
from media_quotes.cli.run import _do_run
from media_quotes.config import Settings
from media_quotes.exceptions import ConfigError
from media_quotes.fetch import FetchPolicy
from media_quotes.live import FetchedArticle, LiveSourceResult
from media_quotes.models import ExportQuoteRecord, VerificationRecord
from media_quotes.pipeline import run_daily_pipeline

ROOT = Path(__file__).resolve().parents[2]
REPORT_DATE = date(2026, 11, 23)
_SOURCES = ("guardian", "bbc", "reuters")

_DEFAULT_QUOTE = (
    '"Air defence deliveries will accelerate this winter," '
    "Defence Minister Andrii Kovalenko said at the Kyiv briefing."
)
_QUOTES = {
    "guardian": _DEFAULT_QUOTE,
    "bbc": (
        '"Additional air defence systems will arrive before winter," '
        "President Volodymyr Zelensky said in Kyiv."
    ),
    "reuters": (
        '"NATO allies agreed on further support for Ukraine," a senior official said in Brussels.'
    ),
}


def _html_for(quote: str) -> bytes:
    return f"""<!doctype html>
<html lang="en">
<head>
  <title>Ukraine air defence update after summit</title>
  <meta name="author" content="Jane Reporter">
  <meta property="article:published_time" content="2026-11-23T08:00:00Z">
</head>
<body>
  <article>
    <p>Ukraine officials met European allies in Kyiv to coordinate air defence deliveries.</p>
    <p>{quote}</p>
    <p>Further NATO aid packages and Russia sanctions coordination remain under review.</p>
  </article>
</body>
</html>
""".encode()


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "sources_csv": ROOT / "data" / "sources.csv",
        "sources_dir": ROOT / "data" / "sources",
        "source_topics_csv": ROOT / "data" / "source_topic_routes.csv",
        "topic_rules_path": ROOT / "data" / "topic_rules.json",
        "topics_dir": ROOT / "data" / "topics",
        "sections_csv": ROOT / "data" / "sections.csv",
        "schema_path": ROOT / "data" / "quotes.schema.json",
        "digest_dir": tmp_path / "digests",
        "export_dir": tmp_path / "export",
        "blob_dir": tmp_path / "blobs",
        "audit_dir": tmp_path / "audit",
        "logs_dir": tmp_path / "logs",
        "report_date": REPORT_DATE,
        "persist_to_postgres": False,
        "llm_provider": "local",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def _only_sources(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    rows = [row for row in _load_registry(settings) if row.source_id in _SOURCES]
    monkeypatch.setattr("media_quotes.cli.run._load_registry", lambda _settings: rows)


def _success(
    cache: BlobCache,
    source_id: str,
    *,
    provenance: str = "live_feed",
    route: str = "official_feed",
) -> LiveSourceResult:
    url = f"https://www.example.com/{source_id}/article"
    html = _html_for(_QUOTES[source_id])
    digest, path = cache.store(source_id, html)
    cache.store_meta(
        source_id,
        digest,
        {
            "url": url,
            "title": "Ukraine air defence update after summit",
            "provenance": provenance,
            "discovered_via": route,
        },
    )
    article = FetchedArticle(
        url=url,
        title="Ukraine air defence update after summit",
        raw=html,
        sha256=digest,
        blob_path=str(path),
        provenance=provenance,  # type: ignore[arg-type]
        discovered_via=route,
    )
    return LiveSourceResult(
        [article], [{"url": url, "title": article.title}], provenance, route, []
    )  # type: ignore[arg-type]


def _failure() -> LiveSourceResult:
    return LiveSourceResult([], [], "live_feed", "official_feed", ["feed timeout"])


def _fixture_stamped_record() -> ExportQuoteRecord:
    now = datetime.combine(REPORT_DATE, time(12, 0), tzinfo=UTC)
    return ExportQuoteRecord(
        quote_id="Q-20261123-0001",
        quote_text_original=_QUOTES["guardian"],
        source_id="guardian",
        source_name="Guardian",
        source_tier=2,
        source_country_region="UK",
        source_type="publicly funded",
        rights_status="license_held",
        access_mode="licensed_database",
        license_reference="LIC-1",
        discovery_method="licensed_database",
        claim_only=False,
        original_reporting_flag="first-hand",
        source_language="en",
        publication_date=REPORT_DATE,
        article_name="Article",
        article_url="https://www.example.com/guardian/article",
        extraction_date=now,
        section_id="other",
        is_obsolete=False,
        content_hash="a" * 64,
        verification=VerificationRecord(
            verbatim_match=True, metadata_verified=True, method="m", verified_at=now
        ),
        provenance="fixture",
    )


def _install_collector(
    monkeypatch: pytest.MonkeyPatch, outcomes: dict[str, LiveSourceResult]
) -> tuple[list[str], list[dict[str, Any]]]:
    calls: list[str] = []
    kwargs_seen: list[dict[str, Any]] = []

    def fake_collector(row: Any, **kwargs: Any) -> LiveSourceResult:
        calls.append(row.source_id)
        kwargs_seen.append(kwargs)
        return outcomes.get(row.source_id, _failure())

    monkeypatch.setattr("media_quotes.cli.run.collect_live_source", fake_collector)
    return calls, kwargs_seen


def test_live_run_uses_collector_refs_and_stamps_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    _only_sources(monkeypatch, settings)
    cache = BlobCache(settings.blob_dir)
    outcomes = {
        "guardian": _success(cache, "guardian"),
        "bbc": _success(cache, "bbc", provenance="live_bing", route="bing_news"),
        "reuters": _success(cache, "reuters"),
    }
    calls, kwargs_seen = _install_collector(monkeypatch, outcomes)

    # Any attempt to list fixture refs means the run silently fell back to replay.
    def no_fixture_discovery(*args: object, **kwargs: object) -> list[str]:
        raise AssertionError("live run must not read fixtures")

    monkeypatch.setattr(FixtureSource, "list_refs", no_fixture_discovery)

    result = run_daily_pipeline(settings, report_date=REPORT_DATE, source_mode="live")

    assert sorted(calls) == sorted(_SOURCES)
    assert result.status != "failed"
    by_source = {record.source_id: record for record in result.records}
    assert by_source["guardian"].provenance == "live_feed"
    assert by_source["bbc"].provenance == "live_bing"
    assert by_source["reuters"].provenance == "live_feed"
    # Mixed collector routes keep the conservative run-level provenance.
    assert result.provenance == "live_feed"
    assert all(record.provenance != "fixture" for record in result.records)
    assert all("fixtures" not in str(record.article_url) for record in result.records)
    assert result.audit_json_path is not None
    audit_payload = json.loads(result.audit_json_path.read_text(encoding="utf-8"))
    assert audit_payload["version_manifest"]["fixture_replay"] is False
    assert audit_payload["version_manifest"]["source_mode"] == "live"

    cov = {item["source_id"]: item for item in result.coverage}
    assert cov["bbc"]["provenance"] == "live_bing"
    assert cov["bbc"]["discovery_route"] == "bing_news"
    assert any(
        event.get("step") == "discover" and event.get("provenance") == "live_bing"
        for event in result.events
    )

    assert kwargs_seen
    first = kwargs_seen[0]
    assert isinstance(first["policy"], FetchPolicy)
    assert isinstance(first["cache"], BlobCache)
    assert first["limit"] == settings.live_article_limit
    all_hosts = set().union(*(kwargs["extra_hosts"] for kwargs in kwargs_seen))
    assert "www.bbc.co.uk" in all_hosts  # configured content host passed to the collector


def test_one_collector_failure_is_a_disclosed_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    _only_sources(monkeypatch, settings)
    cache = BlobCache(settings.blob_dir)
    _install_collector(
        monkeypatch,
        {
            "guardian": _success(cache, "guardian"),
            "bbc": _success(cache, "bbc"),
            "reuters": _failure(),
        },
    )

    result = run_daily_pipeline(settings, report_date=REPORT_DATE, source_mode="live")

    assert any(record.source_id == "guardian" for record in result.records)
    collector_gaps = [gap for gap in result.gaps if "live collector error" in gap.get("detail", "")]
    assert any(
        gap["source_id"] == "reuters" and "feed timeout" in gap["detail"] for gap in collector_gaps
    )
    assert all(gap.get("provenance") == "live_feed" for gap in collector_gaps)


def test_all_mandatory_sources_failing_has_no_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    _only_sources(monkeypatch, settings)
    cache = BlobCache(settings.blob_dir)
    _install_collector(
        monkeypatch,
        {
            "guardian": _success(cache, "guardian"),
            "bbc": _failure(),
            "reuters": _failure(),
        },
    )

    result = run_daily_pipeline(settings, report_date=REPORT_DATE, source_mode="live")

    assert result.status == "failed"
    assert result.digest_path is None
    assert not list((settings.digest_dir).rglob("*.md"))
    assert result.logs_path is not None and result.logs_path.is_file()
    assert result.audit_json_path is not None and result.audit_json_path.is_file()
    assert result.store_path is not None and result.store_path.is_file()
    assert any(record.source_id == "guardian" for record in result.records)
    assert result.audit_json_path is not None
    report = json.loads(result.audit_json_path.read_text(encoding="utf-8"))
    assert report["artifacts"]["digest"] == ""


def test_blobs_mode_is_live_feed_not_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    _only_sources(monkeypatch, settings)
    cache = BlobCache(settings.blob_dir)
    _success(cache, "guardian")

    result = run_daily_pipeline(settings, report_date=REPORT_DATE, source_mode="blobs")
    assert result.provenance == "live_feed"
    assert result.records and all(record.provenance == "live_feed" for record in result.records)
    assert "fixtures" not in str(result.store_path)


def test_unusable_row_never_calls_collector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    blocked = [row for row in _load_registry(settings) if row.source_id == "reuters"]
    blocked = [row.model_copy(update={"enabled": False}) for row in blocked]
    monkeypatch.setattr("media_quotes.cli.run._load_registry", lambda _settings: blocked)
    calls, _ = _install_collector(monkeypatch, {})

    result = run_daily_pipeline(settings, report_date=REPORT_DATE, source_mode="live")

    assert calls == []
    assert result.gaps and result.gaps[0]["source_id"] == "reuters"
    assert result.gaps[0]["gap_reason"] in {"rights_blocked", "not_enabled"}
    assert result.status == "failed"
    assert result.coverage[0]["provenance"] == "live_feed"
    assert "discovery_route" in result.coverage[0]


def test_cli_live_flag_selects_live_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr("media_quotes.cli.app.get_settings", lambda: settings)
    run_kwargs: dict[str, Any] = {}
    backfill_kwargs: dict[str, Any] = {}
    monkeypatch.setattr(cli, "_do_run", lambda _settings, **kwargs: run_kwargs.update(kwargs) or 0)
    monkeypatch.setattr(
        cli, "_do_backfill", lambda _settings, **kwargs: backfill_kwargs.update(kwargs) or 0
    )

    assert cli.main(["run", "--live", "--date", REPORT_DATE.isoformat()]) == 0
    assert run_kwargs["source_mode"] == "live"

    assert cli.main(["run", "--from-blobs", "--date", REPORT_DATE.isoformat()]) == 0
    assert run_kwargs["source_mode"] == "blobs"

    assert (
        cli.main(
            [
                "backfill",
                "--live",
                "--from",
                "2026-11-22",
                "--to",
                "2026-11-23",
            ]
        )
        == 0
    )
    assert backfill_kwargs["source_mode"] == "live"
    assert backfill_kwargs["provenance"] == "live_feed"


def test_fixture_stamped_record_in_live_run_raises_before_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(
        "media_quotes.cli.run._collect",
        lambda *args, **kwargs: ([_fixture_stamped_record()], [], [], [], 0, {}),
    )

    with pytest.raises(ConfigError, match="fixture-stamped"):
        run_daily_pipeline(settings, report_date=REPORT_DATE, source_mode="live")

    assert not list(tmp_path.rglob("*.jsonl"))
    assert not list(tmp_path.rglob("*.md"))
    assert not list(tmp_path.rglob("audit-*.json"))


def test_live_digest_has_no_fixture_notice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    _only_sources(monkeypatch, settings)
    cache = BlobCache(settings.blob_dir)
    _install_collector(
        monkeypatch,
        {
            "guardian": _success(cache, "guardian"),
            "bbc": _success(cache, "bbc"),
            "reuters": _success(cache, "reuters"),
        },
    )

    result = run_daily_pipeline(settings, report_date=REPORT_DATE, source_mode="live")

    assert result.digest_path is not None
    text = result.digest_path.read_text(encoding="utf-8")
    assert "OFFLINE FIXTURE REPLAY" not in text
    assert "LIVE COLLECTION" in text


def test_non_fixture_default_run_id_differs_from_fixture(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    fixture_id = _run_id(REPORT_DATE, settings)
    live_id = _run_id(REPORT_DATE, settings, provenance="live_feed")
    assert live_id.startswith("run-")
    assert live_id != fixture_id


def test_live_run_without_refs_is_degraded_with_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    _only_sources(monkeypatch, settings)
    _install_collector(monkeypatch, {})

    result = run_daily_pipeline(settings, report_date=REPORT_DATE, source_mode="live")

    coverage = {item["source_id"]: item for item in result.coverage}
    assert coverage["guardian"]["status"] == "degraded"
    assert any(
        gap["source_id"] == "guardian" and "no articles were supplied" in gap["detail"]
        for gap in result.gaps
    )


def test_do_run_returns_failure_for_failed_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = _settings(tmp_path)
    _only_sources(monkeypatch, settings)
    cache = BlobCache(settings.blob_dir)
    _install_collector(
        monkeypatch,
        {
            "guardian": _success(cache, "guardian"),
            "bbc": _failure(),
            "reuters": _failure(),
        },
    )

    rc = _do_run(
        settings,
        report_date=REPORT_DATE,
        dry_run=False,
        run_id=None,
        verbose=False,
        source_mode="live",
    )

    assert rc == EXIT_FAILURE
    out = capsys.readouterr().out
    assert "fixture" not in out.lower()
    assert "live_feed quote(s)" in out


def test_live_pipeline_writes_live_review_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from media_quotes.risk_controls.models import ObsolescenceEvidence, ObsolescenceFlag

    settings = _settings(tmp_path)
    _only_sources(monkeypatch, settings)
    cache = BlobCache(settings.blob_dir)
    _install_collector(
        monkeypatch,
        {
            "guardian": _success(cache, "guardian"),
            "bbc": _success(cache, "bbc"),
            "reuters": _success(cache, "reuters"),
        },
    )

    quote_id = "Q-20261123-0001"
    new_flag = ObsolescenceFlag(
        evidence=ObsolescenceEvidence(
            old_quote_id=quote_id,
            old_content_hash="a" * 64,
            newer_article_id="art-2",
            newer_content_hash="b" * 64,
            newer_is_first_hand=True,
            newer_rights_cleared=True,
            superseding_span="Negotiations have been paused pending security verification.",
            decision_id="dec-seed-001",
            rationale="Newer official statement supersedes earlier negotiation position.",
        ),
        confidence=0.95,
        raised_at=datetime(2026, 11, 23, 9, 0, tzinfo=UTC),
        state="pending",
    )
    monkeypatch.setattr(
        "media_quotes.risk_controls.obsolescence.propose_obsolescence",
        lambda *args, **kwargs: [new_flag],
    )

    settings.audit_dir.mkdir(parents=True, exist_ok=True)
    fixture_queue = settings.audit_dir / "review-queue.json"
    fixture_payload = {"flags": []}
    fixture_queue.write_text(json.dumps(fixture_payload), encoding="utf-8")

    result = run_daily_pipeline(settings, report_date=REPORT_DATE, source_mode="live")
    assert result.provenance == "live_feed"

    live_queue = settings.audit_dir / "review-queue-live_feed.json"
    assert live_queue.is_file()
    live_ids = [
        flag["evidence"]["old_quote_id"]
        for flag in json.loads(live_queue.read_text(encoding="utf-8"))["flags"]
    ]
    assert quote_id in live_ids
    assert json.loads(fixture_queue.read_text(encoding="utf-8")) == fixture_payload

    audit = json.loads(
        (settings.audit_dir / f"audit-{REPORT_DATE.isoformat()}.json").read_text(encoding="utf-8")
    )
    assert quote_id in [flag["evidence"]["old_quote_id"] for flag in audit["review_queue"]]


def _archive_refs(cache: BlobCache, source_id: str) -> list[str]:
    article = _success(cache, source_id, route="archive").articles[0]
    return [f"fixture://{source_id}/{Path(str(article.blob_path)).name}"]


def _forbid_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("archive mode must not run live or fixture discovery")

    monkeypatch.setattr("media_quotes.cli.run.collect_live_source", forbidden)
    monkeypatch.setattr(FixtureSource, "list_refs", forbidden)


def test_archive_run_uses_prefetched_refs_without_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    _only_sources(monkeypatch, settings)
    cache = BlobCache(settings.blob_dir)
    refs = {source_id: _archive_refs(cache, source_id) for source_id in _SOURCES}
    _forbid_discovery(monkeypatch)

    result = run_daily_pipeline(
        settings, report_date=REPORT_DATE, source_mode="archive", prefetched_refs=refs
    )

    assert result.status == "succeeded"
    assert {record.source_id for record in result.records} == set(_SOURCES)
    assert all(record.provenance == "live_feed" for record in result.records)
    assert all("fixtures" not in str(record.article_url) for record in result.records)
    assert result.provenance == "live_feed"
    assert all(item["discovery_route"] == "archive" for item in result.coverage)
    assert result.audit_json_path is not None
    manifest = json.loads(result.audit_json_path.read_text(encoding="utf-8"))
    assert manifest["version_manifest"]["source_mode"] == "archive"
    assert manifest["version_manifest"]["fixture_replay"] is False


def test_archive_unusable_row_is_blocked_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    blocked = [row for row in _load_registry(settings) if row.source_id == "reuters"]
    blocked = [row.model_copy(update={"enabled": False}) for row in blocked]
    monkeypatch.setattr("media_quotes.cli.run._load_registry", lambda _settings: blocked)
    _forbid_discovery(monkeypatch)
    cache = BlobCache(settings.blob_dir)
    refs = {"reuters": _archive_refs(cache, "reuters")}

    result = run_daily_pipeline(
        settings, report_date=REPORT_DATE, source_mode="archive", prefetched_refs=refs
    )

    assert result.status == "failed"
    assert result.gaps[0]["source_id"] == "reuters"
    assert result.gaps[0]["gap_reason"] in {"rights_blocked", "not_enabled"}
    assert result.coverage[0]["status"] == "blocked"
    assert result.coverage[0]["discovery_route"] == "archive"


def test_archive_failed_mandatory_sources_publish_no_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    _only_sources(monkeypatch, settings)
    cache = BlobCache(settings.blob_dir)
    _forbid_discovery(monkeypatch)

    result = run_daily_pipeline(
        settings,
        report_date=REPORT_DATE,
        source_mode="archive",
        prefetched_refs={"guardian": _archive_refs(cache, "guardian")},
    )

    assert result.status == "failed"
    assert result.digest_path is None
    assert result.audit_json_path is not None
    report = json.loads(result.audit_json_path.read_text(encoding="utf-8"))
    assert report["artifacts"]["digest"] == ""
