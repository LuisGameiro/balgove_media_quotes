import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from media_quotes.export import (
    content_hash,
    local_store_path,
    quote_record,
    read_jsonl,
    write_jsonl,
)
from media_quotes.models import ExportQuoteRecord
from media_quotes.text_utils import exact_span_match, normalise_text


def _record(
    quote_id: str = "Q-20261001-0001",
    quote_text: str = "sample quote",
) -> ExportQuoteRecord:
    return quote_record(
        quote_id=quote_id,
        quote_text_original=quote_text,
        source_id="scmp",
        source_name="South China Morning Post",
        source_tier=3,
        source_country_region="Hong Kong",
        source_type="independent",
        rights_status="pending_counsel_review",
        access_mode="pending_review",
        discovery_method="web",
        claim_only=False,
        original_reporting_flag="first-hand",
        source_language="en",
        publication_date="2026-10-01",
        article_name="Story",
        article_url="https://example.invalid/a",
        extraction_date="2026-10-02T00:00:00Z",
        section_id="war_intensity",
        is_obsolete=False,
        content_hash="0" * 64,
        verification={
            "verbatim_match": True,
            "metadata_verified": True,
            "method": "sha256_substring_match_v1",
            "verified_at": "2026-10-02T00:00:00Z",
        },
    )


def test_normalise_text_folds_smart_quotes_and_whitespace():
    assert normalise_text("  “hello”\n\nworld  ") == '"hello" world'


def test_exact_span_match():
    text = "The report says “ceasefire” today."
    quote = 'The report says "ceasefire" today.'
    assert exact_span_match(text, quote, 0, len(text))
    assert not exact_span_match(text, quote, 1, len(text))


def test_content_hash_and_local_store_path(tmp_path: Path):
    h = content_hash(["a", "b", "c"])
    assert len(h) == 64
    assert h == content_hash(["a", "b", "c"])

    store = local_store_path(tmp_path, date(2026, 11, 20))
    assert store == tmp_path / "quotes-2026-11-20.jsonl"


def test_read_jsonl_nonexistent_and_roundtrip(tmp_path: Path):
    missing_path = tmp_path / "missing.jsonl"
    assert read_jsonl(missing_path) == []

    records = [_record()]
    write_path = tmp_path / "roundtrip.jsonl"
    write_jsonl(records, write_path)

    loaded = read_jsonl(write_path)
    assert len(loaded) == 1
    assert loaded[0].quote_id == "Q-20261001-0001"


def test_jsonl_has_one_schema_valid_json_object_per_line(tmp_path: Path):
    path = tmp_path / "quotes.jsonl"
    records = [
        _record(),
        _record("Q-20261001-0002", "另一个引用"),
    ]
    count = write_jsonl(records, path)
    assert count == 2
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    lines = raw.decode("utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["quote_id"] == "Q-20261001-0001"
    assert json.loads(lines[1])["quote_text_original"] == "另一个引用"
    assert set(json.loads(lines[0])) >= {
        "quote_id",
        "source_id",
        "content_hash",
        "verification",
        "section_id",
    }


def test_invalid_id_is_rejected():
    with pytest.raises(ValidationError):
        _record("invalid-id")
