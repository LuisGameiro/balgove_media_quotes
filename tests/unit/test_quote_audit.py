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
        _settings(tmp_path), report_date=DAY, limit=1, execute=False
    )
    assert report["executed"] is False
    assert report["articles_scanned"] == 1
    assert not path.exists()


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
        lambda settings, report_date, topic_id, limit: (
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
                        "original_reporting_flag": "first-hand",
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
            _settings(tmp_path), report_date=DAY, limit=1, max_cost=1.0, execute=True, client=client
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


def test_relayed_copy_is_not_marked_publishable(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from media_quotes.quote_audit import _Article, _Candidate, _verify_candidate

    text = 'A spokesperson said, "Air defence deliveries will accelerate this winter."'
    article = _Article(
        row=SimpleNamespace(policy_flags={}),
        ref="fixture://guardian/test.json",
        metadata={
            "canonical_url": "https://example.invalid/a",
            "original_reporting_flag": "relayed wire copy",
        },
        text=text,
    )
    candidate = _Candidate(
        quote_text='"Air defence deliveries will accelerate this winter."',
        speaker="Spokesperson",
    )

    result = _verify_candidate(
        article,
        candidate,
        settings=_settings(tmp_path),
        report_date=DAY,
    )

    assert result["status"] == "rejected"
    assert result["verbatim_in_article"] is True


def test_verify_unmodified_fixture_without_flag_in_rolling_window(tmp_path: Path) -> None:
    """Fixtures omit the reporting flag and predate the report date but sit in the window."""
    from media_quotes.adapters import FixtureSource, build_adapter
    from media_quotes.quote_audit import _Article, _Candidate, _verify_candidate
    from media_quotes.registry import load_sources_csv

    settings = _settings(tmp_path)
    row = next(
        row for row in load_sources_csv(ROOT / "data/sources.csv") if row.source_id == "guardian"
    )
    adapter = build_adapter(row, FixtureSource(settings.fixtures_dir), replay=True)
    ref = "fixture://guardian/synthetic-guardian-topic-war_intensity.html"
    metadata = adapter.parse_metadata(adapter.fetch(ref))
    assert "original_reporting_flag" not in metadata
    span = metadata["quote_spans"][0]
    article = _Article(
        row=row,
        ref=ref,
        metadata=metadata,
        text=str(metadata["article_text"]),
    )
    candidate = _Candidate(quote_text=span["quote_text"], speaker=span["speaker"])

    result = _verify_candidate(article, candidate, settings=settings, report_date=DAY)

    assert result["verbatim_in_article"] is True
    assert result["status"] == "verified", result["failed_checks"]


@pytest.mark.parametrize("max_cost", [float("nan"), float("inf"), -1.0, 0.0])
def test_execute_rejects_non_finite_or_non_positive_cost_cap(
    tmp_path: Path, max_cost: float
) -> None:
    with pytest.raises(ConfigError, match="positive and finite"):
        quote_audit.audit_missed_quotes(
            _settings(tmp_path), report_date=DAY, limit=1, max_cost=max_cost, execute=True
        )


def test_execute_requires_cost_cap(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="--max-cost"):
        quote_audit.audit_missed_quotes(_settings(tmp_path), report_date=DAY, limit=1, execute=True)
