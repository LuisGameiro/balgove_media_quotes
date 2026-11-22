"""Topic gate, language fields and AI-judge review integration (docs/16)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from media_quotes.cli import maintenance, run
from media_quotes.config import Settings
from media_quotes.judge import JudgeDecision
from media_quotes.pipeline import TopicAssessment, assess_topic
from media_quotes.registry import SourceRegistryRow, load_sources_csv
from media_quotes.risk_controls.models import ObsolescenceEvidence, ObsolescenceFlag

ROOT = Path(__file__).resolve().parents[2]
REPORT_DATE = date(2026, 11, 20)
QUOTE = "Synthetic officials stated that emergency talks continued across the region overnight."


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    settings = Settings(
        sources_csv=ROOT / "data/sources.csv",
        sections_csv=ROOT / "data/sections.csv",
        schema_path=ROOT / "data/quotes.schema.json",
        fixtures_dir=ROOT / "data/fixtures",
        gold_set_dir=ROOT / "data/gold_set",
        audit_dir=tmp_path / "audit",
        export_dir=tmp_path / "export",
        logs_dir=tmp_path / "logs",
        persist_to_postgres=False,
    )
    for name, value in overrides.items():
        object.__setattr__(settings, name, value)
    return settings


def _row() -> SourceRegistryRow:
    row = next(
        item for item in load_sources_csv(ROOT / "data/sources.csv") if item.source_id == "reuters"
    )
    return row


@dataclass(frozen=True, slots=True)
class FakeRoute:
    source_id: str = "reuters"
    route_id: str = "fake-route"
    topic_rule_id: str = "rule-v1"
    approved: bool = True
    translation_rights_confirmed: bool = True
    license_reference: str = "LIC-SYNTHETIC"


@dataclass(frozen=True, slots=True)
class FakeDecision:
    status: str = "accepted"
    score: float = 0.9
    matched_terms: tuple[str, ...] = ("ukraine",)
    rule_id: str = "rule-v1"
    rule_version: str = "v1"
    reasons: tuple[str, ...] = ("deterministic rule accepted",)


@dataclass(frozen=True, slots=True)
class FakeLanguage:
    language: str = "uk"
    confidence: float = 0.91
    evidence: tuple[str, ...] = ("script:cyrillic",)


@dataclass(frozen=True, slots=True)
class FakeTranslation:
    text: str = "Synthetic English translation of the fixture quote."
    source_language: str = "uk"
    status: str = "translated"
    model_snapshot: str = "pinned-model"
    prompt_version: str = "translation-v1"
    content_hash: str = "a" * 64
    error: str | None = None


class FakeAdapter:
    mode = "fixture_replay"

    def __init__(self, row: SourceRegistryRow) -> None:
        self._row = row

    def discover(self, window_start: date, report_date: date) -> list[str]:
        return ["fixture://reuters/synthetic-off-topic.json"]

    def fetch(self, ref: str) -> bytes:
        return b"synthetic fixture bytes"

    def parse_metadata(self, raw: bytes) -> dict[str, Any]:
        prefix = "Synthetic Official said: "
        text = prefix + QUOTE + " The fixture report continued."
        return {
            "article_text": text,
            "canonical_url": "https://example.invalid/topic-gate-test",
            "title": "Synthetic topic gate article",
            "byline": "Fixture Reporter",
            "publication_date": REPORT_DATE.isoformat(),
            "tags": ["Ukraine"],
            "quote_spans": [
                {
                    "quote_text": QUOTE,
                    "char_start": len(prefix),
                    "char_end": len(prefix) + len(QUOTE),
                    "section_id": "war_intensity",
                    "speaker": "Synthetic Official",
                    "attribution_span": "Synthetic Official said",
                }
            ],
        }

    def policy_flags(self) -> dict[str, Any]:
        return dict(self._row.policy_flags)

    def extract_quotes(self, text: str) -> list[dict[str, Any]]:
        start = text.find(QUOTE)
        return [{"quote_text": QUOTE, "char_start": start, "char_end": start + len(QUOTE)}]


def _patch_adapter(monkeypatch: Any, row: SourceRegistryRow) -> None:
    monkeypatch.setattr(run, "build_adapter", lambda *args, **kwargs: FakeAdapter(row))


def _patch_gate(monkeypatch: Any, status: str = "rejected") -> None:
    monkeypatch.setattr(run, "load_source_topic_routes", lambda path: {"reuters": [FakeRoute()]})
    monkeypatch.setattr(run, "load_topic_rules", lambda path: {"rule-v1": {}})
    monkeypatch.setattr(
        run,
        "assess_topic",
        lambda rule, article, *, judge=None: TopicAssessment(
            status=status,  # type: ignore[arg-type]
            score=0.75,
            matched_terms=("ukraine",),
            rule_id="rule-v1",
            rule_version="v1",
            reasons=(f"fixture {status}",),
        ),
    )


def _process(settings: Settings) -> tuple[list[Any], list[dict[str, Any]], list[dict[str, Any]]]:
    records, _coverage, gaps, events, rejected, _offsets = run._process_source(
        _row(),
        settings=settings,
        run_id="run-topic",
        window_start=REPORT_DATE - timedelta(days=13),
        report_date=REPORT_DATE,
        used_quote_ids=set(),
    )
    assert rejected >= 0
    return records, gaps, events


def test_off_topic_article_creates_no_records_and_a_coverage_gap(
    tmp_path: Path, monkeypatch: Any
) -> None:
    settings = _settings(tmp_path)
    _patch_adapter(monkeypatch, _row())
    _patch_gate(monkeypatch, "rejected")

    records, gaps, events = _process(settings)

    assert records == []
    gate_events = [event for event in events if event["step"] == "topic_gate"]
    assert len(gate_events) == 1
    assert gate_events[0]["status"] == "rejected"
    assert gate_events[0]["reasons"] == ["fixture rejected"]
    assert gate_events[0]["topic_rule_version"] == "v1"
    assert any(gap.get("topic_status") == "rejected" for gap in gaps)
    assert not any(event["step"] == "verify" and event["status"] == "verified" for event in events)


def test_topic_judge_boundary(monkeypatch: Any) -> None:
    rule: dict[str, Any] = {"rule_id": "r"}
    article = {"article_text": QUOTE, "title": "t", "canonical_url": "u", "tags": []}

    def fake_topic(*args: object, **kwargs: object) -> FakeDecision:
        return FakeDecision(status="rejected")

    monkeypatch.setattr("media_quotes.pipeline.evaluate_topic", fake_topic)

    def forbidden_judge(*args: object, **kwargs: object) -> JudgeDecision:
        raise AssertionError("judge must not override a deterministic rejection")

    monkeypatch.setattr("media_quotes.pipeline.ask_judge", forbidden_judge)
    rejected = assess_topic(rule, article, judge=forbidden_judge)
    assert rejected.status == "rejected"
    assert rejected.judge is None

    monkeypatch.setattr(
        "media_quotes.pipeline.evaluate_topic",
        lambda *a, **k: FakeDecision(status="uncertain"),
    )
    accepted_judge = JudgeDecision(
        task="topic_uncertain",
        verdict="accept",
        confidence=0.91,
        rationale="within the war-topic band",
        actor="openrouter:model@judge-v1",
        model_snapshot="model",
        prompt_version="judge-v1",
        input_hash="in",
        output_hash="out",
    )
    monkeypatch.setattr("media_quotes.pipeline.ask_judge", lambda *a, **k: accepted_judge)
    accepted = assess_topic(rule, article, judge=object())
    assert accepted.status == "accepted"
    assert accepted.judge is not None
    assert accepted.judge.confidence == 0.91

    refusing_judge = replace(
        accepted_judge, verdict="reject", confidence=0.3, escalation_reason="not_in_band"
    )
    monkeypatch.setattr("media_quotes.pipeline.ask_judge", lambda *a, **k: refusing_judge)
    escalated = assess_topic(rule, article, judge=object())
    assert escalated.status == "rejected"
    assert escalated.escalated is True


def test_language_and_translation_fields_are_stored(tmp_path: Path, monkeypatch: Any) -> None:
    settings = _settings(
        tmp_path,
        translation_mode="translate",
        translation_prompt_version="translation-v1",
    )
    _patch_adapter(monkeypatch, _row())
    _patch_gate(monkeypatch, "accepted")
    monkeypatch.setattr(run, "detect_language", lambda text: FakeLanguage())
    calls: list[dict[str, Any]] = []

    def fake_translate(text: str, **kwargs: object) -> FakeTranslation:
        calls.append({"text": text, **kwargs})
        return FakeTranslation()

    monkeypatch.setattr(run, "translate_quote", fake_translate)

    records, gaps, events = _process(settings)

    assert len(records) == 1
    assert not any(gap.get("topic_status") for gap in gaps)
    record = records[0]
    assert record.quote_language == "uk"
    assert record.language_confidence == 0.91
    assert record.quote_text_en == FakeTranslation().text
    assert record.translation_status == "translated"
    assert record.translation_hash == "a" * 64
    assert record.translation_model_snapshot == "pinned-model"
    assert record.translation_prompt_version == "translation-v1"
    assert record.topic_status == "accepted"
    assert record.topic_rule_version == "v1"
    assert record.source_language == _row().language
    assert len(calls) == 1
    assert calls[0]["source_language"] == "uk"
    assert any(event["step"] == "translation" for event in events)


@dataclass(frozen=True, slots=True)
class FakeJudgeProvider:
    decision: JudgeDecision
    tasks: list[str] = field(default_factory=list)

    def decide(self, *, task: str, context: Mapping[str, Any]) -> JudgeDecision:
        self.tasks.append(task)
        return self.decision


def _decision(**overrides: object) -> JudgeDecision:
    values: dict[str, Any] = {
        "task": "obsolescence_review",
        "verdict": "accept",
        "confidence": 0.95,
        "rationale": "newer first-hand statement supersedes the old quote",
        "actor": "openrouter:pinned-model@judge-v1",
        "model_snapshot": "pinned-model",
        "prompt_version": "judge-v1",
        "input_hash": "in",
        "output_hash": "out",
        "escalation_reason": "",
    }
    values.update(overrides)
    return JudgeDecision(**values)


def _queue(settings: Settings, quote_id: str = "Q-20261118-0001") -> Path:
    settings.audit_dir.mkdir(parents=True, exist_ok=True)
    flag = ObsolescenceFlag(
        evidence=ObsolescenceEvidence(
            old_quote_id=quote_id,
            old_content_hash="a" * 64,
            newer_article_id="newer-article",
            newer_content_hash="b" * 64,
            newer_is_first_hand=True,
            newer_rights_cleared=True,
            superseding_span="A newer statement replaces the earlier position.",
            decision_id="dec-1",
            rationale="primary evidence",
        ),
        confidence=0.9,
        state="pending",
    )
    queue_path = settings.audit_dir / "review-queue.json"
    queue_path.write_text(json.dumps({"flags": [flag.model_dump(mode="json")]}), encoding="utf-8")
    return queue_path


def test_review_decision_mode_uses_judge_provenance(tmp_path: Path, monkeypatch: Any) -> None:
    settings = _settings(tmp_path, ai_judge_mode="decision")
    queue_path = _queue(settings)
    provider = FakeJudgeProvider(_decision())
    monkeypatch.setattr(maintenance, "make_judge", lambda _settings: provider)

    assert (
        maintenance._do_review(
            settings, action="decide", quote_id="Q-20261118-0001", verdict="rejected"
        )
        == 0
    )
    payload = json.loads(queue_path.read_text(encoding="utf-8"))
    flag = payload["flags"][0]
    assert provider.tasks == ["obsolescence_review"]
    assert flag["state"] == "confirmed"
    assert flag["reviewer"] == "openrouter:pinned-model@judge-v1"
    assert flag["reason_code"] == "ai-judge"
    assert flag["confidence"] == 0.95
    assert "openrouter:pinned-model@judge-v1" in flag["evidence"]["rationale"]


def test_review_decide_judges_only_the_chosen_flag(tmp_path: Path, monkeypatch: Any) -> None:
    settings = _settings(tmp_path, ai_judge_mode="decision")
    queue_path = _queue(settings)
    payload = json.loads(queue_path.read_text(encoding="utf-8"))
    base_flag = json.loads(json.dumps(payload["flags"][0]))
    for index in (2, 3):
        extra = json.loads(json.dumps(base_flag))
        extra["evidence"]["old_quote_id"] = f"Q-20261118-000{index}"
        payload["flags"].append(extra)
    queue_path.write_text(json.dumps(payload), encoding="utf-8")
    provider = FakeJudgeProvider(_decision())
    monkeypatch.setattr(maintenance, "make_judge", lambda _settings: provider)

    assert (
        maintenance._do_review(
            settings, action="decide", quote_id="Q-20261118-0002", verdict="rejected"
        )
        == 0
    )
    assert provider.tasks == ["obsolescence_review"]


def test_review_low_confidence_escalates(tmp_path: Path, monkeypatch: Any) -> None:
    settings = _settings(tmp_path, ai_judge_mode="decision")
    queue_path = _queue(settings)
    provider = FakeJudgeProvider(_decision(confidence=0.3, verdict="accept"))
    monkeypatch.setattr(maintenance, "make_judge", lambda _settings: provider)

    assert maintenance._do_review(settings, action="decide", quote_id="Q-20261118-0001") == 0
    payload = json.loads(queue_path.read_text(encoding="utf-8"))
    assert payload["flags"][0]["state"] == "escalated"
    assert payload["flags"][0]["reason_code"] == "ai-judge"


def test_review_advisory_mode_keeps_human_decision(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    settings = _settings(tmp_path, ai_judge_mode="advisory")
    queue_path = _queue(settings)
    provider = FakeJudgeProvider(_decision())
    monkeypatch.setattr(maintenance, "make_judge", lambda _settings: provider)

    assert (
        maintenance._do_review(
            settings, action="decide", quote_id="Q-20261118-0001", verdict="rejected"
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "ai-judge advisory" in out
    payload = json.loads(queue_path.read_text(encoding="utf-8"))
    assert payload["flags"][0]["state"] == "rejected"
    assert payload["flags"][0]["reason_code"] == "human-review"
    assert provider.tasks == ["obsolescence_review"]


def test_seed_and_list_still_work_when_judge_is_off(tmp_path: Path, capsys: Any) -> None:
    settings = _settings(tmp_path)
    settings.audit_dir.mkdir(parents=True, exist_ok=True)

    assert maintenance._do_review(settings, action="seed") == 0
    assert maintenance._do_review(settings, action="list") == 0
    payload = json.loads((settings.audit_dir / "review-queue.json").read_text(encoding="utf-8"))
    assert payload["flags"][0]["state"] == "pending"
    assert "ai_judge" not in capsys.readouterr().out
