"""Focused unit tests for the individual validation helpers in validation.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from media_quotes.validation import (
    SECTION_COLUMNS,
    SOURCE_COLUMNS,
    check_columns,
    check_quotes,
    check_sections,
    check_sources,
    read_csv,
)

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
SOURCES_CSV = DATA_DIR / "sources.csv"
SECTIONS_CSV = DATA_DIR / "sections.csv"

OPEN_SCHEMA: dict[str, object] = {"type": "object"}


def _source_row() -> dict[str, str]:
    return dict(read_csv(SOURCES_CSV)[0])


def _approved_source_row() -> dict[str, str]:
    row = _source_row()
    row["rights_status"] = "license_held"
    row["license_reference"] = "LIC-1"
    return row


def _sections() -> list[dict[str, str]]:
    return read_csv(SECTIONS_CSV)


def _sources_map() -> dict[str, dict[str, str]]:
    errors: list[str] = []
    by_id = check_sources([_approved_source_row()], errors)
    assert errors == []
    return by_id


def _section_ids() -> set[str]:
    errors: list[str] = []
    ids = check_sections(_sections(), errors)
    assert errors == []
    return ids


def _record() -> dict[str, object]:
    return {
        "quote_id": "Q-20261005-0001",
        "source_id": "reuters",
        "source_tier": 1,
        "source_country_region": "Global",
        "source_type": "independent",
        "rights_status": "license_held",
        "access_mode": "licensed_database",
        "license_reference": "LIC-1",
        "discovery_method": "sitemap",
        "claim_only": False,
        "source_language": "en",
        "section_id": "war_intensity",
        "publication_date": "2026-10-05",
        "extraction_date": "2026-10-06T09:15:00Z",
        "verification": {"verbatim_match": True, "metadata_verified": True},
        "is_obsolete": False,
    }


# --- check_columns --------------------------------------------------------------------------


def test_check_columns_reports_empty_and_bad_shapes() -> None:
    errors: list[str] = []
    check_columns([], SOURCE_COLUMNS, "sources.csv", errors)
    assert errors == ["sources.csv: no rows found"]

    errors = []
    check_columns(
        [{"source_id": "x", "bogus": "y"}], ["source_id", "missing_col"], "sources.csv", errors
    )
    assert any("missing columns" in message for message in errors)
    assert any("unexpected columns" in message for message in errors)

    errors = []
    check_columns([{"source_id": "x"}], ["source_id"], "sources.csv", errors)
    assert errors == []


# --- check_sources --------------------------------------------------------------------------


def test_check_sources_accepts_valid_rows() -> None:
    errors: list[str] = []
    by_id = check_sources([_source_row()], errors)
    assert errors == []
    assert list(by_id) == ["reuters"]


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("source_id", "", "missing source_id"),
        ("tier", "9", "tier must be"),
        ("cadence_hours", "abc", "cadence_hours must be"),
        ("cadence_hours", "0", "positive cadence_hours"),
        ("source_type", "bogus", "unknown source_type"),
        ("rights_status", "bogus", "unknown rights_status"),
        ("access_mode", "bogus", "unknown access_mode"),
        ("discovery_method", "bogus", "unknown discovery_method"),
        ("license_reference", "", "license_reference"),
        ("source_type", "state-controlled", "state-controlled sources must be claim_only"),
        ("claim_only", "maybe", "claim_only must be true or false"),
        ("requires_translation", "maybe", "requires_translation must be true or false"),
        ("enabled", "maybe", "enabled must be true or false"),
        ("paywalled", "maybe", "paywalled must be true, false or unknown"),
        ("language", "eng", "ISO 639-1"),
        ("homepage_url", "http://example.invalid", "homepage_url must be an https URL"),
        ("feed_url", "http://example.invalid", "feed_url must be an https URL"),
        ("archive_start_date", "not-a-date", "archive_start_date is not ISO 8601"),
        ("rights_status", "blocked", "T0 gate"),
    ],
)
def test_check_sources_rejects_invalid_fields(field: str, value: str, expected: str) -> None:
    row = _source_row()
    row[field] = value
    errors: list[str] = []
    check_sources([row], errors)
    assert any(expected in message for message in errors), errors


def test_check_sources_rejects_duplicate_ids() -> None:
    row = _source_row()
    errors: list[str] = []
    check_sources([row, dict(row)], errors)
    assert any("duplicate source_id" in message for message in errors)


def test_check_sources_tier_must_be_disabled_with_zero_cadence() -> None:
    row = _source_row()
    row["tier"] = "4"
    row["cadence_hours"] = "5"
    row["enabled"] = "true"
    errors: list[str] = []
    check_sources([row], errors)
    assert any("disabled tier must have cadence_hours 0" in m for m in errors)
    assert any("tier 4 sources must be disabled" in m for m in errors)


# --- check_sections -------------------------------------------------------------------------


def test_check_sections_accepts_valid_rows() -> None:
    errors: list[str] = []
    ids = check_sections(_sections(), errors)
    assert errors == []
    assert "war_intensity" in ids
    assert SECTION_COLUMNS == ["section_id", "section_name", "description"]


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("section_id", "", "missing section_id"),
        ("section_name", "", "section_name and description are required"),
        ("description", "", "section_name and description are required"),
        ("section_id", "bogus", "unknown section_id"),
    ],
)
def test_check_sections_rejects_invalid_fields(field: str, value: str, expected: str) -> None:
    section = _sections()[0]
    section[field] = value
    errors: list[str] = []
    check_sections([section], errors)
    assert any(expected in message for message in errors), errors


def test_check_sections_rejects_duplicate_ids() -> None:
    section = _sections()[0]
    errors: list[str] = []
    check_sections([section, dict(section)], errors)
    assert any("duplicate section_id" in message for message in errors)


# --- check_quotes ---------------------------------------------------------------------------


def test_check_quotes_missing_store_is_noop(tmp_path: Path) -> None:
    errors: list[str] = []
    check_quotes(tmp_path / "quotes.jsonl", _sources_map(), _section_ids(), OPEN_SCHEMA, errors)
    assert errors == []


def test_check_quotes_accepts_valid_record_and_skips_blank_lines(tmp_path: Path) -> None:
    store = tmp_path / "quotes.jsonl"
    store.write_text("\n" + json.dumps(_record()) + "\n", encoding="utf-8")
    errors: list[str] = []
    check_quotes(store, _sources_map(), _section_ids(), OPEN_SCHEMA, errors)
    assert errors == []


def test_check_quotes_rejects_invalid_json(tmp_path: Path) -> None:
    store = tmp_path / "quotes.jsonl"
    store.write_text("{not json\n", encoding="utf-8")
    errors: list[str] = []
    check_quotes(store, _sources_map(), _section_ids(), OPEN_SCHEMA, errors)
    assert any("invalid JSON" in message for message in errors)


def test_check_quotes_reports_schema_errors(tmp_path: Path) -> None:
    store = tmp_path / "quotes.jsonl"
    record = _record()
    del record["quote_id"]
    store.write_text(json.dumps(record) + "\n", encoding="utf-8")
    schema = {"type": "object", "required": ["quote_id"]}
    errors: list[str] = []
    check_quotes(store, _sources_map(), _section_ids(), schema, errors)
    assert any("schema error" in message for message in errors)


@pytest.mark.parametrize(
    ("mutator", "expected"),
    [
        (lambda r: r.update(source_id="nope"), "unknown source_id"),
        (lambda r: r.update(source_type="bogus"), "source_type does not match"),
        (lambda r: r.update(source_tier=9), "source_tier does not match"),
        (lambda r: r.update(section_id="bogus"), "unknown section_id"),
        (lambda r: r.update(quote_id="Q-20260101-0001"), "quote_id date does not match"),
        (
            lambda r: r.update(extraction_date="2026-10-01T09:15:00Z"),
            "extraction_date precedes publication_date",
        ),
        (
            lambda r: r.update(verification={"verbatim_match": False, "metadata_verified": True}),
            "verification flags true",
        ),
        (lambda r: r.update(is_obsolete=True), "obsolete quotes need an obsolete_reason"),
        (lambda r: r.update(source_language="fr"), "non-English quotes need quote_text_en"),
    ],
)
def test_check_quotes_rejects_bad_records(tmp_path, mutator, expected: str) -> None:
    record = _record()
    mutator(record)
    store = tmp_path / "quotes.jsonl"
    store.write_text(json.dumps(record) + "\n", encoding="utf-8")
    errors: list[str] = []
    check_quotes(store, _sources_map(), _section_ids(), OPEN_SCHEMA, errors)
    assert any(expected in message for message in errors), errors


def test_check_quotes_flags_blocked_sources(tmp_path: Path) -> None:
    row = _source_row()
    row["rights_status"] = "blocked"
    row["license_reference"] = ""
    row["enabled"] = "false"
    errors: list[str] = []
    check_sources([row], errors)
    assert errors == []

    record = _record()
    record.update(
        rights_status="license_held", access_mode="licensed_database", license_reference=""
    )
    store = tmp_path / "quotes.jsonl"
    store.write_text(json.dumps(record) + "\n", encoding="utf-8")
    errors = []
    check_quotes(store, {row["source_id"]: row}, _section_ids(), OPEN_SCHEMA, errors)
    assert any("blocked source" in message for message in errors)


def test_check_quotes_ignores_missing_extraction_date(tmp_path: Path) -> None:
    record = _record()
    del record["extraction_date"]
    store = tmp_path / "quotes.jsonl"
    store.write_text(json.dumps(record) + "\n", encoding="utf-8")
    errors: list[str] = []
    check_quotes(store, _sources_map(), _section_ids(), OPEN_SCHEMA, errors)
    assert errors == []
