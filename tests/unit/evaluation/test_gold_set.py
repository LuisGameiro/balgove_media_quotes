"""A2 — versioned gold set load and deterministic 60/20/20 splits."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from media_quotes.governance.gold_set import (
    GoldSet,
    load_gold_set,
    split_articles,
    split_for,
    validate_sections,
)

FIXTURE = Path(__file__).resolve().parents[3] / "data/gold_set/synthetic-v1.json"


def test_load_real_fixture() -> None:
    gold = load_gold_set(FIXTURE)
    assert gold.version == "synthetic-v1"
    assert len(gold.articles) >= 60
    assert len(gold.negative_candidates) >= 10
    assert all(quote.human_label for article in gold.articles for quote in article.quotes)


def test_legacy_gold_set_defaults_to_fixture_provenance() -> None:
    gold = load_gold_set(FIXTURE)
    assert gold.provenance == "fixture"


def test_split_is_deterministic_and_exhaustive() -> None:
    gold = load_gold_set(FIXTURE)
    split = split_articles(gold)
    total = sum(len(group) for group in split.values())
    assert total == len(gold.articles)
    assert len(split["train"]) > 0
    assert len(split["dev"]) > 0
    assert len(split["test"]) > 0
    for article in gold.articles:
        assert split_for(article.article_ref, gold.version) in {"train", "dev", "test"}
    # Repeating with the same version yields identical membership.
    again = split_articles(gold)
    assert {k: [a.article_ref for a in v] for k, v in split.items()} == {
        k: [a.article_ref for a in v] for k, v in again.items()
    }


def test_unknown_section_rejected() -> None:
    good = load_gold_set(FIXTURE)
    payload = good.model_dump()
    payload["articles"][0]["quotes"][0]["section_id"] = "not_a_section"
    with pytest.raises(ValueError, match="unknown section_id"):
        validate_sections(GoldSet.model_validate(payload))


def test_strict_model_rejects_coercion() -> None:
    with pytest.raises(ValidationError):
        GoldSet(
            version="v1",
            articles=[
                {
                    "article_ref": "fixture://x/1.html",
                    "source_id": "reuters",
                    "publication_date": "2026-11-18",
                    "relevant": "true",
                }
            ],
        )
