"""Context packs are bounded, deterministic and provenance-bearing."""

from __future__ import annotations

import pytest

from media_quotes.context_pack import build_context_pack
from media_quotes.models import ExportQuoteRecord


def _record(quote_id: str, text: str) -> ExportQuoteRecord:
    return ExportQuoteRecord.model_construct(
        quote_id=quote_id,
        quote_text_original=text,
        source_id="guardian",
        section_id="war_intensity",
        topic_id="ukraine-war",
        content_hash="a" * 64,
    )


def test_context_pack_bounded_and_deterministic() -> None:
    records = [
        _record("Q-20261119-0001", "Talks continue."),
        _record("Q-20261119-0002", "Talks paused."),
    ]
    first = build_context_pack(records, max_tokens=200)
    second = build_context_pack(records, max_tokens=200)
    assert first == second
    assert first["manifest"]["quote_count"] == 2
    assert first["manifest"]["token_estimate"] <= 200
    assert first["manifest"]["quote_ids"] == ["Q-20261119-0001", "Q-20261119-0002"]
    assert first["quotes"][0]["content_hash"] == "a" * 64


def test_context_pack_filters_other_topics_and_rejects_bad_budget() -> None:
    record = _record("Q-20261119-0001", "Talks continue.")
    other = record.model_copy(update={"topic_id": "ai-policy"})
    pack = build_context_pack([record, other], max_tokens=1000, topic_id="ukraine-war")
    assert pack["manifest"]["quote_count"] == 1
    with pytest.raises(ValueError, match="max_tokens"):
        build_context_pack([record], max_tokens=0)


def test_context_pack_skips_whole_quotes_over_budget() -> None:
    records = [_record("Q-20261119-0001", "x" * 40), _record("Q-20261119-0002", "y" * 40)]
    one = build_context_pack([records[0]], max_tokens=10_000)
    budget = one["manifest"]["token_estimate"] + 5
    pack = build_context_pack(records, max_tokens=budget)
    assert pack["manifest"]["quote_count"] == 1
    assert pack["quotes"][0]["quote_text"] == "x" * 40
