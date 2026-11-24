"""Shadow missed-quote audit: offline, injected client, never writes digest or export."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from media_quotes import quote_audit
from media_quotes.config import Settings
from media_quotes.exceptions import ConfigError
from media_quotes.llm_live import PinnedOpenAIClient

ROOT = Path(__file__).resolve().parents[2]
DAY = date(2026, 11, 20)


class FakeCompletions:
    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        message = type("Message", (), {"content": self.payload, "refusal": None})()
        choice = type("Choice", (), {"message": message, "finish_reason": "stop"})()
        return type("Response", (), {"id": "audit-1", "choices": [choice]})()


def _client(payload: str) -> tuple[PinnedOpenAIClient, FakeCompletions]:
    completions = FakeCompletions(payload)
    api = type("API", (), {"completions": completions})()
    raw_client = type("Client", (), {"chat": api})()
    return (
        PinnedOpenAIClient(
            raw_client,
            model_snapshot="openai/gpt-4o-mini-2024-07-18",
            prompt_version=quote_audit.PROMPT_VERSION,
            schema_hash_value=quote_audit._SCHEMA_HASH,
        ),
        completions,
    )


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        report_date=DAY,
        persist_to_postgres=True,
        llm_provider="openrouter",
        llm_model_snapshot="openai/gpt-4o-mini-2024-07-18",
        openrouter_api_key="test-key",  # type: ignore[arg-type]
        audit_dir=tmp_path / "audit",
        export_dir=tmp_path / "export",
        logs_dir=tmp_path / "logs",
        fixtures_dir=ROOT / "data/fixtures",
        sources_csv=ROOT / "data/sources.csv",
        sources_dir=ROOT / "data/sources",
        sections_csv=ROOT / "data/sections.csv",
        schema_path=ROOT / "data/quotes.schema.json",
        gold_set_dir=ROOT / "data/gold_set",
    )


def test_plan_is_free_and_writes_nothing(tmp_path: Path) -> None:
    report, path = quote_audit.audit_missed_quotes(
        _settings(tmp_path), report_date=DAY, limit=1, execute=False, provenance="fixture"
    )
    assert report["executed"] is False
    assert report["articles_scanned"] == 1
    assert not path.exists()


def test_fixture_audit_path_is_isolated_by_topic_and_provenance(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    fixture_path = quote_audit._audit_path(settings, DAY, "custom-topic", "fixture")
    assert fixture_path == (
        settings.audit_dir / "fixtures" / "custom-topic" / f"missed-quotes-{DAY.isoformat()}.json"
    )
    assert fixture_path.parent.is_dir()
    live_path = quote_audit._audit_path(settings, DAY, "custom-topic", "live_feed")
    assert live_path.parent == settings.audit_dir / "custom-topic"


def test_shadow_audit_verifies_candidates_without_publishing(tmp_path: Path) -> None:
    text = (
        "Officials confirmed that talks continued in Kyiv on Thursday. "
        "A minister said “Every additional air defence battery protects civilian heating networks "
        "across our cities.” The report continued with more detail."
    )
    payload = json.dumps(
        {
            "candidates": [
                {
                    "quote_text": (
                        "Every additional air defence battery protects "
                        "civilian heating networks across our cities."
                    ),
                    "speaker": "A minister",
                    "why_missed": "single-quote style",
                },
                {
                    "quote_text": "This quote is not in the article at all.",
                    "speaker": "",
                    "why_missed": "test",
                },
            ]
        }
    )
    client, completions = _client(payload)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        quote_audit,
        "_load_articles",
        lambda settings, report_date, topic_id, limit, provenance="fixture": (
            [
                quote_audit._Article(
                    row=type(
                        "Row",
                        (),
                        {
                            "source_id": "guardian",
                            "source_name": "Guardian",
                            "claim_only": False,
                            "requires_translation": False,
                            "policy_flags": {
                                "source_id": "guardian",
                                "claim_only": False,
                                "requires_translation": False,
                            },
                        },
                    )(),
                    ref="fixture://guardian/test.json",
                    metadata={
                        "canonical_url": "https://example.invalid/a",
                        "title": "Test article",
                        "byline": "Reporter",
                        "publication_date": DAY.isoformat(),
                    },
                    text=text,
                )
            ],
            [],
        ),
    )
    monkeypatch.setattr(quote_audit, "_published_texts", lambda *args, **kwargs: set())
    try:
        report, path = quote_audit.audit_missed_quotes(
            _settings(tmp_path),
            report_date=DAY,
            limit=1,
            max_cost=1.0,
            execute=True,
            client=client,
            provenance="fixture",
        )
    finally:
        monkeypatch.undo()
    assert len(completions.calls) == 1
    assert report["candidates_total"] == 2
    assert report["missed_publishable"] == 1
    statuses = {item["status"] for item in report["articles"][0]["candidates"]}
    assert statuses == {"verified", "rejected"}
    quote_audit.write_report(report, path)
    assert path.is_file()
    assert "no digest" in json.loads(path.read_text(encoding="utf-8"))["notice"]


def test_audit_verifies_earlier_articles_using_the_report_window(tmp_path: Path) -> None:
    from datetime import timedelta

    text = (
        "Officials confirmed that talks continued in Kyiv on Thursday. "
        "A minister said “Every additional air defence battery protects civilian heating networks "
        "across our cities.” The report continued with more detail."
    )
    payload = json.dumps(
        {
            "candidates": [
                {
                    "quote_text": (
                        "Every additional air defence battery protects "
                        "civilian heating networks across our cities."
                    ),
                    "speaker": "A minister",
                    "why_missed": "single-quote style",
                }
            ]
        }
    )
    client, _ = _client(payload)
    published_day = DAY - timedelta(days=3)
    row = type(
        "Row",
        (),
        {
            "source_id": "guardian",
            "source_name": "Guardian",
            "claim_only": False,
            "requires_translation": False,
            "policy_flags": {
                "source_id": "guardian",
                "claim_only": False,
                "requires_translation": False,
            },
        },
    )()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        quote_audit,
        "_load_articles",
        lambda settings, report_date, topic_id, limit, provenance="fixture": (
            [
                quote_audit._Article(
                    row=row,
                    ref="fixture://guardian/earlier.json",
                    metadata={
                        "canonical_url": "https://example.invalid/earlier",
                        "title": "Earlier article",
                        "byline": "Reporter",
                        "publication_date": published_day.isoformat(),
                    },
                    text=text,
                )
            ],
            [],
        ),
    )
    monkeypatch.setattr(quote_audit, "_published_texts", lambda *args, **kwargs: set())
    try:
        report, _path = quote_audit.audit_missed_quotes(
            _settings(tmp_path),
            report_date=DAY,
            limit=1,
            max_cost=1.0,
            execute=True,
            client=client,
            provenance="fixture",
        )
    finally:
        monkeypatch.undo()
    assert report["articles"][0]["candidates"][0]["status"] == "verified"


def test_write_report_uses_atomic_write_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, str]] = []

    def fake_atomic(path: Path, text: str) -> None:
        calls.append((path, text))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    monkeypatch.setattr(quote_audit, "atomic_write_text", fake_atomic)
    path = tmp_path / "nested" / "report.json"
    report = {"schema_version": "missed-quote-audit-v1", "executed": True}

    quote_audit.write_report(report, path)

    assert len(calls) == 1
    assert calls[0][0] == path
    assert json.loads(path.read_text(encoding="utf-8")) == report


def test_execute_requires_cost_cap(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="--max-cost"):
        quote_audit.audit_missed_quotes(
            _settings(tmp_path), report_date=DAY, limit=1, execute=True, provenance="fixture"
        )


def test_live_blob_audit_filters_sidecar_provenance_and_never_reads_fixtures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from media_quotes.adapters import BlobCache, FixtureSource
    from media_quotes.registry import load_sources_csv

    settings = _settings(tmp_path).model_copy(
        update={"blob_dir": tmp_path / "blobs", "fixtures_dir": tmp_path / "fixtures"}
    )
    cache = BlobCache(settings.blob_dir)
    guardian = next(
        row for row in load_sources_csv(settings.sources_csv) if row.source_id == "guardian"
    )
    monkeypatch.setattr("media_quotes.cli.common._load_registry", lambda _settings: [guardian])

    body = json.dumps(
        {
            "article_text": "Officials said talks continued in Kyiv on Thursday.",
            "publication_date": DAY.isoformat(),
            "canonical_url": "",
        }
    ).encode("utf-8")
    live_digest, _ = cache.store("guardian", body, suffix=".json")
    cache.store_meta(
        "guardian", live_digest, {"url": "https://example.invalid/live", "provenance": "live_feed"}
    )
    other_digest, _ = cache.store("guardian", body + b" ", suffix=".json")
    cache.store_meta(
        "guardian",
        other_digest,
        {"url": "https://example.invalid/other", "provenance": "live_bing"},
    )

    # A real fixture holding a quote that must never be audited in live mode.
    fixture_dir = settings.fixtures_dir / "guardian"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    (fixture_dir / "trap.json").write_text(
        json.dumps(
            {
                "article_text": "TRAP FIXTURE QUOTE that must not appear in a live audit.",
                "publication_date": DAY.isoformat(),
            }
        ),
        encoding="utf-8",
    )

    roots: list[Path] = []
    real_source = FixtureSource

    class SpySource(real_source):
        def __init__(self, root: Path | None = None) -> None:
            roots.append((root or settings.fixtures_dir).resolve())
            super().__init__(root)

    monkeypatch.setattr(quote_audit, "FixtureSource", SpySource)
    client, _ = _client('{"candidates": []}')
    report, _path = quote_audit.audit_missed_quotes(
        settings,
        report_date=DAY,
        limit=10,
        max_cost=1.0,
        execute=True,
        client=client,
        provenance="live_feed",
    )

    assert report["provenance"] == "live_feed"
    assert report["source_mode"] == "blobs"
    assert report["articles_scanned"] == 1
    assert any("sidecar provenance" in item for item in report["skipped_articles"])
    assert roots and all(root == settings.blob_dir.resolve() for root in roots)
    assert report["articles"][0]["canonical_url"] == "https://example.invalid/live"
    assert "TRAP FIXTURE QUOTE" not in json.dumps(report)


def test_live_blob_audit_skips_malformed_blob_without_aborting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from media_quotes.adapters import BlobCache
    from media_quotes.registry import load_sources_csv

    settings = _settings(tmp_path).model_copy(update={"blob_dir": tmp_path / "blobs"})
    cache = BlobCache(settings.blob_dir)
    guardian = next(
        row for row in load_sources_csv(settings.sources_csv) if row.source_id == "guardian"
    )
    monkeypatch.setattr("media_quotes.cli.common._load_registry", lambda _settings: [guardian])

    good = json.dumps(
        {
            "article_text": "Officials said talks continued in Kyiv on Thursday.",
            "publication_date": DAY.isoformat(),
            "canonical_url": "",
        }
    ).encode("utf-8")
    good_digest, _ = cache.store("guardian", good, suffix=".json")
    cache.store_meta(
        "guardian",
        good_digest,
        {"url": "https://example.invalid/good", "provenance": "live_feed"},
    )
    bad_digest, _ = cache.store("guardian", b"{not valid json", suffix=".json")
    cache.store_meta(
        "guardian",
        bad_digest,
        {"url": "https://example.invalid/bad", "provenance": "live_feed"},
    )

    report, _path = quote_audit.audit_missed_quotes(
        settings, report_date=DAY, limit=10, execute=False, provenance="live_feed"
    )

    assert report["articles_scanned"] == 1
    assert any(bad_digest in item for item in report["skipped_articles"])
