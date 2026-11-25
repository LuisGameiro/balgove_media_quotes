"""Behavioral tests for the uncovered helper branches in source_processing.py."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from media_quotes.cli import run
from media_quotes.config import PROJECT_ROOT, Settings
from media_quotes.pipeline import (
    JudgeDecisionSnapshot,
    LanguageDecisionSnapshot,
    TopicAssessment,
    TranslationSnapshot,
)
from media_quotes.registry import SourceRegistryRow, load_sources_csv
from media_quotes.source_processing import (
    TopicGate,
    TopicGateOutcome,
    _article_for_topic,
    _detect_language_safe,
    _path_setting,
    _select_topic_route,
    _setting,
    _topic_event_fields,
    _topic_gate,
    _translation_for_quote,
)
from media_quotes.topics import load_topic_pack

ROUTES_CSV = PROJECT_ROOT / "data" / "source_topic_routes.csv"
RULES_JSON = PROJECT_ROOT / "data" / "topic_rules.json"


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    settings = Settings(_env_file=None, output_dir=tmp_path / "output")
    for name, value in overrides.items():
        object.__setattr__(settings, name, value)
    return settings


def _reuters_row() -> SourceRegistryRow:
    return next(
        row
        for row in load_sources_csv(PROJECT_ROOT / "data" / "sources.csv")
        if row.source_id == "reuters"
    )


def _route(**overrides: object) -> SimpleNamespace:
    values: dict[str, Any] = {
        "source_id": "reuters",
        "route_id": "route-reuters-ukraine-default",
        "topic_rule_id": "ukraine-war-v1",
        "approved": True,
        "translation_rights_confirmed": True,
        "license_reference": "LIC-TEST",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _assessment(**overrides: object) -> TopicAssessment:
    values: dict[str, Any] = {
        "status": "accepted",
        "score": 0.9,
        "matched_terms": ("ukraine",),
        "rule_id": "ukraine-war-v1",
        "rule_version": "1.1.0",
        "reasons": ("deterministic rule accepted",),
        "judge": None,
    }
    values.update(overrides)
    return TopicAssessment(**values)


# ---------------------------------------------------------------------------
# _setting / _path_setting
# ---------------------------------------------------------------------------


def test_setting_reads_present_value_and_default() -> None:
    settings = SimpleNamespace(translation_mode="translate")
    assert _setting(settings, "translation_mode", "off") == "translate"
    assert _setting(settings, "translation_prompt_version", "fallback") == "fallback"


def test_path_setting_handles_none_empty_path_and_string() -> None:
    settings = SimpleNamespace(
        absent=None,
        empty="",
        as_path=Path("data/x.csv"),
        as_str="data/y.csv",
    )
    assert _path_setting(settings, "absent") is None
    assert _path_setting(settings, "empty") is None
    assert _path_setting(settings, "missing") is None
    assert _path_setting(settings, "as_path") is settings.as_path
    assert _path_setting(settings, "as_str") == Path("data/y.csv")


# ---------------------------------------------------------------------------
# _topic_gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("has_routes", "has_rules"),
    [(False, False), (True, False), (False, True)],
)
def test_topic_gate_requires_a_route_and_rule_source(
    tmp_path: Path, has_routes: bool, has_rules: bool
) -> None:
    settings = _settings(
        tmp_path,
        source_topics_csv=ROUTES_CSV if has_routes else None,
        topic_rules_path=RULES_JSON if has_rules else None,
        sources_dir=None,
        topics_dir=None,
    )
    assert _topic_gate(settings) is None


def test_topic_gate_loads_checked_in_routes_and_rules(tmp_path: Path) -> None:
    gate = _topic_gate(_settings(tmp_path))
    assert gate is not None
    assert gate.preferred_rule_id == ""
    route = _select_topic_route(gate, "reuters")
    assert route is not None
    assert route.topic_rule_id == "ukraine-war-v1"
    assert "ukraine-war-v1" in gate.rules


def test_topic_gate_uses_custom_paths_from_cli_run_globals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routes_path = tmp_path / "custom-routes.csv"
    rules_path = tmp_path / "custom-rules.json"
    routes_path.write_text("source_id,route_id\n", encoding="utf-8")
    rules_path.write_text("{}", encoding="utf-8")
    settings = _settings(
        tmp_path,
        source_topics_csv=routes_path,
        topic_rules_path=rules_path,
        sources_dir=None,
        topics_dir=None,
    )
    seen: dict[str, Path] = {}
    custom_route = _route(route_id="custom-route", topic_rule_id="custom-rule")

    def fake_routes(path: Path) -> dict[str, list[SimpleNamespace]]:
        seen["routes"] = path
        return {"custom-source": [custom_route]}

    def fake_rules(path: Path) -> dict[str, dict[str, Any]]:
        seen["rules"] = path
        return {"custom-rule": {"rule_id": "custom-rule"}}

    monkeypatch.setattr(run, "load_source_topic_routes", fake_routes)
    monkeypatch.setattr(run, "load_topic_rules", fake_rules)

    gate = _topic_gate(settings)

    assert seen == {"routes": routes_path, "rules": rules_path}
    assert gate is not None
    assert gate.routes_by_source["custom-source"] == [custom_route]
    assert gate.rules == {"custom-rule": {"rule_id": "custom-rule"}}
    assert gate.preferred_rule_id == ""


@pytest.mark.parametrize("loader", ["routes", "rules"])
@pytest.mark.parametrize("exc_type", [ImportError, FileNotFoundError, ValueError, TypeError])
def test_topic_gate_returns_none_on_loader_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    loader: str,
    exc_type: type[Exception],
) -> None:
    settings = _settings(tmp_path)

    def boom(*args: object, **kwargs: object) -> Any:
        raise exc_type("loader failed")

    target = "load_source_topic_routes" if loader == "routes" else "load_topic_rules"
    monkeypatch.setattr(run, target, boom)
    assert _topic_gate(settings) is None


def test_topic_gate_injects_topic_pack_rule(tmp_path: Path) -> None:
    pack = load_topic_pack("ukraine-war")
    settings = _settings(
        tmp_path,
        sources_dir=None,
        topics_dir=None,
        topic_rules_path=None,
    )
    gate = _topic_gate(settings, topic_pack=pack)
    assert gate is not None
    assert gate.preferred_rule_id == pack.relevance.rule_id == "ukraine-war-v1"
    assert gate.rules[gate.preferred_rule_id] == pack.to_rule_dict()
    assert gate.rules[pack.topic_id] == pack.to_rule_dict()
    route = _select_topic_route(gate, "reuters")
    assert route is not None
    assert route.topic_rule_id == gate.preferred_rule_id


# ---------------------------------------------------------------------------
# _select_topic_route
# ---------------------------------------------------------------------------


def test_select_topic_route_returns_none_without_gate() -> None:
    assert _select_topic_route(None, "reuters") is None


def test_select_topic_route_prefers_matching_approved_rule() -> None:
    gate = TopicGate(
        routes_by_source={
            "reuters": [
                _route(route_id="unapproved", approved=False),
                _route(route_id="other-rule", topic_rule_id="ai-policy-v1"),
                _route(route_id="matching"),
            ]
        },
        rules={},
        preferred_rule_id="ukraine-war-v1",
    )
    selected = _select_topic_route(gate, "reuters")
    assert selected is not None
    assert selected.route_id == "matching"


def test_select_topic_route_preferred_rule_without_match_is_none() -> None:
    gate = TopicGate(
        routes_by_source={"reuters": [_route()]},
        rules={},
        preferred_rule_id="ai-policy-v1",
    )
    assert _select_topic_route(gate, "reuters") is None


def test_select_topic_route_without_preferred_uses_first_approved_rule() -> None:
    gate = TopicGate(
        routes_by_source={
            "reuters": [
                _route(route_id="no-rule", topic_rule_id=""),
                _route(route_id="approved-rule"),
                _route(route_id="unapproved", approved=False),
            ]
        },
        rules={},
    )
    selected = _select_topic_route(gate, "reuters")
    assert selected is not None
    assert selected.route_id == "approved-rule"


def test_select_topic_route_without_preferred_ignores_unapproved_and_unknown() -> None:
    gate = TopicGate(
        routes_by_source={
            "reuters": [_route(approved=False), _route(topic_rule_id="")],
        },
        rules={},
    )
    assert _select_topic_route(gate, "reuters") is None
    assert _select_topic_route(gate, "unknown-source") is None


# ---------------------------------------------------------------------------
# _article_for_topic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["tags", "topics", "keywords", "tags_list"])
def test_article_for_topic_extracts_tags_from_each_key(key: str) -> None:
    metadata = {key: ["Ukraine", " diplomacy "], "language": "en"}
    article = _article_for_topic(
        metadata,
        text="body",
        canonical_url="https://example.invalid/a",
        title="T",
        ref="r1",
    )
    assert article == {
        "article_text": "body",
        "title": "T",
        "canonical_url": "https://example.invalid/a",
        "tags": ["Ukraine", " diplomacy "],
        "article_ref": "r1",
        "language": "en",
    }


def test_article_for_topic_prefers_tags_and_handles_string_and_empty() -> None:
    metadata = {"tags": "Ukraine", "topics": ["ignored"], "keywords": ["ignored"]}
    article = _article_for_topic(metadata, text="t", canonical_url="u", title="ti", ref="r")
    assert article["tags"] == ["Ukraine"]
    empty = _article_for_topic({}, text="t", canonical_url="u", title="ti", ref="r")
    assert empty["tags"] == []
    assert empty["language"] == ""


# ---------------------------------------------------------------------------
# _topic_event_fields
# ---------------------------------------------------------------------------


def test_topic_event_fields_without_judge() -> None:
    row = _reuters_row()
    outcome = TopicGateOutcome(assessment=_assessment(), route=_route())
    fields = _topic_event_fields(outcome, row)
    assert fields == {
        "topic_status": "accepted",
        "topic_rule_id": "ukraine-war-v1",
        "topic_rule_version": "1.1.0",
        "topic_score": 0.9,
        "matched_terms": ["ukraine"],
        "reasons": ["deterministic rule accepted"],
        "route_id": "route-reuters-ukraine-default",
        "translation_rights_confirmed": True,
        "escalated": False,
        "license_reference": row.license_reference,
    }
    assert not any(key.startswith("judge_") for key in fields)


def test_topic_event_fields_with_judge_provenance() -> None:
    judge = JudgeDecisionSnapshot(
        task="topic_uncertain",
        verdict="accept",
        confidence=0.91,
        rationale="within the war-topic band",
        actor="openrouter:model@judge-v1",
        model_snapshot="model",
        prompt_version="judge-v1",
        input_hash="in",
        output_hash="out",
        escalation_reason="",
    )
    outcome = TopicGateOutcome(assessment=_assessment(judge=judge), route=_route())
    fields = _topic_event_fields(outcome, _reuters_row())
    assert {key: fields[key] for key in fields if key.startswith("judge_")} == {
        "judge_actor": judge.actor,
        "judge_verdict": judge.verdict,
        "judge_confidence": judge.confidence,
        "judge_model_snapshot": judge.model_snapshot,
        "judge_prompt_version": judge.prompt_version,
        "judge_input_hash": judge.input_hash,
        "judge_output_hash": judge.output_hash,
    }


def test_topic_event_fields_without_route_uses_fallbacks() -> None:
    row = SimpleNamespace(license_reference="")
    outcome = TopicGateOutcome(assessment=_assessment(), route=None)
    fields = _topic_event_fields(outcome, row)
    assert fields["route_id"] == ""
    assert fields["translation_rights_confirmed"] is False
    assert fields["license_reference"] == "(none)"


# ---------------------------------------------------------------------------
# _detect_language_safe
# ---------------------------------------------------------------------------


def test_detect_language_safe_normalises_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        run,
        "detect_language",
        lambda text: {"language": "uk", "confidence": 0.88, "evidence": "script:cyrillic"},
    )
    assert _detect_language_safe("text") == LanguageDecisionSnapshot(
        language="uk", confidence=0.88, evidence="script:cyrillic"
    )


@pytest.mark.parametrize("exc_type", [ImportError, ValueError, TypeError])
def test_detect_language_safe_falls_back_on_failure(
    monkeypatch: pytest.MonkeyPatch, exc_type: type[Exception]
) -> None:
    def boom(text: str) -> Any:
        raise exc_type("no language model")

    monkeypatch.setattr(run, "detect_language", boom)
    assert _detect_language_safe("text") == LanguageDecisionSnapshot(
        language="", confidence=0.0, evidence="unavailable"
    )


# ---------------------------------------------------------------------------
# _translation_for_quote
# ---------------------------------------------------------------------------


def _translate_kwargs(
    language: LanguageDecisionSnapshot,
    route: SimpleNamespace | None,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "quote_text": "Synthetic quote.",
        "language": language,
        "route": route,
        "run_id": "run-t",
        "trace_id": "trace-t",
        "row": _reuters_row(),
        "events": events,
    }


@pytest.mark.parametrize("mode", ["off", "detect_only"])
def test_translation_skips_when_mode_is_not_translate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    settings = _settings(tmp_path, translation_mode=mode)
    events: list[dict[str, Any]] = []

    def forbidden(*args: object, **kwargs: object) -> Any:
        raise AssertionError("translate_quote must not run")

    monkeypatch.setattr(run, "translate_quote", forbidden)
    kwargs = _translate_kwargs(LanguageDecisionSnapshot("uk", 0.9, "script"), _route(), events)
    assert _translation_for_quote(settings, **kwargs) is None
    assert events == []


@pytest.mark.parametrize("route", [None, _route(translation_rights_confirmed=False)])
def test_translation_skips_without_confirmed_rights(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: SimpleNamespace | None
) -> None:
    settings = _settings(tmp_path, translation_mode="translate")
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(run, "translate_quote", lambda *a, **k: pytest.fail("must not translate"))
    kwargs = _translate_kwargs(LanguageDecisionSnapshot("uk", 0.9, "script"), route, events)
    assert _translation_for_quote(settings, **kwargs) is None
    assert events == []


@pytest.mark.parametrize("language", ["", "en"])
def test_translation_skips_for_english_or_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str
) -> None:
    settings = _settings(tmp_path, translation_mode="translate")
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(run, "translate_quote", lambda *a, **k: pytest.fail("must not translate"))
    snapshot = LanguageDecisionSnapshot(language, 0.9, "script")
    kwargs = _translate_kwargs(snapshot, _route(), events)
    assert _translation_for_quote(settings, **kwargs) is None
    assert events == []


@pytest.mark.parametrize("exc_type", [ImportError, ValueError, TypeError])
def test_translation_failure_records_failed_snapshot_and_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exc_type: type[Exception],
) -> None:
    settings = _settings(tmp_path, translation_mode="translate", translation_prompt_version="v2")
    events: list[dict[str, Any]] = []
    row = _reuters_row()

    def boom(*args: object, **kwargs: object) -> Any:
        raise exc_type("translation backend down")

    monkeypatch.setattr(run, "translate_quote", boom)
    kwargs = _translate_kwargs(LanguageDecisionSnapshot("uk", 0.9, "script"), _route(), events)
    result = _translation_for_quote(settings, **kwargs)

    assert isinstance(result, TranslationSnapshot)
    assert result == TranslationSnapshot(
        text="",
        source_language="uk",
        status="failed",
        model_snapshot="",
        prompt_version="v2",
        content_hash="",
        error="translation backend down",
        usable=False,
    )
    assert len(events) == 1
    event = events[0]
    assert event["step"] == "translation"
    assert event["status"] == "failed"
    assert event["quote_language"] == "uk"
    assert event["translation_status"] == "failed"
    assert event["translation_prompt_version"] == "v2"
    assert event["error"] == "translation backend down"
    assert event["license_reference"] == (row.license_reference or "(none)")
    assert event["run_id"] == "run-t"
    assert event["trace_id"] == "trace-t"
