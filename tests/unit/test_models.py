from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError

from media_quotes.export import quote_record
from media_quotes.models import ArticleIn, ExportQuoteRecord, QuoteCandidate


def _export_record(**kwargs: object) -> ExportQuoteRecord:
    base: dict[str, object] = {
        "quote_id": "Q-20261001-0001",
        "quote_text_original": "sample quote",
        "source_id": "scmp",
        "source_name": "South China Morning Post",
        "source_tier": 3,
        "source_country_region": "Hong Kong",
        "source_type": "independent",
        "rights_status": "pending_counsel_review",
        "access_mode": "pending_review",
        "claim_only": False,
        "original_reporting_flag": "first-hand",
        "source_language": "en",
        "publication_date": "2026-10-01",
        "article_name": "Story",
        "article_url": "https://example.invalid/a",
        "extraction_date": "2026-10-02T00:00:00Z",
        "section_id": "war_intensity",
        "is_obsolete": False,
        "content_hash": "0" * 64,
        "verification": {
            "verbatim_match": True,
            "metadata_verified": True,
            "method": "sha256_substring_match_v1",
            "verified_at": "2026-10-02T00:00:00Z",
        },
    }
    base.update(kwargs)
    return quote_record(**base)  # type: ignore[arg-type]


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


def test_export_record_speaker_defaults_to_empty():
    assert _export_record().speaker == ""


def test_export_record_populated_speaker():
    assert _export_record(speaker="Jane Doe").speaker == "Jane Doe"


def test_export_roundtrip_preserves_speaker():
    record = _export_record(speaker="Jane Doe")
    restored = ExportQuoteRecord.model_validate(record.model_dump(mode="json"))
    assert restored.speaker == "Jane Doe"
