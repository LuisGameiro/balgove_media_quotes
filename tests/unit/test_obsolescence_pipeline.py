"""Daily pipeline integration for obsolescence proposals and review persistence."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from media_quotes.config import Settings
from media_quotes.judge import JudgeDecision
from media_quotes.models import ExportQuoteRecord, VerificationRecord
from media_quotes.pipeline import run_daily_pipeline
from media_quotes.risk_controls.models import ObsolescenceEvidence, ObsolescenceFlag
from media_quotes.risk_controls.obsolescence import ObsolescenceProposal

ROOT = Path(__file__).resolve().parents[2]
REPORT_DATE = date(2026, 11, 19)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        sources_csv=ROOT / "data/sources.csv",
        sources_dir=ROOT / "data/sources",
        source_topics_csv=ROOT / "data/source_topic_routes.csv",
        topic_rules_path=ROOT / "data/topic_rules.json",
        topics_dir=ROOT / "data/topics",
        sections_csv=ROOT / "data/sections.csv",
        schema_path=ROOT / "data/quotes.schema.json",
        audit_dir=tmp_path / "audit",
        export_dir=tmp_path / "quotes",
        digest_dir=tmp_path / "digests",
        logs_dir=tmp_path / "logs",
        persist_to_postgres=False,
    )


def _record(quote_id: str, text: str, day: date, content_hash: str) -> ExportQuoteRecord:
    now = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    return ExportQuoteRecord(
        quote_id=quote_id,
        quote_text_original=text,
        source_id="guardian",
        source_name="Guardian",
        source_tier=1,
        source_country_region="UK",
        source_type="publicly funded",
        rights_status="license_held",
        access_mode="licensed_database",
        license_reference="LIC-1",
        discovery_method="licensed_database",
        claim_only=False,
        original_reporting_flag="first-hand",
        source_language="en",
        publication_date=day,
        article_name="Article",
        article_author="Jane Reporter",
        article_url=f"https://www.theguardian.com/a/{quote_id}",
        extraction_date=now,
        section_id="war_intensity",
        is_obsolete=False,
        content_hash=content_hash,
        verification=VerificationRecord(
            verbatim_match=True, metadata_verified=True, method="exact", verified_at=now
        ),
    )


class _Provider:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def decide(self, *, task: str, context: Mapping[str, Any]) -> JudgeDecision:
        self.calls.append({"task": task, **dict(context)})
        if self.fail:
            raise RuntimeError("judge unavailable")
        return JudgeDecision(
            task=task,
            verdict="confirm",
            confidence=0.95,
            rationale="The newer first-hand statement supersedes the earlier position.",
            actor="test-judge",
            model_snapshot="test-model",
            prompt_version="judge-v1",
            input_hash="a" * 64,
            output_hash="b" * 64,
        )


def _patch_collection(
    monkeypatch: Any, records: list[ExportQuoteRecord], offsets: dict[str, dict[str, Any]]
) -> None:
    monkeypatch.setattr(
        "media_quotes.cli.run._collect",
        lambda *args, **kwargs: (records, [], [], [], 0, offsets),
    )


def test_daily_pipeline_proposes_merges_and_persists_review_flags(
    tmp_path: Path, monkeypatch: Any
) -> None:
    settings = _settings(tmp_path)
    older = _record(
        "Q-20261118-0001", "Talks will continue tomorrow.", date(2026, 11, 18), "a" * 64
    )
    newer = _record("Q-20261119-0001", "Talks have been paused.", date(2026, 11, 19), "b" * 64)
    existing = ObsolescenceFlag(
        evidence=ObsolescenceEvidence(
            old_quote_id="Q-20261117-0001",
            old_content_hash="c" * 64,
            newer_article_id="https://example.invalid/newer",
            newer_content_hash="d" * 64,
            newer_is_first_hand=True,
            newer_rights_cleared=True,
            superseding_span="A newer statement.",
            decision_id="existing",
            rationale="Already under review.",
        ),
        confidence=0.9,
        state="pending",
    )
    settings.audit_dir.mkdir(parents=True)
    queue_path = settings.audit_dir / "review-queue.json"
    queue_path.write_text(
        json.dumps({"flags": [existing.model_dump(mode="json")]}), encoding="utf-8"
    )
    offsets = {
        older.quote_id: {"speaker": "Alex Morgan"},
        newer.quote_id: {"speaker": "Alex Morgan"},
    }
    _patch_collection(monkeypatch, [older, newer], offsets)
    provider = _Provider()
    monkeypatch.setattr("media_quotes.pipeline.make_judge", lambda _settings: provider)

    result = run_daily_pipeline(settings, report_date=REPORT_DATE)

    assert result.store_path == settings.export_dir / "fixtures" / "quotes-2026-11-19.jsonl"
    assert result.store_path.is_file()
    assert result.digest_path == settings.digest_dir / "fixtures" / "2026-11-19.md"
    assert result.digest_path.is_file()
    assert len(provider.calls) == 1
    assert provider.calls[0]["task"] == "obsolescence_review"
    assert {flag.evidence.old_quote_id for flag in result.review_flags} == {
        existing.evidence.old_quote_id,
        older.quote_id,
    }
    payload = json.loads(queue_path.read_text(encoding="utf-8"))
    assert {flag["evidence"]["old_quote_id"] for flag in payload["flags"]} == {
        existing.evidence.old_quote_id,
        older.quote_id,
    }
    held_at = datetime.combine(REPORT_DATE, datetime.min.time(), tzinfo=UTC)
    assert all(not flag.may_hide_quote(now=held_at) for flag in result.review_flags)


def test_daily_pipeline_records_obsolescence_failure_without_blocking(
    tmp_path: Path, monkeypatch: Any
) -> None:
    settings = _settings(tmp_path)
    older = _record(
        "Q-20261118-0001", "Talks will continue tomorrow.", date(2026, 11, 18), "a" * 64
    )
    newer = _record("Q-20261119-0001", "Talks have been paused.", date(2026, 11, 19), "b" * 64)
    offsets = {
        older.quote_id: {"speaker": "Alex Morgan"},
        newer.quote_id: {"speaker": "Alex Morgan"},
    }
    _patch_collection(monkeypatch, [older, newer], offsets)
    monkeypatch.setattr("media_quotes.pipeline.make_judge", lambda _settings: _Provider(fail=True))

    result = run_daily_pipeline(settings, report_date=REPORT_DATE)

    assert result.review_flags == ()
    assert any(
        event.get("step") == "obsolescence" and event.get("status") == "skipped"
        for event in result.events
    )
    assert not (settings.audit_dir / "review-queue.json").exists()


def test_new_and_interleaved_flags_survive_the_review_queue_merge(
    tmp_path: Path, monkeypatch: Any
) -> None:
    def flag(quote_id: str, state: str = "pending") -> ObsolescenceFlag:
        return ObsolescenceFlag(
            evidence=ObsolescenceEvidence(
                old_quote_id=quote_id,
                old_content_hash="a" * 64,
                newer_article_id="art-2",
                newer_content_hash="b" * 64,
                newer_is_first_hand=True,
                newer_rights_cleared=True,
                superseding_span="A newer official statement.",
                decision_id="dec-1",
                rationale="Superseded by a first-hand statement.",
            ),
            confidence=0.9,
            state=state,
        )

    settings = _settings(tmp_path)
    older = _record(
        "Q-20261118-0001", "Talks will continue tomorrow.", date(2026, 11, 18), "a" * 64
    )
    newer = _record("Q-20261119-0001", "Talks have been paused.", date(2026, 11, 19), "b" * 64)
    offsets = {
        older.quote_id: {"speaker": "Alex Morgan"},
        newer.quote_id: {"speaker": "Alex Morgan"},
    }
    _patch_collection(monkeypatch, [older, newer], offsets)

    settings.audit_dir.mkdir(parents=True)
    queue_path = settings.audit_dir / "review-queue.json"
    lock_path = queue_path.with_name(".review-queue.json.lock")
    interleaved = flag("Q-INTERLEAVED")
    decided = flag("Q-DECIDED", "confirmed")
    proposed = flag("Q-PROPOSED")
    queue_path.write_text(
        json.dumps({"flags": [interleaved.model_dump(mode="json")]}), encoding="utf-8"
    )

    def propose(
        records: Any, *, now: Any, judge: Any, speakers: Any, max_pairs: Any = None
    ) -> ObsolescenceProposal:
        # Judge/provider work must never run while the review queue is locked.
        assert not lock_path.exists()
        payload = json.loads(queue_path.read_text(encoding="utf-8"))
        payload["flags"].append(decided.model_dump(mode="json"))  # another operator's verdict
        queue_path.write_text(json.dumps(payload), encoding="utf-8")
        return ObsolescenceProposal(flags=[proposed], pairs_evaluated=1, pairs_skipped=0)

    monkeypatch.setattr("media_quotes.risk_controls.obsolescence.propose_obsolescence", propose)

    result = run_daily_pipeline(settings, report_date=REPORT_DATE)

    assert result.artifact_scope is None
    assert not lock_path.exists()
    expected = {"Q-INTERLEAVED", "Q-DECIDED", "Q-PROPOSED"}
    assert expected <= {f.evidence.old_quote_id for f in result.review_flags}
    payload = json.loads(queue_path.read_text(encoding="utf-8"))
    states = {item["evidence"]["old_quote_id"]: item["state"] for item in payload["flags"]}
    assert states["Q-DECIDED"] == "confirmed"
    assert expected <= set(states)


def test_paid_shadow_judge_mode_caps_pairs(tmp_path: Path, monkeypatch: Any) -> None:
    settings = _settings(tmp_path)
    settings.ai_judge_mode = "shadow"
    monkeypatch.setattr("media_quotes.pipeline.make_judge", lambda _settings: object())
    captured: dict[str, Any] = {}

    def propose(
        records: Any, *, now: Any, judge: Any, speakers: Any, max_pairs: Any = None
    ) -> ObsolescenceProposal:
        captured["max_pairs"] = max_pairs
        return ObsolescenceProposal()

    monkeypatch.setattr("media_quotes.risk_controls.obsolescence.propose_obsolescence", propose)
    _patch_collection(monkeypatch, [], {})

    run_daily_pipeline(settings, report_date=REPORT_DATE)

    assert captured["max_pairs"] == 20
