from datetime import date
from pathlib import Path

import pytest

from media_quotes.adapters import SourceAdapter, SourceNotPermitted, build_adapter
from media_quotes.enums import DISCOVERY_METHODS
from media_quotes.registry import load_sources_csv

SOURCES_CSV = Path(__file__).resolve().parents[2] / "data" / "sources.csv"
ROWS = {row.source_id: row for row in load_sources_csv(SOURCES_CSV)}


def test_factory_builds_adapter_for_every_registered_method() -> None:
    for method in DISCOVERY_METHODS:
        row = ROWS["reuters"].model_copy(update={"discovery_method": method})
        assert isinstance(build_adapter(row), SourceAdapter)


def test_blocked_source_cannot_discover() -> None:
    adapter = build_adapter(ROWS["reuters"])
    assert adapter.permitted is False
    with pytest.raises(SourceNotPermitted):
        adapter.discover(date(2026, 10, 1), date(2026, 10, 2))


def test_web_discovery_is_never_permitted() -> None:
    adapter = SourceAdapter(ROWS["ap"])
    with pytest.raises(SourceNotPermitted):
        adapter.fetch("https://example.invalid/article")


def test_policy_flags_come_from_registry_without_network() -> None:
    adapter = build_adapter(ROWS["anadolu"])
    assert adapter.policy_flags()["claim_only"] is True
    assert adapter.policy_flags()["source_type"] == "state-controlled"


def test_licensed_row_is_permitted() -> None:
    row = ROWS["reuters"].model_copy(
        update={
            "rights_status": "license_held",
            "access_mode": "licensed_database",
            "license_reference": "LIC-1",
            "enabled": True,
            "discovery_method": "licensed_database",
        }
    )
    adapter = build_adapter(row)
    assert adapter.permitted is True
    with pytest.raises(NotImplementedError):
        adapter.fetch("licensed://article/1")
