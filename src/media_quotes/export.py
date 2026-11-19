"""Derived JSONL export for LLM contextualization.

JSONL is generated from Postgres, not maintained by hand. Every record is validated
against the strict `ExportQuoteRecord` model, which mirrors data/quotes.schema.json.
Format: UTF-8, no BOM, one JSON object per line, newline-terminated, .jsonl extension.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any, cast

from .models import ExportQuoteRecord


def local_store_path(export_dir: Path, report_date: date) -> Path:  # pragma: no cover
    """Canonical offline store path for one report date."""
    return export_dir / f"quotes-{report_date.isoformat()}.jsonl"


def read_jsonl(path: Path) -> list[ExportQuoteRecord]:  # pragma: no cover
    """Read a local JSONL store back into strict records (idempotent replay input)."""
    if not path.is_file():
        return []
    records: list[ExportQuoteRecord] = []
    with path.open(encoding="utf-8", newline="\n") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = cast(dict[str, Any], json.loads(line))
            records.append(ExportQuoteRecord.model_validate(payload))
    return records


def content_hash(parts: Sequence[str]) -> str:  # pragma: no cover
    """Deterministic SHA-256 over canonical JSON, used for ids and the decision cache."""
    canonical = json.dumps(list(parts), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def write_jsonl(records: Iterable[ExportQuoteRecord], path: Path) -> int:
    """Write validated records as JSONL and return the number of lines written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            payload = record.model_dump(mode="json")
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
            count += 1
    return count


def quote_record(
    *,
    quote_id: str,
    quote_text_original: str,
    source_id: str,
    source_name: str,
    source_tier: int,
    source_country_region: str,
    source_type: str,
    rights_status: str,
    access_mode: str,
    claim_only: bool,
    original_reporting_flag: str,
    source_language: str,
    publication_date: str,
    article_name: str,
    article_url: str,
    extraction_date: str,
    section_id: str,
    is_obsolete: bool,
    content_hash: str,
    verification: Mapping[str, Any],
    quote_html: str = "",
    quote_text_en: str = "",
    publication_name: str = "",
    license_reference: str = "",
    discovery_method: str = "web",
    article_author: str = "",
    obsolete_reason: str = "",
    notes: str = "",
) -> ExportQuoteRecord:
    """Build and validate one schema-compliant exported record."""
    record: dict[str, Any] = {
        "quote_id": quote_id,
        "quote_text_original": quote_text_original,
        "quote_html": quote_html,
        "quote_text_en": quote_text_en,
        "source_id": source_id,
        "source_name": source_name,
        "publication_name": publication_name,
        "source_tier": source_tier,
        "source_country_region": source_country_region,
        "source_type": source_type,
        "rights_status": rights_status,
        "access_mode": access_mode,
        "license_reference": license_reference,
        "discovery_method": discovery_method,
        "claim_only": claim_only,
        "original_reporting_flag": original_reporting_flag,
        "source_language": source_language,
        "publication_date": publication_date,
        "article_name": article_name,
        "article_author": article_author,
        "article_url": article_url,
        "extraction_date": extraction_date,
        "section_id": section_id,
        "is_obsolete": is_obsolete,
        "obsolete_reason": obsolete_reason,
        "content_hash": content_hash,
        "verification": dict(verification),
        "notes": notes,
    }
    return ExportQuoteRecord.model_validate(record)
