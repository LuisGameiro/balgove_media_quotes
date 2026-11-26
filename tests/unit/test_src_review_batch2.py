"""Regressions for the second batch of src-review findings (offline only)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from media_quotes.config import Settings
from media_quotes.governance.restore_drill import verify_export_integrity
from media_quotes.models import ExportQuoteRecord
from media_quotes.text_utils import local_xml_tag

ROOT = Path(__file__).resolve().parents[2]


def _record(quote_id: str, content_hash: str = "a" * 64) -> ExportQuoteRecord:
    source = (ROOT / "data/quotes.example.jsonl").read_text(encoding="utf-8")
    payload = json.loads(source.splitlines()[0])
    return ExportQuoteRecord.model_validate(
        {**payload, "quote_id": quote_id, "content_hash": content_hash}
    )


def test_local_xml_tag_is_shared() -> None:
    assert local_xml_tag("{http://example.com}Item") == "Item"
    assert local_xml_tag("plain") == "plain"


def test_restore_drill_detects_duplicate_ids() -> None:
    first = _record("Q-20261120-0001")
    ok, reasons = verify_export_integrity([first, first], [first])
    assert not ok
    assert any("duplicate_original" in reason for reason in reasons)


def test_blob_prune_skips_referenced_blobs(tmp_path: Path) -> None:
    from media_quotes.governance.blob_prune import prune_blobs

    settings = Settings(_env_file=None, blob_dir=tmp_path / "raw")
    source_dir = settings.blob_dir / "guardian"
    source_dir.mkdir(parents=True)
    digest = "b" * 64
    (source_dir / f"{digest}.html").write_bytes(b"<html/>")
    report = prune_blobs(
        settings,
        today=date(2030, 1, 1),
        tombstones=frozenset({digest}),
        dry_run=True,
        referenced=frozenset({digest}),
    )
    assert report["referenced_kept"] == 1
    assert report["pruned"] == 0
    assert (source_dir / f"{digest}.html").is_file()


def test_logs_tolerate_malformed_event_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from media_quotes.cli.logs import _do_logs

    settings = Settings(_env_file=None, logs_dir=tmp_path)
    (tmp_path / "run1.jsonl").write_text('{"license_reference": "L"}\n', encoding="utf-8")
    assert _do_logs(settings, run_id="run1", trace_id=None) == 0
    assert "unknown" in capsys.readouterr().out


def test_source_routes_merge_csv_and_json(tmp_path: Path) -> None:
    from media_quotes.source_configs import load_routes_from_sources

    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    csv_path = tmp_path / "source_topic_routes.csv"
    csv_path.write_text(
        "source_id,route_id,route_type,discovery_url,topic_rule_id,priority,"
        "fallback_route_id,approved,license_reference,translation_rights_confirmed,notes\n"
        "guardian,csv-route,rss,https://example.invalid/rss,ukraine-war-v1,1,,true,LIC,false,\n"
        "bbc,csv-only,rss,https://bbc.invalid/rss,ukraine-war-v1,1,,true,LIC,false,\n",
        encoding="utf-8",
    )
    merged = load_routes_from_sources(sources_dir, fallback_csv=csv_path)
    assert {route.route_id for route in merged.get("bbc", [])} == {"csv-only"}
    assert any(route.route_id == "csv-route" for route in merged.get("guardian", []))


def test_llm_cache_uses_injected_session_context() -> None:
    from media_quotes.ai.llm_cache import DbDecisionCache
    from media_quotes.ai.llm_live import LLMRequest

    contexts: list[Any] = []

    class Session:
        def __enter__(self) -> Session:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

    class Context:
        def __call__(self, _settings: Settings) -> Session:
            contexts.append(_settings)
            return Session()

    settings = Settings(_env_file=None)
    cache = DbDecisionCache(settings, session_context=Context())  # type: ignore[arg-type]
    request = LLMRequest(
        decision_type="test",
        cache_key="k",
        input_hash="i",
        model_snapshot="m",
        prompt_version="p",
        schema_hash="s",
        system_prompt="s",
        user_prompt="u",
    )
    # No DB is touched: the injected context returns an object without get_decision_cache,
    # so the lookup raises, proving the injected context was used.
    with pytest.raises(AttributeError):
        cache.lookup(request)
    assert contexts == [settings]


def test_obsolescence_threshold_constant_is_used() -> None:
    from media_quotes.risk_controls import obsolescence

    assert obsolescence.triage_obsolescence(obsolescence.JUDGE_CONFIDENCE_THRESHOLD) == "pending"


def test_eval_split_also_writes_canonical_report(tmp_path: Path) -> None:
    from media_quotes.cli.eval import evaluate_gold_set_report

    settings = Settings(
        _env_file=None,
        report_date=date(2026, 11, 19),
        persist_to_postgres=False,
        audit_dir=tmp_path,
        fixtures_dir=ROOT / "data/fixtures",
        sources_csv=ROOT / "data/sources.csv",
        sources_dir=ROOT / "data/sources",
        sections_csv=ROOT / "data/sections.csv",
        schema_path=ROOT / "data/quotes.schema.json",
        gold_set_dir=ROOT / "data/gold_set",
    )
    result = evaluate_gold_set_report(settings, gold_set="synthetic-v1", split="dev")
    assert result["evaluated_split"] == "dev"
    assert (tmp_path / "eval-synthetic-v1-dev.json").is_file()
    assert (tmp_path / "eval-synthetic-v1.json").is_file()


def test_graph_speaker_query_prefers_exact_key() -> None:
    from media_quotes.graph.queries import fetch_speaker_timeline

    captured: dict[str, Any] = {}

    class Session:
        def run(self, query: str, params: dict[str, Any]) -> Any:
            captured["query"] = query
            return SimpleNamespace(data=lambda: [])

    assert fetch_speaker_timeline(Session(), "Jane Doe") == []  # type: ignore[arg-type]
    assert "CASE WHEN sp.speaker_key = $speaker_key" in captured["query"]
