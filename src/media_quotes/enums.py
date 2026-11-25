"""Shared enumerations and policy constants.

One definition of the values used by the CSV registry, JSON schema, database
constraints, validation and the section catalog. Import from here rather than
re-declaring literals in individual modules.
"""

from typing import Final, Literal

SourceType = Literal[
    "independent", "publicly funded", "state-affiliated", "state-controlled", "aggregator"
]
RightsStatus = Literal["license_held", "api_permitted", "pending_counsel_review", "blocked"]
AccessMode = Literal[
    "licensed_database",
    "official_api",
    "public_rss_permitted",
    "pending_review",
    "scraped_prohibited",
]
DiscoveryMethod = Literal["sitemap", "rss", "web", "api", "gdelt", "licensed_database"]
OriginalReportingFlag = Literal["first-hand", "relayed wire copy", "unknown"]
AvailabilityStatus = Literal[
    "live",
    "partial_body",
    "accessible_no_quotes",
    "blocked",
    "discovery_only",
    "fixture_only",
]
SectionId = Literal[
    "war_intensity",
    "international_support",
    "peace_initiatives",
    "sanctions",
    "resources",
    "other",
]

APPROVED_RIGHTS: Final[frozenset[str]] = frozenset({"license_held", "api_permitted"})
PERMITTED_ACCESS: Final[frozenset[str]] = frozenset(
    {"licensed_database", "official_api", "public_rss_permitted"}
)
ENUMERABLE_DISCOVERY: Final[frozenset[str]] = frozenset(
    {"sitemap", "rss", "web", "api", "gdelt", "licensed_database"}
)
TIERS: Final[frozenset[str]] = frozenset({"1", "2", "3", "4"})
BOOLEANS: Final[frozenset[str]] = frozenset({"true", "false"})

SOURCE_TYPES: Final[tuple[str, ...]] = (
    "independent",
    "publicly funded",
    "state-affiliated",
    "state-controlled",
    "aggregator",
)
RIGHTS_STATUSES: Final[tuple[str, ...]] = (
    "license_held",
    "api_permitted",
    "pending_counsel_review",
    "blocked",
)
ACCESS_MODES: Final[tuple[str, ...]] = (
    "licensed_database",
    "official_api",
    "public_rss_permitted",
    "pending_review",
    "scraped_prohibited",
)
DISCOVERY_METHODS: Final[tuple[str, ...]] = tuple(DiscoveryMethod.__args__)  # type: ignore[attr-defined]
AVAILABILITY_STATUSES: Final[tuple[str, ...]] = tuple(AvailabilityStatus.__args__)  # type: ignore[attr-defined]
SECTION_IDS: Final[tuple[str, ...]] = tuple(SectionId.__args__)  # type: ignore[attr-defined]
