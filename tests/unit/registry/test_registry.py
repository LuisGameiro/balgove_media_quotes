from datetime import date
from pathlib import Path

import pytest

from media_quotes.registry import SourceRegistryRow, load_sources_csv

SOURCES_CSV = Path(__file__).resolve().parents[3] / "data" / "sources.csv"


def test_load_registry():
    rows = load_sources_csv(SOURCES_CSV)
    assert len(rows) == 28
    by_id = {row.source_id: row for row in rows}
    assert by_id["reuters"].rights_status == "license_held"
    assert by_id["reuters"].license_reference
    assert by_id["reuters"].discovery_method == "sitemap"
    assert by_id["bbc"].discovery_method == "rss"
    assert by_id["kyodo"].paywalled is None
    assert by_id["anadolu"].claim_only is True
    assert by_id["nyt_api"].paywalled is None
    assert all(row.enabled for row in rows)
    assert all(row.claim_only for row in rows if row.tier == 4)
    assert all(row.archive_start_date == date(2026, 10, 1) for row in rows)


def test_usable_gate_blocks_every_non_cleared_row():
    rows = load_sources_csv(SOURCES_CSV)
    # Every source is licensed and enabled; tier-4 material remains claim-only.
    assert all(row.usable for row in rows)
    bad = rows[0].model_copy(update={"rights_status": "blocked", "license_reference": ""})
    assert bad.usable is False


def test_policy_flags_travel_with_quotes():
    row = load_sources_csv(SOURCES_CSV)[0]
    flags = row.policy_flags
    assert flags["source_id"] == row.source_id
    assert flags["source_tier"] == row.tier
    assert flags["requires_translation"] == row.requires_translation


def test_strict_row_rejects_bad_boolean():
    base = load_sources_csv(SOURCES_CSV)[0].model_dump(mode="python")
    base["claim_only"] = "maybe"
    with pytest.raises(ValueError):
        SourceRegistryRow.model_validate(base)
