"""Direct tests for public diagnostics helpers backed by the real on-disk configuration.

All helpers here are offline: they read the checked-in CSV/JSON configuration only and
never fetch a feed or hit the network.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from media_quotes.diagnostics import (
    TopicContext,
    _load_source_row,
    detect_article_language,
    discover_sections,
    evaluate_source_topic,
    expected_sections_for_fixture,
    fixture_html_files,
    has_translation_rights,
    is_source_route_approved,
    load_topic_configuration,
)
from media_quotes.enums import SECTION_IDS

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
CONTEXT = load_topic_configuration(
    source_topics_csv=DATA_DIR / "source_topic_routes.csv",
    topic_rules_path=DATA_DIR / "topic_rules.json",
)


def _context_with_guardian_route(**overrides: object) -> TopicContext:
    """Clone the real context with one guardian route tweaked for a single assertion."""
    route = replace(CONTEXT.routes["guardian"][0], **overrides)
    return TopicContext(
        routes={**CONTEXT.routes, "guardian": [route]},
        rules=CONTEXT.rules,
        source_topics_csv=CONTEXT.source_topics_csv,
        topic_rules_path=CONTEXT.topic_rules_path,
        topic_pack=CONTEXT.topic_pack,
    )


def test_has_translation_rights_false_for_real_unconfirmed_routes() -> None:
    # Every checked-in route is approved but deliberately leaves translation rights unconfirmed.
    assert CONTEXT.routes["guardian"]
    assert has_translation_rights("guardian", CONTEXT) is False


def test_has_translation_rights_false_for_unknown_source() -> None:
    assert has_translation_rights("not-a-configured-source", CONTEXT) is False


def test_has_translation_rights_true_when_route_confirmed() -> None:
    context = _context_with_guardian_route(translation_rights_confirmed=True)
    assert has_translation_rights("guardian", context) is True


def test_has_translation_rights_requires_approved_route() -> None:
    context = _context_with_guardian_route(translation_rights_confirmed=True, approved=False)
    assert has_translation_rights("guardian", context) is False


def test_detect_article_language_english_from_real_fixture() -> None:
    lines = (DATA_DIR / "quotes.example.jsonl").read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    decision = detect_article_language(record["quote_text_original"])
    assert decision["language"] == "en"
    assert 0.0 < decision["confidence"] <= 1.0
    assert any("latin" in item or item.startswith("word:") for item in decision["evidence"])


def test_detect_article_language_ukrainian() -> None:
    decision = detect_article_language("Повітряна оборона прискорить поставки цієї зими.")
    assert decision["language"] == "uk"
    assert decision["confidence"] > 0
    assert any("uk" in item for item in decision["evidence"])


def test_detect_article_language_empty_text() -> None:
    assert detect_article_language("   ") == {
        "language": "und",
        "confidence": 0.0,
        "evidence": ["empty_text"],
    }


def test_load_topic_configuration_with_explicit_overrides() -> None:
    context = load_topic_configuration(
        source_topics_csv=DATA_DIR / "source_topic_routes.csv",
        topic_rules_path=DATA_DIR / "topic_rules.json",
    )
    assert context.configured is True
    assert context.source_topics_csv == DATA_DIR / "source_topic_routes.csv"
    assert context.topic_rules_path == DATA_DIR / "topic_rules.json"
    assert context.topic_pack is None
    assert "guardian" in context.routes
    assert "ukraine-war-v1" in context.rules


def test_load_topic_configuration_with_topic_pack_override() -> None:
    context = load_topic_configuration(
        source_topics_csv=DATA_DIR / "source_topic_routes.csv",
        topic_rules_path=DATA_DIR / "topic_rules.json",
        topic_id="ukraine-war",
    )
    assert context.topic_pack is not None
    assert context.topic_pack.topic_id == "ukraine-war"


def test_load_topic_configuration_missing_files_yield_empty_context(tmp_path: Path) -> None:
    context = load_topic_configuration(
        source_topics_csv=tmp_path / "missing-routes.csv",
        topic_rules_path=tmp_path / "missing-rules.json",
    )
    assert context.routes == {}
    assert context.rules == {}
    assert context.configured is False


def test_discover_sections_from_real_catalog() -> None:
    assert discover_sections(DATA_DIR / "sections.csv") == list(SECTION_IDS)


def test_discover_sections_missing_catalog_falls_back(tmp_path: Path) -> None:
    assert discover_sections(tmp_path / "missing.csv") == list(SECTION_IDS)


def test_evaluate_source_topic_accepts_real_topic_article() -> None:
    decision = evaluate_source_topic(
        "guardian",
        {
            "source_id": "guardian",
            "title": "Ukraine air defence and ceasefire talks",
            "text": (
                "Ukraine and its partners discussed air defence and ceasefire negotiations "
                "in Kyiv as Russian missile strikes continued. New sanctions were announced."
            ),
            "tags": ["Ukraine war"],
            "url": "https://guardian.invalid/ukraine-air-defence",
        },
        CONTEXT,
    )
    assert decision["status"] == "accepted"
    assert decision["source_id"] == "guardian"
    assert decision["rule_id"] == "ukraine-war-v1"
    assert decision["rule_found"] is True
    assert decision["route_approved"] is True
    assert decision["matched_terms"]


def test_evaluate_source_topic_unknown_source_has_no_route() -> None:
    decision = evaluate_source_topic(
        "not-a-configured-source",
        {
            "title": "Ukraine strikes near Kyiv",
            "text": "Ukraine and Kyiv faced missile strikes and new sanctions.",
            "url": "https://fixture.invalid/unknown",
        },
        CONTEXT,
    )
    assert decision["source_id"] == "not-a-configured-source"
    assert decision["route_id"] == ""
    assert decision["rule_found"] is False
    assert decision["route_approved"] is False


def test_is_source_route_approved_requires_usable_row() -> None:
    assert is_source_route_approved("guardian", CONTEXT) is True
    assert is_source_route_approved("guardian", CONTEXT, SimpleNamespace(usable=False)) is False
    assert is_source_route_approved("not-a-configured-source", CONTEXT) is False


def test_is_source_route_approved_without_routes() -> None:
    empty = TopicContext(
        routes={},
        rules={},
        source_topics_csv=DATA_DIR / "source_topic_routes.csv",
        topic_rules_path=DATA_DIR / "topic_rules.json",
    )
    assert empty.configured is False
    assert is_source_route_approved("any-source", empty) is True


def test_expected_sections_for_fixture_declares_section() -> None:
    path = DATA_DIR / "fixtures" / "reuters" / "synthetic-reuters-topic-sanctions.html"
    assert "sanctions" in expected_sections_for_fixture(path)
    assert expected_sections_for_fixture(path, ["war_intensity"]) == []


def test_expected_sections_for_fixture_missing_file(tmp_path: Path) -> None:
    assert expected_sections_for_fixture(tmp_path / "absent.html") == []


def test_fixture_html_files_from_real_data() -> None:
    files = fixture_html_files("reuters")
    assert files
    assert files == sorted(files)
    assert all(path.suffix == ".html" for path in files)
    assert fixture_html_files("not-a-configured-source") == []


def test_unknown_source_row_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unknown source_id"):
        _load_source_row("not-a-configured-source")
