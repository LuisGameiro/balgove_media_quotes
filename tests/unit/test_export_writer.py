"""Export writer behaviour beyond record construction."""

from __future__ import annotations

import json

from media_quotes.export import quote_record, write_jsonl


def _record() -> object:
    return quote_record(
        quote_id="Q-20261001-0001",
        quote_text_original="sample quote",
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


def test_write_jsonl_overwrites_existing_file(tmp_path) -> None:
    path = tmp_path / "quotes.jsonl"
    path.write_text("stale\n", encoding="utf-8")

    assert write_jsonl([_record()], path) == 1

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["quote_id"] == "Q-20261001-0001"
    assert json.loads(lines[0])["provenance"] == "fixture"
