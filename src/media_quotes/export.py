"""Derived JSONL export for LLM contextualization.

JSONL is generated from Postgres, not maintained by hand. Every record is validated
against the strict `ExportQuoteRecord` model, which mirrors data/quotes.schema.json.
Format: UTF-8, no BOM, one JSON object per line, newline-terminated, .jsonl extension.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any, cast

from .config import DEFAULT_TOPIC_ID
from .enums import AcquisitionProvenance
from .models import ExportQuoteRecord
from .text_utils import require_safe_id


def local_store_path(
    export_dir: Path,
    report_date: date,
    *,
    topic_id: str | None = None,
    provenance: AcquisitionProvenance = "fixture",
    scope: str | None = None,
) -> Path:
    """Store path for one report date; fixtures and scoped runs are isolated."""
    if provenance == "fixture":
        root = export_dir / "fixtures"
    elif scope:
        root = export_dir / "runs" / scope
    else:
        root = export_dir
    name = f"quotes-{report_date.isoformat()}.jsonl"
    if topic_id and topic_id != DEFAULT_TOPIC_ID:
        return root / require_safe_id(topic_id, "topic_id") / name
    return root / name


def read_jsonl(path: Path) -> list[ExportQuoteRecord]:
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


def content_hash(parts: Sequence[str]) -> str:
    """Deterministic SHA-256 over canonical JSON, used for ids and the decision cache."""
    canonical = json.dumps(list(parts), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def safe_replace(temp: Path, target: Path, *, tolerate_existing: bool = False) -> None:
    """Atomically replace target with temp; retry briefly on Windows locks.

    Strict by default: a final PermissionError propagates so a failed write is visible.
    Content-addressed blobs set tolerate_existing=True: a concurrent writer that wins
    the race leaves identical bytes, so keeping the existing target is success.
    """
    for attempt in range(5):
        try:
            with contextlib.suppress(OSError):
                # NamedTemporaryFile is 0600; the dashboard runs as a different UID.
                temp.chmod(0o644)
            os.replace(temp, target)
            return
        except PermissionError:
            if attempt == 4:
                if tolerate_existing and target.exists():
                    temp.unlink(missing_ok=True)
                    return
                raise
            time.sleep(0.05 * (attempt + 1))


# Backward-compatible import path for existing callers/tests.
_safe_replace = safe_replace


def atomic_write_text(path: Path, text: str) -> None:
    """Write through a temporary file so a crash cannot truncate the final artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as f:
        temp_path = Path(f.name)
        f.write(text)
    try:
        safe_replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def write_jsonl(records: Iterable[ExportQuoteRecord], path: Path) -> int:
    """Stream validated records to a temp file, then replace the final export atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    import tempfile

    count = 0
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as f:
        temp_path = Path(f.name)
        for record in records:
            f.write(
                json.dumps(
                    record.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
                )
            )
            f.write("\n")
            count += 1
    try:
        safe_replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
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
    speaker: str = "",
    publication_name: str = "",
    license_reference: str = "",
    discovery_method: str = "web",
    article_author: str = "",
    obsolete_reason: str = "",
    notes: str = "",
    topic_id: str = DEFAULT_TOPIC_ID,
    provenance: AcquisitionProvenance = "fixture",
) -> ExportQuoteRecord:
    """Build and validate one schema-compliant exported record."""
    record: dict[str, Any] = {
        "provenance": provenance,
        "quote_id": quote_id,
        "quote_text_original": quote_text_original,
        "quote_html": quote_html,
        "quote_text_en": quote_text_en,
        "speaker": speaker,
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
        "verification": verification,
        "notes": notes,
        "topic_id": topic_id,
    }
    return ExportQuoteRecord.model_validate(record)
