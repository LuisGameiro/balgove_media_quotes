from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError

from media_quotes.models import ArticleIn, QuoteCandidate


def test_strict_model_rejects_coercion():
    with pytest.raises(ValidationError):
        ArticleIn(
            canonical_url="https://example.invalid/a",
            title=None,
            publication_date="2026-10-01",  # type: ignore[arg-type]
            extracted_text="text",
            extractor_version="v1",
            content_hash="abc",
        )


def test_article_accepts_typed_date():
    article = ArticleIn(
        canonical_url="https://example.invalid/a",
        title="A",
        publication_date=date(2026, 10, 1),
        extracted_text="text",
        extractor_version="v1",
        content_hash="abc",
    )
    assert article.publication_date == date(2026, 10, 1)


def test_quote_candidate_min_length():
    with pytest.raises(ValidationError):
        QuoteCandidate(
            quote_text="too short",
            article_id=uuid4(),
            char_start=0,
            char_end=9,
        )
