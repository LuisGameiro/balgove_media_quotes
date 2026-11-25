"""Pure topic rule evaluation: deterministic, explainable, no network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from media_quotes.relevance import TopicDecision, evaluate_topic, load_topic_rules
from media_quotes.topics import TopicPack, TopicRelevanceConfig, TopicSection, load_topic_pack

_TOPICS_DIR = Path(__file__).resolve().parents[2] / "data" / "topics"


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


def test_hard_exclusion_rejects_ice_hockey_despite_ukraine_russia_anchors() -> None:
    rule = _rule(
        anchors=["ukraine", "russia"],
        supporting_terms=["war"],
        exclusions=[],
        hard_exclusions=["ice hockey", "nhl"],
        weights={"anchor": 3.0, "support": 1.0},
        accept_threshold=2.0,
    )
    decision = evaluate_topic(
        rule,
        _article(
            "Ukraine and Russia meet in ice hockey final",
            body="Kyiv and Moscow followed the ice hockey match closely.",
        ),
    )

    assert decision.status == "rejected"
    assert decision.score == 0.0
    assert "hard_exclusion" in decision.reasons
    assert "ice hockey" in decision.matched_terms


def test_soft_exclusion_only_downgrades_not_hard_rejects() -> None:
    rule = load_topic_pack("ukraine-war", _TOPICS_DIR).to_rule_dict()
    decision = evaluate_topic(
        rule,
        _article("Ukraine war update", body="Aid to Gaza was discussed alongside the war."),
    )

    assert decision.status == "uncertain"
    assert "exclusion_downgrades" in decision.reasons
    assert "hard_exclusion" not in decision.reasons
    # The built-in pack keeps other conflicts soft and moves sport/entertainment to hard.
    assert "gaza" in rule["exclusions"]["en"]
    assert "gaza" not in rule["hard_exclusions"]["en"]
    assert "premier league" in rule["hard_exclusions"]["en"]


def _section_test_pack() -> TopicPack:
    return TopicPack(
        topic_id="ukraine-war",
        name="Ukraine War",
        default_section="other",
        sections=[
            TopicSection(
                section_id="war_intensity",
                section_name="War Intensity",
                keywords=["strike"],
            ),
            TopicSection(
                section_id="peace_initiatives",
                section_name="Peace Initiatives",
                keywords=["halt in strikes"],
            ),
            TopicSection(section_id="aid", section_name="Aid", keywords=["aid"]),
            TopicSection(section_id="other", section_name="Other", keywords=[]),
        ],
        relevance=TopicRelevanceConfig(
            rule_id="ukraine-war-v1",
            anchors={"en": ["ukraine"]},
            supporting_terms={"en": ["war"]},
        ),
    )


def test_infer_section_uses_strongest_weighted_match_deterministically() -> None:
    pack = _section_test_pack()
    text = "A strike was followed by a halt in strikes."

    # "halt in strikes" (3 words) outweighs the single-word "strike" match.
    assert pack.infer_section(text) == "peace_initiatives"
    assert pack.infer_section("halt in strikes after the strike") == "peace_initiatives"
    assert pack.infer_section(text) == pack.infer_section(text)

    # Equal one-word matches tie, so the section declared first wins.
    assert pack.infer_section("aid and a strike") == "war_intensity"
