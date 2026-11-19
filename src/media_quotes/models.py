"""Strict boundary models.

These schemas are used at the verification gate: an LLM may *propose* a quote, but the
quote is only published after a deterministic verifier has matched it against stored
article text. Strict mode prevents silent coercion (e.g. '123' -> 123).
"""

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import (
    AccessMode,
    DiscoveryMethod,
    OriginalReportingFlag,
    RightsStatus,
    SectionId,
    SourceType,
)

STRICT = ConfigDict(strict=True, extra="forbid")


def _parse_date(value: Any) -> Any:
    return date.fromisoformat(value) if isinstance(value, str) else value


def _parse_datetime(value: Any) -> Any:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value


# Source rows are defined in registry.py, mirroring the authoritative data/sources.csv.


class ArticleIn(BaseModel):
    model_config = STRICT

    canonical_url: str
    title: str | None = None
    publication_date: date | None = None
    language: str | None = None
    extracted_text: str
    extractor_version: str
    content_hash: str
    raw_blob_ref: str | None = None
    article_metadata: dict[str, Any] = Field(default_factory=dict)


class QuoteCandidate(BaseModel):
    """proposal from an extractor or LLM; not trusted until verified."""

    model_config = STRICT

    quote_text: str = Field(min_length=20, max_length=4000)
    article_id: UUID
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=1)
    dom_path: str | None = None
    speaker: str | None = None
    attribution_span: str | None = None


class VerificationResult(BaseModel):
    model_config = STRICT

    candidate: QuoteCandidate
    status: Literal["verified", "unverified", "rejected"]
    checks: dict[str, bool]
    reasons: list[str] = Field(default_factory=list)
    verified_at: datetime


class DecisionIn(BaseModel):
    """Provenance record for one LLM call; mirrored into llm_decisions."""

    model_config = STRICT

    decision_type: str
    cache_key: str
    input_hash: str
    model_snapshot: str
    prompt_version: str
    schema_hash: str
    decoding_params: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] | None = None
    refusal: bool = False
    incomplete: bool = False
    provider_response_id: str | None = None


class VerificationRecord(BaseModel):
    """Deterministic verification snapshot stored on every exported quote."""

    model_config = ConfigDict(extra="forbid")

    verbatim_match: bool
    metadata_verified: bool
    method: str
    verified_at: datetime

    @field_validator("verified_at", mode="before")
    @classmethod
    def _parse_verified_at(cls, value: Any) -> Any:
        return _parse_datetime(value)


class ExportQuoteRecord(BaseModel):
    """One record in the derived JSONL export; mirrors data/quotes.schema.json."""

    model_config = ConfigDict(extra="forbid")

    quote_id: str = Field(pattern=r"^Q-[0-9]{8}-[0-9]{4}$")
    quote_text_original: str = Field(min_length=1)
    quote_html: str = ""
    quote_text_en: str = ""
    source_id: str
    source_name: str
    publication_name: str = ""
    source_tier: int
    source_country_region: str
    source_type: SourceType
    rights_status: RightsStatus
    access_mode: AccessMode
    license_reference: str = ""
    discovery_method: DiscoveryMethod
    claim_only: bool
    original_reporting_flag: OriginalReportingFlag
    source_language: str = Field(pattern=r"^[a-z]{2}$")
    publication_date: date
    article_name: str
    article_author: str = ""
    article_url: str
    extraction_date: datetime
    section_id: SectionId
    is_obsolete: bool
    obsolete_reason: str = ""
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    verification: VerificationRecord
    notes: str = ""

    @field_validator("publication_date", mode="before")
    @classmethod
    def _parse_publication_date(cls, value: Any) -> Any:
        return _parse_date(value)

    @field_validator("extraction_date", mode="before")
    @classmethod
    def _parse_extraction_date(cls, value: Any) -> Any:
        return _parse_datetime(value)
