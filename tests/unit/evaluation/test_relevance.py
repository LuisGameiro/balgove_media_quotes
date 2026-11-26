"""Pure topic rule evaluation: deterministic, explainable, no network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from media_quotes.governance.relevance import TopicDecision, evaluate_topic, load_topic_rules


def _rule(**overrides: object) -> dict[str, Any]:
    rule: dict[str, Any] = {
        "rule_id": "ukraine-war",
        "version": "2026-11-v1",
        "locales": ["en", "uk", "ru"],
        "anchors": ["ukraine", "russia"],
        "supporting_terms": ["war", "invasion"],
        "exclusions": ["sport", "football"],
        "weights": {"anchor": 2.0, "support": 1.0},
        "accept_threshold": 3.0,
        "uncertain_band": 1.0,
    }
    rule.update(overrides)
    return rule


def _article(
    title: str, *, body: str = "", tags: tuple[str, ...] = (), url: str = ""
) -> dict[str, Any]:
    return {
        "article_text": body,
        "title": title,
        "canonical_url": url,
        "tags": list(tags),
    }


def test_accepted_article_records_evidence(tmp_path: Path) -> None:
    rule_path = tmp_path / "rules.json"
    rule_path.write_text(json.dumps({"ukraine-war": _rule()}), encoding="utf-8")

    rules = load_topic_rules(rule_path)
    decision = evaluate_topic(
        rules["ukraine-war"],
        _article("Russia's war in Ukraine", tags=("Ukraine",)),
    )

    assert isinstance(decision, TopicDecision)
    assert decision.status == "accepted"
    assert decision.rule_id == "ukraine-war"
    assert decision.rule_version == "2026-11-v1"
    assert {"russia", "ukraine", "war"} <= set(decision.matched_terms)
    assert any(reason.startswith("anchor:") for reason in decision.reasons)
    assert decision.score >= 3.0


def test_off_topic_and_substring_false_positives_are_rejected() -> None:
    rule = _rule(anchors=["ros"], supporting_terms=["war"], exclusions=[])
    decision = evaluate_topic(rule, _article("Russian war headlines"))

    assert decision.status == "rejected"
    assert "ros" not in decision.matched_terms
    assert "no_anchor" in decision.reasons


def test_anchor_without_support_is_uncertain() -> None:
    decision = evaluate_topic(_rule(), _article("Ukraine politics briefing"))

    assert decision.status == "uncertain"
    assert "no_supporting_term" in decision.reasons
    assert "ukraine" in decision.matched_terms


def test_exclusions_never_allow_publication() -> None:
    decision = evaluate_topic(
        _rule(), _article("War in Ukraine", body="Football tournament report")
    )

    assert decision.status in {"rejected", "uncertain"}
    assert any(reason.startswith("exclusion:") for reason in decision.reasons)


def test_unicode_casefold_and_word_boundaries() -> None:
    ukrainian_rule = _rule(
        anchors=["україна", "україні"],
        supporting_terms=["війна"],
        exclusions=[],
    )
    decision = evaluate_topic(ukrainian_rule, _article("Війна в Україні"))

    assert decision.status == "accepted"
    assert set(decision.matched_terms) == {"україні", "війна"}


def test_single_rule_document_with_banded_thresholds_loads(tmp_path: Path) -> None:
    rule = _rule(uncertain_band={"lower": 1, "upper": 2.9})
    rule["weights"]["supporting_term"] = 1
    rule_path = tmp_path / "single.json"
    rule_path.write_text(json.dumps(rule), encoding="utf-8")

    rules = load_topic_rules(rule_path)

    assert list(rules) == ["ukraine-war"]
    decision = evaluate_topic(rules["ukraine-war"], _article("War in Ukraine"))
    assert decision.status == "accepted"


def test_rule_files_are_strict_and_decisions_are_replayable(tmp_path: Path) -> None:
    rule_path = tmp_path / "rules.json"
    rule_path.write_text('{"bad": 1}', encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        load_topic_rules(rule_path)

    mismatched = tmp_path / "mismatch.json"
    mismatched.write_text('{"other": ' + json.dumps(_rule()) + "}", encoding="utf-8")
    with pytest.raises(ValueError, match="does not match rule_id"):
        load_topic_rules(mismatched)

    article = _article("Russia's war in Ukraine", body="Sanctions tighten", url="/ukraine")
    assert evaluate_topic(_rule(), article) == evaluate_topic(_rule(), article)
