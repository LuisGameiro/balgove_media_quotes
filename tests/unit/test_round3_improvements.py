"""Unit tests for Round 3 deep hardening, completeness, and code improvements."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from media_quotes.adapters.html_extractor import (
    _params_from_hints,
)
from media_quotes.cli.common import _digest_path
from media_quotes.cli.eval import evaluate_gold_set_report
from media_quotes.cli.maintenance import _do_restore_drill
from media_quotes.config import Settings
from media_quotes.dashboard import _latest_audit, latest_digests
from media_quotes.extraction import ExtractionParams, extract_article, find_span
from media_quotes.source_configs import (
    ExtractorHints,
    SourceConfig,
    SourceRouteConfig,
)
from media_quotes.source_topics import SourceTopicRoute, validate_topic_configuration
from media_quotes.topics import (
    TopicPack,
    TopicRelevanceConfig,
    TopicSection,
)


def _make_dummy_topic_pack() -> TopicPack:
    return TopicPack(
        topic_id="ai-policy",
        name="AI Policy",
        description="Monitoring AI policy developments.",
        version="1.0.0",
        default_section="other",
        sections=[
            TopicSection(
                section_id="ethics",
                section_name="Ethics & Safety",
                keywords=["safety", "alignment", "ethics"],
            ),
            TopicSection(
                section_id="other",
                section_name="Other",
                keywords=[],
            ),
        ],
        relevance=TopicRelevanceConfig(
            rule_id="ai-policy-v1",
            version="1.0.0",
            locales=["en"],
            anchors={"en": ["ai", "artificial intelligence"]},
            supporting_terms={"en": ["policy", "regulation", "law", "governance"]},
            exclusions={"en": ["game", "sports", "entertainment"]},
        ),
        quick_keywords=["ai"],
    )


def test_gold_set_slug_path_traversal_rejected(tmp_path: Path) -> None:
    settings = Settings(
        gold_set_dir=tmp_path,
        audit_dir=tmp_path,
        report_date=date(2026, 11, 20),
    )
    with pytest.raises(ValueError, match="invalid gold_set"):
        evaluate_gold_set_report(settings, gold_set="../escape")
    with pytest.raises(ValueError, match="invalid gold_set"):
        evaluate_gold_set_report(settings, gold_set="invalid/slug")


def test_eval_uses_default_session_context(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = Settings(
        audit_dir=tmp_path,
        report_date=date(2026, 11, 20),
        persist_to_postgres=True,
    )

    session_opened = False

    class FakeSession:
        def add(self, item: Any) -> None:
            pass

        def commit(self) -> None:
            pass

        def execute(self, statement: Any) -> Any:
            result = MagicMock()
            result.scalars.return_value.all.return_value = []
            return result

    @contextmanager
    def fake_session_context(s: Settings) -> Any:
        nonlocal session_opened
        session_opened = True
        yield FakeSession()

    monkeypatch.setattr("media_quotes.pipeline._default_session_context", fake_session_context)
    monkeypatch.setattr("media_quotes.db.repository.sync_gold_set_labels", lambda session, data: 0)
    monkeypatch.setattr(
        "media_quotes.db.repository.record_eval_run",
        lambda session, **kwargs: 1,
    )

    # Use the existing synthetic-v1 gold set
    metrics = evaluate_gold_set_report(settings, gold_set="synthetic-v1")
    assert session_opened is True
    assert metrics["gold_set_version"] == "synthetic-v1"


def test_digest_path_slug_validation(tmp_path: Path) -> None:
    settings = Settings(digest_dir=tmp_path)
    # Valid topic
    p = _digest_path(settings, date(2026, 11, 20), topic_id="ai-policy")
    assert p == tmp_path / "fixtures" / "ai-policy" / "2026-11-20.md"

    # Default topic
    p_default = _digest_path(settings, date(2026, 11, 20), topic_id="ukraine-war")
    assert p_default == tmp_path / "fixtures" / "2026-11-20.md"

    # Path traversal rejected
    with pytest.raises(ValueError, match="invalid topic_id"):
        _digest_path(settings, date(2026, 11, 20), topic_id="../escape")


def test_restore_drill_topic_validation(tmp_path: Path) -> None:
    settings = Settings(export_dir=tmp_path)
    with pytest.raises(ValueError, match="invalid topic_id"):
        _do_restore_drill(settings, topic_id="../escape")


def test_dashboard_topic_safe_id_guard(tmp_path: Path) -> None:
    # Invalid topic returns empty list/dict without error or traversal
    assert latest_digests(tmp_path, topic_id="../escape") == []
    assert _latest_audit(tmp_path, topic_id="../escape") == {}


def test_topic_pack_word_boundary_matching() -> None:
    pack = _make_dummy_topic_pack()

    # "said" or "against" contains "ai" as substring, but word boundary matching must not trigger
    off_topic = "The prime minister said he was against the proposal in parliament today."
    assert pack.is_relevant(off_topic) is False

    # Standalone "AI" is an anchor term
    on_topic = "The prime minister discussed new AI governance and policy today."
    assert pack.is_relevant(on_topic) is True

    # Anchor present but exclusion also present:
    # "ai" + "game" (exclusion) -> delegates to evaluate_article
    excluded = "The company showed a new AI video game at the sports event."
    assert pack.is_relevant(excluded) is False


def test_topic_pack_infer_section_word_boundary() -> None:
    pack = _make_dummy_topic_pack()
    # "alignment" matches ethics section
    assert pack.infer_section("We need strict alignment of artificial intelligence.") == "ethics"
    # Word boundary: "ethics" matches
    assert pack.infer_section("Research on ethics and governance is key.") == "ethics"
    # Unrelated text falls back to default
    assert pack.infer_section("A simple quote about nothing in particular.") == "other"


def test_extractor_hints_mapping_and_min_chars(tmp_path: Path) -> None:
    hints = ExtractorHints(
        strip_selectors=["//div[@class='ad']"],
        min_text_length=300,
        favor_precision=False,
        favor_recall=False,
    )
    # When neither precision nor recall explicitly forced, defaults to precision=True
    params = _params_from_hints(hints)
    assert params.favor_precision is True
    assert params.favor_recall is False
    assert params.prune_xpath == ("//div[@class='ad']",)

    # When favor_recall is True
    hints_recall = ExtractorHints(favor_recall=True)
    params_recall = _params_from_hints(hints_recall)
    assert params_recall.favor_recall is True
    assert params_recall.favor_precision is False

    # Check that extract_article accepts min_chars
    html = (
        "<html><body><p>"
        + ("Hello world this is a test article body. " * 15)
        + "</p></body></html>"
    )
    # min_chars=100 should easily pass quality gate
    res = extract_article(
        html, params=ExtractionParams(), source_url="https://example.com/test", min_chars=100
    )
    assert res.quality_ok is True


def test_find_span_advancing_offset() -> None:
    text = (
        "The president said 'peace is possible' early on, and repeated 'peace is possible' later."
    )
    target = "peace is possible"

    # First occurrence
    span1 = find_span(text, target)
    assert span1 is not None
    assert span1[0] == 20
    assert span1[1] == 20 + len(target)

    # Second occurrence using start_from
    span2 = find_span(text, target, start_from=span1[1])
    assert span2 is not None
    assert span2[0] > span1[1]
    assert text[span2[0] : span2[1]] == target


def test_source_config_discovery_feed_url_unapproved_skipped() -> None:
    config = SourceConfig(
        source_id="testsource",
        source_name="Test Source",
        homepage_url="https://example.com",
        feed_url="",
        tier=3,
        cadence_hours=24,
        country_region="Global",
        source_type="independent",
        language="en",
        rights_status="license_held",
        access_mode="official_api",
        license_reference="LIC-123",
        discovery_method="rss",
        archive_start_date=date(2026, 1, 1),
        enabled=True,
        routes=[
            SourceRouteConfig(
                route_id="unapproved-rss",
                route_type="topic_rss",
                discovery_url="https://example.com/unapproved-feed.xml",
                topic_rule_id="ukraine-war-v1",
                topic_id="ukraine-war",
                priority=10,
                approved=False,  # Unapproved route
            ),
            SourceRouteConfig(
                route_id="approved-rss",
                route_type="topic_rss",
                discovery_url="https://example.com/approved-feed.xml",
                topic_rule_id="ukraine-war-v1",
                topic_id="ukraine-war",
                priority=20,
                approved=True,  # Approved route
            ),
        ],
    )
    # Should skip unapproved-rss and return approved-rss
    feed = config.discovery_feed_url("ukraine-war")
    assert feed == "https://example.com/approved-feed.xml"


def test_validate_topic_configuration_cyclic_fallback_detection() -> None:
    # 1. Self-cycle: A -> A
    self_loop_routes = {
        "source1": [
            SourceTopicRoute(
                source_id="source1",
                route_id="routeA",
                route_type="web",
                discovery_url="https://example.com/a",
                topic_rule_id="rule1",
                priority=10,
                fallback_route_id="routeA",  # Self-loop!
                approved=True,
                license_reference="LIC-1",
                translation_rights_confirmed=False,
                notes="",
            )
        ]
    }
    rules = {"rule1": {"rule_id": "rule1"}}
    dummy_row = SimpleNamespace(source_id="source1", usable=False)
    errors = validate_topic_configuration(self_loop_routes, rules, [dummy_row])
    assert any("cyclic fallback route chain" in err for err in errors)

    # 2. Multi-hop cycle: A -> B -> A
    cycle_routes = {
        "source1": [
            SourceTopicRoute(
                source_id="source1",
                route_id="routeA",
                route_type="web",
                discovery_url="https://example.com/a",
                topic_rule_id="rule1",
                priority=10,
                fallback_route_id="routeB",
                approved=True,
                license_reference="LIC-1",
                translation_rights_confirmed=False,
                notes="",
            ),
            SourceTopicRoute(
                source_id="source1",
                route_id="routeB",
                route_type="web",
                discovery_url="https://example.com/b",
                topic_rule_id="rule1",
                priority=20,
                fallback_route_id="routeA",  # Cycle back to A!
                approved=True,
                license_reference="LIC-1",
                translation_rights_confirmed=False,
                notes="",
            ),
        ]
    }
    errors2 = validate_topic_configuration(cycle_routes, rules, [dummy_row])
    assert any("cyclic fallback route chain" in err for err in errors2)
