"""Extra coverage for registry CSV coercion validators and loader errors."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from media_quotes.registry import SourceRegistryRow, load_sources_csv

SOURCES_CSV = Path(__file__).resolve().parents[2] / "data" / "sources.csv"


def _base() -> dict[str, object]:
    return dict(load_sources_csv(SOURCES_CSV)[0].model_dump(mode="python"))


@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, True), (False, False), ("true", True), ("false", False)],
)
def test_csv_bool_coercions(value: object, expected: bool) -> None:
    base = _base()
    base["claim_only"] = value
    row = SourceRegistryRow.model_validate(base)
    assert row.claim_only is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", True),
        ("false", False),
        ("unknown", None),
        ("", None),
        (True, True),
        (None, None),
    ],
)
def test_csv_paywalled_coercions(value: object, expected: bool | None) -> None:
    base = _base()
    base["paywalled"] = value
    row = SourceRegistryRow.model_validate(base)
    assert row.paywalled is expected


def test_csv_paywalled_rejects_unknown_value() -> None:
    base = _base()
    base["paywalled"] = "maybe"
    with pytest.raises(ValueError):
        SourceRegistryRow.model_validate(base)


def test_csv_int_and_date_coercions() -> None:
    base = _base()
    base["tier"] = "2"
    base["cadence_hours"] = "12"
    base["archive_start_date"] = "2026-11-01"
    row = SourceRegistryRow.model_validate(base)
    assert row.tier == 2
    assert row.cadence_hours == 12
    assert row.archive_start_date == date(2026, 11, 1)


def test_load_sources_csv_rejects_missing_columns(tmp_path: Path) -> None:
    path = tmp_path / "sources.csv"
    path.write_text("source_id,source_name\ns1,Example\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing columns"):
        load_sources_csv(path)
