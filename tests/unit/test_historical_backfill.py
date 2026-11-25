"""Pass 3: historical backfill orchestration with mocked discovery, fetch and pipeline."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

import media_quotes.backfill as backfill
from media_quotes.adapters import BlobCache
from media_quotes.archive import ArchiveDiscoveryResult, HistoricalArticle
from media_quotes.backfill import run_historical_backfill
from media_quotes.config import Settings
from media_quotes.exceptions import ConfigError
from media_quotes.live import FetchedArticle
from media_quotes.pipeline import DailyPipelineResult
from media_quotes.source_configs import SourceConfig

START = date(2026, 10, 1)
TOPIC = "ukraine-war"
SOURCE_ID = "archive-news"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        audit_dir=tmp_path / "audit",
        blob_dir=tmp_path / "blobs",
        active_topic=TOPIC,
        persist_to_postgres=False,
        llm_provider="local",
    )


def _config() -> SourceConfig:
    return SourceConfig(
        source_id=SOURCE_ID,
        source_name="Archive News",
        homepage_url="https://www.example.com/",
        feed_url="https://feeds.example.com/archive.xml",
        tier=2,
        cadence_hours=24,
        country_region="Global",
        source_type="independent",
        language="en",
        rights_status="license_held",
        access_mode="public_rss_permitted",
        license_reference="LIC-ARCHIVE-1",
        discovery_method="rss",
        archive_start_date=START,
        enabled=True,
        content_hosts=["www.example.com"],
        routes=[],
    )


class _MetadataAdapter:
    def __init__(self, published: dict[str, str]) -> None:
        self.published = published

    def parse_metadata(self, raw: bytes, *, source_url: str = "") -> dict[str, Any]:
        assert raw  # the fetched blob must be readable
        return {"publication_date": self.published.get(source_url, "")}


def _install(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    *,
    candidates: list[tuple[str, str]],
    statuses: list[str],
    fetch_errors: dict[str, str] | None = None,
    sources: list[SourceConfig] | None = None,
    failing_sources: frozenset[str] = frozenset(),
) -> dict[str, list[Any]]:
    """Wire mocked boundaries; return the recorded calls for each boundary."""
    calls: dict[str, list[Any]] = {"discover": [], "fetch": [], "pipeline": [], "writes": []}
    fetch_errors = fetch_errors or {}
    configs = sources if sources is not None else [_config()]
    rows = [config.to_registry_row() for config in configs]
    monkeypatch.setattr(backfill, "_load_registry", lambda _settings: rows)
    monkeypatch.setattr(
        backfill, "_load_source_configs", lambda _settings: {c.source_id: c for c in configs}
    )
    monkeypatch.setattr(backfill, "_load_topic_pack", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        backfill,
        "build_adapter",
        lambda row, fixture, **kwargs: _MetadataAdapter(
            {url: published for url, _, published in candidates}
        ),
    )

    def fake_discover(row_arg: Any, config_arg: Any, **kwargs: Any) -> ArchiveDiscoveryResult:
        calls["discover"].append(kwargs)
        if row_arg.source_id in failing_sources:
            raise RuntimeError("feed rejected")
        return ArchiveDiscoveryResult(
            candidates=[
                HistoricalArticle(
                    source_id=row_arg.source_id,
                    url=url,
                    title=title,
                    published=published,
                    discovery_url="https://feeds.example.com/archive.xml",
                )
                for url, title, published in candidates
            ],
            errors=[],
            sitemaps_fetched=0,
        )

    def fake_fetch(
        row_arg: Any, items: list[dict[str, str]], **kwargs: Any
    ) -> list[FetchedArticle]:
        calls["fetch"].append(kwargs)
        cache = BlobCache(settings.blob_dir)
        results: list[FetchedArticle] = []
        for item in items:
            url = item["url"]
            if url in fetch_errors:
                results.append(
                    FetchedArticle(
                        url=url,
                        title=item["title"],
                        raw=b"",
                        sha256="",
                        blob_path="",
                        error=fetch_errors[url],
                        provenance=kwargs["provenance"],
                        discovered_via=kwargs["discovered_via"],
                    )
                )
                continue
            digest, path = cache.store(row_arg.source_id, f"<html>{url}</html>".encode())
            results.append(
                FetchedArticle(
                    url=url,
                    title=item["title"],
                    raw=b"",
                    sha256=digest,
                    blob_path=str(path),
                    provenance=kwargs["provenance"],
                    discovered_via=kwargs["discovered_via"],
                )
            )
        return results

    def fake_pipeline(settings_arg: Any, **kwargs: Any) -> DailyPipelineResult:
        calls["pipeline"].append(kwargs)
        status = statuses[min(len(calls["pipeline"]) - 1, len(statuses) - 1)]
        return DailyPipelineResult(
            run_id=f"run-{kwargs['report_date'].isoformat()}",
            report_date=kwargs["report_date"],
            dry_run=False,
            records=(),
            coverage=(),
            gaps=({},) if status == "partial" else (),
            events=(),
            rejected=0,
            rejected_total=0,
            status=status,
        )

    real_atomic = backfill.atomic_write_text

    def spy_atomic(path: Path, text: str) -> None:
        calls["writes"].append((path, json.loads(text)))
        real_atomic(path, text)

    monkeypatch.setattr(backfill, "discover_historical_articles", fake_discover)
    monkeypatch.setattr(backfill, "fetch_url_documents", fake_fetch)
    monkeypatch.setattr(backfill, "run_daily_pipeline", fake_pipeline)
    monkeypatch.setattr(backfill, "atomic_write_text", spy_atomic)
    return calls


def _manifest_path(settings: Settings, end: date) -> Path:
    return backfill._manifest_path(settings, START, end, TOPIC)


def test_groups_refs_by_parsed_publication_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    end = date(2026, 10, 2)
    candidates = [
        ("https://www.example.com/a", "A", "2026-10-01"),
        ("https://www.example.com/b", "B", "2026-10-01"),
        ("https://www.example.com/c", "C", "2026-10-02"),
    ]
    calls = _install(monkeypatch, settings, candidates=candidates, statuses=["succeeded"] * 2)

    summary = run_historical_backfill(settings, start=START, end=end, max_cost=10)

    assert [call["report_date"] for call in calls["pipeline"]] == [START, end]
    for call in calls["pipeline"]:
        assert call["source_mode"] == "archive"
        assert call["provenance"] == "live_feed"
        assert call["pipeline"] == "mq-backfill-archive"
        assert call["topic_id"] == TOPIC
        assert call["source_ids"] == {SOURCE_ID}
    first_refs = calls["pipeline"][0]["prefetched_refs"][SOURCE_ID]
    second_refs = calls["pipeline"][1]["prefetched_refs"][SOURCE_ID]
    assert all(ref.startswith(f"fixture://{SOURCE_ID}/") for ref in first_refs + second_refs)
    assert len(first_refs) == 2 and len(first_refs[0]) == len(first_refs[1])
    assert len(set(first_refs)) == 2
    assert len(second_refs) == 1
    assert calls["discover"][0]["limit"] == settings.archive_article_limit
    assert calls["fetch"][0]["provenance"] == "live_feed"
    assert calls["fetch"][0]["discovered_via"] == "archive"
    assert calls["fetch"][0]["extra_hosts"] == frozenset({"www.example.com"})
    assert summary["discovered"] == 3
    assert summary["fetched"] == 3
    assert summary["statuses"] == {"succeeded": 2}
    assert summary["paid_calls"] == 0


def test_out_of_window_undated_and_failed_articles_are_recorded_not_assigned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    candidates = [
        ("https://www.example.com/in", "In", "2026-10-01"),
        ("https://www.example.com/old", "Old", "2026-09-30"),
        ("https://www.example.com/undated", "Undated", ""),
        ("https://www.example.com/broken", "Broken", "2026-10-01"),
    ]
    calls = _install(
        monkeypatch,
        settings,
        candidates=candidates,
        statuses=["succeeded"],
        fetch_errors={"https://www.example.com/broken": "timeout"},
    )

    summary = run_historical_backfill(settings, start=START, end=START, max_cost=10)

    refs = calls["pipeline"][0]["prefetched_refs"][SOURCE_ID]
    assert len(refs) == 1
    assert refs[0].rsplit("/", 1)[-1].endswith(".html")
    assert len(refs[0].rsplit("/", 1)[-1].removesuffix(".html")) == 64
    assert summary["fetched"] == 1
    issues = " ".join(summary["issues"])
    assert "out-of-window" in issues and "2026-09-30" in issues
    assert "undated" in issues
    assert "fetch failed" in issues and "timeout" in issues
    manifest = json.loads(_manifest_path(settings, START).read_text(encoding="utf-8"))
    assert summary["issues"] == manifest["issues"]


def test_resume_skips_completed_days_without_network_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    end = date(2026, 10, 3)
    path = _manifest_path(settings, end)
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "completed": {
            day.isoformat(): {
                "status": "succeeded",
                "quotes": 1,
                "gaps": 0,
                "run_id": f"run-{day.isoformat()}",
            }
            for day in (START, date(2026, 10, 2), end)
        }
    }
    path.write_text(json.dumps(manifest), encoding="utf-8")
    calls = _install(
        monkeypatch,
        settings,
        candidates=[("https://www.example.com/a", "A", "2026-10-01")],
        statuses=["failed"],
    )

    summary = run_historical_backfill(settings, start=START, end=end, max_cost=10)

    assert summary["manifest"] == str(path)
    assert summary["statuses"] == {"succeeded": 3}
    assert summary["quotes"] == 3
    assert calls["discover"] == []
    assert calls["fetch"] == []
    assert calls["pipeline"] == []
    assert calls["writes"] == []


def test_failed_day_is_retried_and_succeeded_days_are_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    end = date(2026, 10, 2)
    path = _manifest_path(settings, end)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "completed": {
                    START.isoformat(): {"status": "succeeded", "quotes": 1, "gaps": 0},
                    end.isoformat(): {"status": "failed", "quotes": 0, "gaps": 0},
                }
            }
        ),
        encoding="utf-8",
    )
    calls = _install(
        monkeypatch,
        settings,
        candidates=[("https://www.example.com/b", "B", "2026-10-02")],
        statuses=["succeeded"],
    )

    summary = run_historical_backfill(settings, start=START, end=end, max_cost=10)

    assert [call["report_date"] for call in calls["pipeline"]] == [end]
    assert summary["statuses"] == {"succeeded": 2}
    assert (
        json.loads(path.read_text(encoding="utf-8"))["completed"][end.isoformat()]["status"]
        == "succeeded"
    )


def test_zero_ref_day_is_recorded_empty_without_pipeline_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    end = date(2026, 10, 2)
    calls = _install(
        monkeypatch,
        settings,
        candidates=[("https://www.example.com/a", "A", "2026-10-01")],
        statuses=["succeeded", "succeeded"],
    )

    summary = run_historical_backfill(settings, start=START, end=end, max_cost=10)

    assert [call["report_date"] for call in calls["pipeline"]] == [START]
    manifest = json.loads(_manifest_path(settings, end).read_text(encoding="utf-8"))
    assert manifest["completed"][end.isoformat()] == {
        "status": "empty",
        "quotes": 0,
        "gaps": 0,
        "run_id": "",
    }
    assert summary["statuses"] == {"succeeded": 1, "empty": 1}
    assert summary["retry_empty"] is False
    assert len(calls["writes"]) == 2


def test_resume_skips_empty_days_without_network_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    end = date(2026, 10, 2)
    path = _manifest_path(settings, end)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "completed": {
                    START.isoformat(): {"status": "succeeded", "quotes": 1, "gaps": 0},
                    end.isoformat(): {"status": "empty", "quotes": 0, "gaps": 0},
                }
            }
        ),
        encoding="utf-8",
    )
    calls = _install(
        monkeypatch,
        settings,
        candidates=[("https://www.example.com/b", "B", "2026-10-02")],
        statuses=["succeeded"],
    )

    summary = run_historical_backfill(settings, start=START, end=end, max_cost=10)

    assert summary["statuses"] == {"succeeded": 1, "empty": 1}
    assert calls["discover"] == []
    assert calls["fetch"] == []
    assert calls["pipeline"] == []
    assert calls["writes"] == []


def test_retry_empty_re_enters_empty_days(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    end = date(2026, 10, 2)
    path = _manifest_path(settings, end)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "completed": {
                    START.isoformat(): {"status": "succeeded", "quotes": 1, "gaps": 0},
                    end.isoformat(): {"status": "empty", "quotes": 0, "gaps": 0},
                }
            }
        ),
        encoding="utf-8",
    )
    calls = _install(
        monkeypatch,
        settings,
        candidates=[("https://www.example.com/b", "B", "2026-10-02")],
        statuses=["succeeded"],
    )

    summary = run_historical_backfill(settings, start=START, end=end, max_cost=10, retry_empty=True)

    assert [call["report_date"] for call in calls["pipeline"]] == [end]
    assert len(calls["discover"]) == 1
    assert (
        json.loads(path.read_text(encoding="utf-8"))["completed"][end.isoformat()]["status"]
        == "succeeded"
    )
    assert summary["statuses"] == {"succeeded": 2}
    assert summary["retry_empty"] is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("llm_provider", "openrouter"),
        ("llm_provider", " OpenRouter "),
        ("translation_mode", "translate"),
        ("ai_judge_mode", "shadow"),
    ],
)
def test_paid_modes_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(settings, field, value)
    calls = _install(
        monkeypatch,
        settings,
        candidates=[("https://www.example.com/a", "A", "2026-10-01")],
        statuses=["succeeded"],
    )

    with pytest.raises(ConfigError, match="paid historical inference"):
        run_historical_backfill(settings, start=START, end=START, max_cost=10)

    assert calls["discover"] == []
    assert calls["pipeline"] == []


@pytest.mark.parametrize("max_cost", [None, 0, -1])
def test_max_cost_must_be_present_and_positive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, max_cost: float | None
) -> None:
    settings = _settings(tmp_path)
    calls = _install(
        monkeypatch,
        settings,
        candidates=[("https://www.example.com/a", "A", "2026-10-01")],
        statuses=["succeeded"],
    )

    with pytest.raises(ConfigError, match="max-cost"):
        run_historical_backfill(
            settings,
            start=START,
            end=START,
            max_cost=max_cost,  # type: ignore[arg-type]
        )

    assert calls["discover"] == []


def test_manifest_is_atomically_updated_after_each_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    end = date(2026, 10, 2)
    calls = _install(
        monkeypatch,
        settings,
        candidates=[
            ("https://www.example.com/a", "A", "2026-10-01"),
            ("https://www.example.com/b", "B", "2026-10-02"),
        ],
        statuses=["partial", "succeeded"],
    )

    summary = run_historical_backfill(settings, start=START, end=end, max_cost=10)

    path = _manifest_path(settings, end)
    writes = calls["writes"]
    assert len(writes) == 2
    assert all(written == path for written, _ in writes)
    assert list(writes[0][1]["completed"]) == [START.isoformat()]
    assert list(writes[1][1]["completed"]) == [START.isoformat(), end.isoformat()]
    assert writes[0][1]["completed"][START.isoformat()]["status"] == "partial"
    assert summary["manifest"] == str(path)
    assert summary["gaps"] == 1
    assert summary["statuses"] == {"partial": 1, "succeeded": 1}
    assert not list(path.parent.glob(".*.tmp"))


@pytest.mark.parametrize(
    ("stage", "message"),
    [
        ("discover", "feed rejected"),
        ("fetch", "fetch rejected"),
        ("parse", "parse rejected"),
    ],
)
def test_source_exception_becomes_issue_and_other_days_continue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    message: str,
) -> None:
    settings = _settings(tmp_path)
    end = date(2026, 10, 2)
    bad_id = "bad-news"
    bad_config = _config().model_copy(update={"source_id": bad_id})
    calls = _install(
        monkeypatch,
        settings,
        candidates=[
            ("https://www.example.com/a", "A", "2026-10-01"),
            ("https://www.example.com/b", "B", "2026-10-02"),
        ],
        statuses=["succeeded", "succeeded"],
        sources=[_config(), bad_config],
        failing_sources=frozenset({bad_id}) if stage == "discover" else frozenset(),
    )
    if stage == "fetch":
        real_fetch = backfill.fetch_url_documents

        def failing_fetch(row_arg: Any, items: list[dict[str, str]], **kwargs: Any) -> Any:
            if row_arg.source_id == bad_id:
                raise RuntimeError(message)
            return real_fetch(row_arg, items, **kwargs)

        monkeypatch.setattr(backfill, "fetch_url_documents", failing_fetch)
    elif stage == "parse":
        real_build = backfill.build_adapter

        class BrokenAdapter:
            def parse_metadata(self, raw: bytes, *, source_url: str = "") -> dict[str, Any]:
                raise RuntimeError(message)

        def failing_build(row_arg: Any, fixture: Any, **kwargs: Any) -> Any:
            return (
                BrokenAdapter()
                if row_arg.source_id == bad_id
                else real_build(row_arg, fixture, **kwargs)
            )

        monkeypatch.setattr(backfill, "build_adapter", failing_build)

    summary = run_historical_backfill(settings, start=START, end=end, max_cost=10)

    assert [call["report_date"] for call in calls["pipeline"]] == [START, end]
    assert all(set(call["prefetched_refs"]) == {SOURCE_ID} for call in calls["pipeline"])
    assert summary["statuses"] == {"succeeded": 2}
    assert summary["fetched"] == 2
    assert any(bad_id in issue and message in issue for issue in summary["issues"])
    manifest = json.loads(_manifest_path(settings, end).read_text(encoding="utf-8"))
    assert manifest["completed"][end.isoformat()]["status"] == "succeeded"


def test_live_backfill_cli_skips_paid_estimate_budget_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from media_quotes.cli import ops

    settings = _settings(tmp_path)
    monkeypatch.setattr(ops, "_load_registry", lambda _settings: [])

    def forbidden_gate(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("archive backfill must not apply the synthetic paid-call budget gate")

    monkeypatch.setattr(ops, "budget_gate", forbidden_gate)
    captured: dict[str, Any] = {}

    def fake_backfill(settings_arg: Settings, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"manifest": "manifest.json", "gaps": 0, "completed": {}}

    monkeypatch.setattr(backfill, "run_historical_backfill", fake_backfill)

    rc = ops._do_backfill(
        settings,
        start=START,
        end=START,
        dry_run=False,
        max_cost=42.0,
        execute=True,
        source_mode="live",
        provenance="live_feed",
    )

    assert rc == 0
    assert captured["max_cost"] == 42.0
    out = capsys.readouterr().out
    assert "paid inference is disabled" in out.lower()
    assert "zero" in out.lower()
    assert "Manifest: manifest.json" in out

    with pytest.raises(ConfigError, match="positive"):
        ops._do_backfill(
            settings,
            start=START,
            end=START,
            dry_run=False,
            max_cost=0,
            execute=True,
            source_mode="live",
        )


def test_span_over_limit_raises_before_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    calls = _install(
        monkeypatch,
        settings,
        candidates=[("https://www.example.com/a", "A", "2026-10-01")],
        statuses=["succeeded"],
    )

    with pytest.raises(ConfigError, match="365-day limit"):
        run_historical_backfill(settings, start=START, end=START + timedelta(days=365), max_cost=10)

    assert calls["discover"] == []
    assert calls["pipeline"] == []


def test_resume_ignores_fetched_articles_for_completed_middle_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    middle = START + timedelta(days=1)
    last = START + timedelta(days=2)
    path = _manifest_path(settings, last)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "completed": {
                    middle.isoformat(): {
                        "status": "succeeded",
                        "quotes": 1,
                        "gaps": 0,
                        "run_id": "run-middle",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    candidates = [
        ("https://www.example.com/a", "A", "2026-10-01"),
        ("https://www.example.com/m", "M", "2026-10-02"),
        ("https://www.example.com/z", "Z", "2026-10-03"),
    ]
    calls = _install(monkeypatch, settings, candidates=candidates, statuses=["succeeded"] * 2)

    summary = run_historical_backfill(settings, start=START, end=last, max_cost=10)

    assert calls["discover"][0]["start"] == START
    assert calls["discover"][0]["end"] == last
    assert [call["report_date"] for call in calls["pipeline"]] == [START, last]
    cache = BlobCache(settings.blob_dir)

    def ref_for(url: str) -> str:
        digest, blob = cache.store(SOURCE_ID, f"<html>{url}</html>".encode())
        assert digest
        return f"fixture://{SOURCE_ID}/{Path(blob).name}"

    first_refs = calls["pipeline"][0]["prefetched_refs"][SOURCE_ID]
    last_refs = calls["pipeline"][1]["prefetched_refs"][SOURCE_ID]
    assert first_refs == [ref_for("https://www.example.com/a")]
    assert last_refs == [ref_for("https://www.example.com/z")]
    assert ref_for("https://www.example.com/m") not in first_refs + last_refs
    assert summary["fetched"] == 2
    assert summary["statuses"] == {"succeeded": 3}
    assert any(
        "completed article" in issue and middle.isoformat() in issue for issue in summary["issues"]
    )


def test_archive_backfill_days_are_scoped_away_from_live_daily_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from media_quotes.cli.common import run_scope

    settings = _settings(tmp_path)
    end = date(2026, 10, 2)
    calls = _install(
        monkeypatch,
        settings,
        candidates=[
            ("https://www.example.com/a", "A", "2026-10-01"),
            ("https://www.example.com/b", "B", "2026-10-02"),
        ],
        statuses=["succeeded", "succeeded"],
    )

    run_historical_backfill(settings, start=START, end=end, max_cost=10)

    assert calls["pipeline"]
    scopes = set()
    for call in calls["pipeline"]:
        scope = run_scope(
            call["source_mode"], source_ids=call["source_ids"], provenance=call["provenance"]
        )
        assert scope is not None and scope.startswith("archive-")
        assert scope != run_scope("live")  # a complete live run stays canonical
        scopes.add(scope)
    assert len(scopes) == 1  # same source filter yields a deterministic scope


def test_source_ids_filter_limits_discovery_to_requested_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    other = _config().model_copy(update={"source_id": "other-news"})
    calls = _install(
        monkeypatch,
        settings,
        candidates=[("https://www.example.com/a", "A", "2026-10-01")],
        statuses=["succeeded"],
        sources=[_config(), other],
    )

    summary = run_historical_backfill(
        settings, start=START, end=START, max_cost=10, source_ids={SOURCE_ID}
    )

    assert len(calls["discover"]) == 1
    assert len(calls["fetch"]) == 1
    assert all(set(call["prefetched_refs"]) == {SOURCE_ID} for call in calls["pipeline"])
    assert summary["discovered"] == 1
    assert summary["fetched"] == 1


def test_one_article_parse_failure_does_not_skip_later_articles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    urls = [f"https://www.example.com/{number}" for number in (1, 2, 3)]
    candidates = [(url, f"Article {url[-1]}", "2026-10-01") for url in urls]
    calls = _install(monkeypatch, settings, candidates=candidates, statuses=["succeeded"])
    real_build = backfill.build_adapter

    class PartlyBrokenAdapter:
        def __init__(self, inner: Any) -> None:
            self.inner = inner

        def parse_metadata(self, raw: bytes, *, source_url: str = "") -> dict[str, Any]:
            if source_url.endswith("/2"):
                raise RuntimeError("parse rejected")
            return self.inner.parse_metadata(raw, source_url=source_url)

    def failing_build(row_arg: Any, fixture: Any, **kwargs: Any) -> Any:
        return PartlyBrokenAdapter(real_build(row_arg, fixture, **kwargs))

    monkeypatch.setattr(backfill, "build_adapter", failing_build)

    summary = run_historical_backfill(settings, start=START, end=START, max_cost=10)

    refs = calls["pipeline"][0]["prefetched_refs"][SOURCE_ID]
    assert len(refs) == 2  # articles #1 and #3, despite #2 raising
    assert summary["discovered"] == 3
    assert summary["fetched"] == 2
    assert any("/2" in issue and "parse rejected" in issue for issue in summary["issues"])
    manifest = json.loads(_manifest_path(settings, START).read_text(encoding="utf-8"))
    assert manifest["completed"][START.isoformat()]["status"] == "succeeded"


def test_article_config_error_still_aborts_backfill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    calls = _install(
        monkeypatch,
        settings,
        candidates=[("https://www.example.com/a", "A", "2026-10-01")],
        statuses=["succeeded"],
    )
    real_build = backfill.build_adapter

    class ConfigBrokenAdapter:
        def __init__(self, inner: Any) -> None:
            self.inner = inner

        def parse_metadata(self, raw: bytes, *, source_url: str = "") -> dict[str, Any]:
            raise ConfigError("invalid extractor configuration")

    def failing_build(row_arg: Any, fixture: Any, **kwargs: Any) -> Any:
        return ConfigBrokenAdapter(real_build(row_arg, fixture, **kwargs))

    monkeypatch.setattr(backfill, "build_adapter", failing_build)

    with pytest.raises(ConfigError, match="invalid extractor configuration"):
        run_historical_backfill(settings, start=START, end=START, max_cost=10)
    assert calls["pipeline"] == []
