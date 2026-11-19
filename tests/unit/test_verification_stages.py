"""C3 — two-stage verifier: pre-LLM filtering and publishable-only output."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest

from media_quotes.adapters import FixtureSource, build_adapter
from media_quotes.models import QuoteCandidate
from media_quotes.registry import load_sources_csv
from media_quotes.verification_stages import PipelineResult, pre_llm_filter, verify_pipeline

ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "data/sources.csv"
FIXTURE = "fixture://reuters/synthetic-reuters-2026-11-18.html"
ROW = load_sources_csv(CSV)[0]  # reuters, blocked but fixture replay is allowed
FIXTURES = FixtureSource(ROOT / "data/fixtures")
ADAPTER = build_adapter(ROW, FIXTURES, replay=True)
META = ADAPTER.parse_metadata(FIXTURES.read(FIXTURE))
TEXT = str(META["article_text"])
FLAGS = dict(ADAPTER.policy_flags()) | {
    "source_id": ROW.source_id,
    "original_reporting_flag": "first-hand",
}


def candidate(quote_text: str, start: int) -> QuoteCandidate:
    return QuoteCandidate(
        quote_text=quote_text,
        article_id=uuid5(NAMESPACE_URL, FIXTURE),
        char_start=start,
        char_end=start + len(quote_text),
        speaker="Analyst",
        attribution_span="said",
    )


def real_candidates() -> list[QuoteCandidate]:
    out = []
    for span in META["quote_spans"]:
        text = str(span["quote_text"])
        start = int(span["char_start"])
        out.append(candidate(text, start))
    return out


def test_stage1_drops_mismatch_short_and_overlimit() -> None:
    good = real_candidates()
    assert good
    bad_span = candidate("Completely fabricated quote for testing.", 0)
    too_long = candidate(" ".join(["word"] * 31), TEXT.find("A"))
    result = pre_llm_filter([*good, bad_span, too_long], text=TEXT, policy_flags=FLAGS)
    assert len(result.kept) == len(good)
    assert result.rejected.get("span_mismatch", 0) >= 1
    assert result.rejected.get("over_word_limit", 0) == 0  # invalid span dropped first


def test_stage1_requires_first_hand() -> None:
    no_flags = dict(FLAGS) | {"original_reporting_flag": "relayed wire copy"}
    result = pre_llm_filter(real_candidates(), text=TEXT, policy_flags=no_flags)
    assert result.kept == []
    assert result.rejected["not_first_hand"] > 0


def test_pipeline_publishable_only_verified() -> None:
    pipeline = verify_pipeline(
        real_candidates(),
        text=TEXT,
        source_row=ROW,
        policy_flags=FLAGS,
        metadata=META,
        window_start=date(2026, 11, 1),
        window_end=date(2026, 11, 30),
    )
    assert isinstance(pipeline, PipelineResult)
    for item in pipeline.results:
        if item.candidate in pipeline.publishable:
            assert item.status == "verified"
    assert len(pipeline.publishable) <= len(pipeline.prefilter.kept)
    for item in pipeline.results:
        if item.status != "verified":
            assert item.candidate not in pipeline.publishable


def test_word_limits_must_be_positive() -> None:
    with pytest.raises(ValueError):
        pre_llm_filter([], text=TEXT, policy_flags=FLAGS, max_quote_words=0)
