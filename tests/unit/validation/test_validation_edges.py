"""Regression tests for validation/adapter edge cases found in the fifth review."""

from __future__ import annotations

from pathlib import Path

import pytest

from media_quotes.adapters.registry import build_adapter
from media_quotes.registry import load_sources_csv
from media_quotes.source_configs import SourceConfig
from media_quotes.validation.common import check_columns

ROOT = Path(__file__).resolve().parents[3]


def test_unknown_adapter_hint_is_an_error() -> None:
    row = next(
        row for row in load_sources_csv(ROOT / "data/sources.csv") if row.source_id == "guardian"
    )
    config = SourceConfig.model_validate(
        {
            "source_id": row.source_id,
            "source_name": row.source_name,
            "homepage_url": row.homepage_url,
            "feed_url": row.feed_url,
            "tier": row.tier,
            "cadence_hours": row.cadence_hours,
            "country_region": row.country_region,
            "source_type": row.source_type,
            "claim_only": row.claim_only,
            "language": row.language,
            "requires_translation": row.requires_translation,
            "paywalled": row.paywalled,
            "rights_status": row.rights_status,
            "access_mode": row.access_mode,
            "license_reference": row.license_reference,
            "discovery_method": row.discovery_method,
            "archive_start_date": row.archive_start_date.isoformat(),
            "enabled": row.enabled,
            "notes": row.notes,
            "extractor_hints": {"adapter": "no_such_plugin"},
        }
    )
    with pytest.raises(ValueError, match="unknown adapter plugin"):
        build_adapter(row, source_config=config)


def test_columns_are_checked_on_every_row() -> None:
    rows = [
        {"source_id": "a", "extra": ""},
        {"source_id": "b", "extra": "", "oops": "x"},
    ]
    errors: list[str] = []
    check_columns(rows, ["source_id", "extra"], "test.csv", errors)
    assert any("unexpected columns" in message and "oops" in message for message in errors)
