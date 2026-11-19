"""The enum values are part of the data contract and must not drift silently."""

from media_quotes.enums import (
    ACCESS_MODES,
    DISCOVERY_METHODS,
    RIGHTS_STATUSES,
    SECTION_IDS,
    SOURCE_TYPES,
)


def test_enum_contracts() -> None:
    assert set(SOURCE_TYPES) == {
        "independent",
        "publicly funded",
        "state-affiliated",
        "state-controlled",
        "aggregator",
    }
    assert set(RIGHTS_STATUSES) == {
        "license_held",
        "api_permitted",
        "pending_counsel_review",
        "blocked",
    }
    assert set(ACCESS_MODES) == {
        "licensed_database",
        "official_api",
        "public_rss_permitted",
        "pending_review",
        "scraped_prohibited",
    }
    assert "licensed_database" in DISCOVERY_METHODS
    assert set(SECTION_IDS) == {
        "war_intensity",
        "international_support",
        "peace_initiatives",
        "sanctions",
        "resources",
        "other",
    }
