"""Source registry validation, including the T0 rights gate."""

from __future__ import annotations

import re
from datetime import date

from ..enums import (
    ACCESS_MODES,
    APPROVED_RIGHTS,
    BOOLEANS,
    DISCOVERY_METHODS,
    RIGHTS_STATUSES,
    SOURCE_TYPES,
    TIERS,
)
from .common import check_columns

SOURCE_COLUMNS = [
    "source_id",
    "source_name",
    "homepage_url",
    "feed_url",
    "tier",
    "cadence_hours",
    "country_region",
    "source_type",
    "claim_only",
    "language",
    "requires_translation",
    "paywalled",
    "rights_status",
    "access_mode",
    "license_reference",
    "discovery_method",
    "archive_start_date",
    "enabled",
    "notes",
]


def check_sources(sources: list[dict[str, str]], errors: list[str]) -> dict[str, dict[str, str]]:
    check_columns(sources, SOURCE_COLUMNS, "sources.csv", errors)
    by_id: dict[str, dict[str, str]] = {}
    for line, row in enumerate(sources, start=2):
        where = f"sources.csv line {line}"
        sid = row["source_id"]
        if not sid:
            errors.append(f"{where}: missing source_id")
            continue
        if sid in by_id:
            errors.append(f"{where}: duplicate source_id {sid}")
        by_id[sid] = row

        if row["tier"] not in TIERS:
            errors.append(f"{where}: tier must be 1, 2, 3 or 4")
        cadence = row["cadence_hours"]
        if not cadence.isdigit():
            errors.append(f"{where}: cadence_hours must be a non-negative integer")
        elif row["tier"] == "4" and int(cadence) != 0:
            errors.append(f"{where}: disabled tier must have cadence_hours 0")
        elif row["tier"] != "4" and int(cadence) == 0:
            errors.append(f"{where}: enabled tier needs a positive cadence_hours")
        if row["tier"] == "4" and row["enabled"] != "false":
            errors.append(f"{where}: tier 4 sources must be disabled")
        if row["source_type"] not in SOURCE_TYPES:
            errors.append(f"{where}: unknown source_type {row['source_type']!r}")
        if row["rights_status"] not in RIGHTS_STATUSES:
            errors.append(f"{where}: unknown rights_status {row['rights_status']!r}")
        if row["access_mode"] not in ACCESS_MODES:
            errors.append(f"{where}: unknown access_mode {row['access_mode']!r}")
        if row["discovery_method"] not in DISCOVERY_METHODS:
            errors.append(f"{where}: unknown discovery_method {row['discovery_method']!r}")
        if row["enabled"] == "true" and row["rights_status"] not in APPROVED_RIGHTS:
            errors.append(f"{where}: T0 gate - enabled sources need approved rights_status")
        if row["rights_status"] in APPROVED_RIGHTS and not row["license_reference"]:
            errors.append(f"{where}: approved rights need a license_reference")
        if row["source_type"] == "state-controlled" and row["claim_only"] != "true":
            errors.append(f"{where}: state-controlled sources must be claim_only")
        for flag in ("claim_only", "requires_translation", "enabled"):
            if row[flag] not in BOOLEANS:
                errors.append(f"{where}: {flag} must be true or false")
        if row["paywalled"] not in {"true", "false", "unknown"}:
            errors.append(f"{where}: paywalled must be true, false or unknown")
        if not re.fullmatch(r"[a-z]{2}", row["language"]):
            errors.append(f"{where}: language must be an ISO 639-1 two-letter code")
        for field in ("homepage_url", "feed_url"):
            value = row[field]
            if value and not value.startswith("https://"):
                errors.append(f"{where}: {field} must be an https URL")
        try:
            date.fromisoformat(row["archive_start_date"])
        except ValueError:
            errors.append(f"{where}: archive_start_date is not ISO 8601")
    return by_id
