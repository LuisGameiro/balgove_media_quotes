"""Versioned gold set with deterministic 60/20/20 article splits.

A gold set records acquisition provenance (`fixture`, `live_feed`, `live_bing`, `manual_sample`) and
its article refs may be `blob://` paths so evaluation can run on collected text. The fixture is
synthetic; the label model and split function are production-shaped. Splitting is deterministic by
sha256(article_ref) so a gold-set version always yields the same train, dev and test sets — no
tuning on the test split.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from ..enums import SECTION_IDS, AcquisitionProvenance

STRICT = ConfigDict(strict=True, extra="forbid")
Split = Literal["train", "dev", "test"]


class GoldQuoteLabel(BaseModel):
    """One human-labelled quote span in an article."""

    model_config = STRICT

    quote_text: str = Field(min_length=1)
    section_id: str
    human_label: bool


class GoldArticle(BaseModel):
    """One human-labelled article and its quote labels."""

    model_config = STRICT

    article_ref: str
    source_id: str
    publication_date: str
    relevant: bool
    quotes: list[GoldQuoteLabel] = Field(default_factory=list)


class NegativeCandidate(BaseModel):
    """A labelled non-quote that must never be published."""

    model_config = STRICT

    quote_text: str = Field(min_length=1)
    reason: str = ""
    human_label: bool = False


class GoldSet(BaseModel):
    """Validated versioned gold set from a JSON fixture."""

    model_config = STRICT

    version: str
    provenance: AcquisitionProvenance = "fixture"
    notice: str = ""
    split: str = ""
    articles: list[GoldArticle] = Field(min_length=1)
    negative_candidates: list[NegativeCandidate] = Field(default_factory=list)
    obsolete_quote_ids: list[str] = Field(default_factory=list)


def validate_sections(gold: GoldSet) -> GoldSet:
    """Reject labels using a section outside the canonical allow-list."""
    for article in gold.articles:
        for quote in article.quotes:
            if quote.section_id not in SECTION_IDS:
                raise ValueError(
                    f"unknown section_id {quote.section_id!r} in {article.article_ref}"
                )
    return gold


def load_gold_set(path: Path) -> GoldSet:
    """Read and strictly validate a gold-set JSON file."""
    data = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    return validate_sections(GoldSet.model_validate(data))


def split_for(article_ref: str, gold_set_version: str) -> Split:
    """Deterministic bucket by sha256(f'{version}:{article_ref}') modulo 10."""
    digest = hashlib.sha256(f"{gold_set_version}:{article_ref}".encode()).digest()
    bucket = digest[0] % 10
    if bucket < 6:
        return "train"
    if bucket < 8:
        return "dev"
    return "test"


def split_articles(gold: GoldSet) -> dict[Split, list[GoldArticle]]:
    """Split by article (never by quote) into 60/20/20 buckets."""
    out: dict[Split, list[GoldArticle]] = {"train": [], "dev": [], "test": []}
    for article in gold.articles:
        out[split_for(article.article_ref, gold.version)].append(article)
    return out
