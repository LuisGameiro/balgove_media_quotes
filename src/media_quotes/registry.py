"""Authoritative source registry validation.

data/sources/*.json is authoritative and data/sources.csv is its generated mirror
(docs/03-sources.md explains the policy). This module validates CSV rows into strict models
and syncs them into Postgres.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import (
    APPROVED_RIGHTS,
    ENUMERABLE_DISCOVERY,
    PERMITTED_ACCESS,
    AccessMode,
    AvailabilityStatus,
    DiscoveryMethod,
    RightsStatus,
    SourceType,
)

EXPECTED_COLUMNS = [
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
    "tier_block",
    "blocker_reason",
    "availability_status",
    "notes",
]


class SourceRegistryRow(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    source_id: str
    source_name: str
    homepage_url: str
    feed_url: str = ""
    tier: int = Field(ge=1, le=4)
    cadence_hours: int = Field(ge=0)
    country_region: str
    source_type: SourceType
    claim_only: bool
    language: str = Field(pattern=r"^[a-z]{2}$")
    requires_translation: bool
    paywalled: bool | None  # None == 'unknown' in the CSV
    rights_status: RightsStatus
    access_mode: AccessMode
    license_reference: str = ""
    discovery_method: DiscoveryMethod
    archive_start_date: date
    enabled: bool = False
    tier_block: bool = False
    blocker_reason: str = ""
    availability_status: AvailabilityStatus = "live"
    notes: str = ""

    @field_validator("claim_only", "requires_translation", "enabled", "tier_block", mode="before")
    @classmethod
    def _csv_bool(cls, value: Any) -> Any:
        if isinstance(value, bool):
            return value
        if value == "true":
            return True
        if value == "false":
            return False
        raise ValueError("expected 'true' or 'false'")

    @field_validator("paywalled", mode="before")
    @classmethod
    def _csv_paywalled(cls, value: Any) -> Any:
        if isinstance(value, bool) or value is None:
            return value
        if value in ("", "unknown"):
            return None
        if value == "true":
            return True
        if value == "false":
            return False
        raise ValueError("expected 'true', 'false' or 'unknown'")

    @field_validator("tier", "cadence_hours", mode="before")
    @classmethod
    def _csv_int(cls, value: Any) -> Any:
        return int(value) if isinstance(value, str) else value

    @field_validator("archive_start_date", mode="before")
    @classmethod
    def _csv_date(cls, value: Any) -> Any:
        return date.fromisoformat(value) if isinstance(value, str) else value

    @field_validator("feed_url", "license_reference", "blocker_reason", "notes", mode="before")
    @classmethod
    def _empty_string(cls, value: Any) -> Any:
        return "" if value is None else value

    @property
    def usable(self) -> bool:
        """T0 gate in code; the database enforces the same rule via CHECK constraints."""
        return (
            self.enabled
            and self.rights_status in APPROVED_RIGHTS
            and bool(self.license_reference)
            and self.access_mode in PERMITTED_ACCESS
            and self.discovery_method in ENUMERABLE_DISCOVERY
            and self.discovery_method != "web"
        )

    @property
    def policy_flags(self) -> dict[str, Any]:
        """Flags that must travel into every stored quote."""
        return {
            "source_id": self.source_id,
            "source_tier": self.tier,
            "source_country_region": self.country_region,
            "source_type": self.source_type,
            "claim_only": self.claim_only,
            "requires_translation": self.requires_translation,
            "source_language": self.language,
            "access_mode": self.access_mode,
            "license_reference": self.license_reference,
        }


def load_sources_csv(path: Path) -> list[SourceRegistryRow]:
    with Path(path).open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = set(EXPECTED_COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"sources.csv is missing columns: {sorted(missing)}")
        return [SourceRegistryRow.model_validate(row) for row in reader]
