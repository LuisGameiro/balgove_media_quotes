"""Derived quote export validation."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import jsonschema


def check_quotes(
    store: Path,
    sources: dict[str, dict[str, str]],
    sections: set[str],
    schema: dict[str, Any],
    errors: list[str],
) -> None:
    if not store.exists():
        return
    validator = jsonschema.Draft202012Validator(schema)
    with store.open(encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            where = f"{store.name} line {lineno}"
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"{where}: invalid JSON ({exc.msg})")
                continue
            try:
                validator.validate(record)
            except jsonschema.ValidationError as exc:
                path = ".".join(str(part) for part in exc.path)
                errors.append(f"{where}: schema error at {path}: {exc.message}")
            source = sources.get(record.get("source_id", ""))
            if source is None:
                errors.append(f"{where}: unknown source_id {record.get('source_id')!r}")
            else:
                for key, column in (
                    ("source_type", "source_type"),
                    ("source_country_region", "country_region"),
                    ("claim_only", "claim_only"),
                    ("rights_status", "rights_status"),
                    ("access_mode", "access_mode"),
                    ("license_reference", "license_reference"),
                    ("discovery_method", "discovery_method"),
                ):
                    expected: Any = (
                        source[column] == "true" if column == "claim_only" else source[column]
                    )
                    if record.get(key) != expected:
                        errors.append(f"{where}: {key} does not match sources.csv ({expected!r})")
                if record.get("source_tier") != int(source["tier"]):
                    errors.append(f"{where}: source_tier does not match sources.csv")
                if source["rights_status"] == "blocked":
                    errors.append(f"{where}: quote cannot be stored from a blocked source")
            if record.get("section_id") not in sections:
                errors.append(f"{where}: unknown section_id {record.get('section_id')!r}")
            pub = record.get("publication_date", "")
            qid = record.get("quote_id", "")
            if re.fullmatch(r"Q-\d{8}-\d{4}", qid) and not qid.startswith(
                "Q-" + pub.replace("-", "") + "-"
            ):
                errors.append(f"{where}: quote_id date does not match publication_date")
            try:
                extracted = datetime.fromisoformat(
                    record["extraction_date"].replace("Z", "+00:00")
                ).date()
                if extracted < date.fromisoformat(pub):
                    errors.append(f"{where}: extraction_date precedes publication_date")
            except (KeyError, ValueError, TypeError):
                pass
            verification = record.get("verification", {})
            if not verification.get("verbatim_match") or not verification.get("metadata_verified"):
                errors.append(
                    f"{where}: quote cannot be stored without both verification flags true"
                )
            if record.get("is_obsolete") and not record.get("obsolete_reason"):
                errors.append(f"{where}: obsolete quotes need an obsolete_reason")
            quote_language = record.get("quote_language") or ""
            needs_translation = record.get("source_language") not in (None, "en") or (
                quote_language not in ("", "en", "und")
            )
            if needs_translation and not record.get("quote_text_en"):
                errors.append(f"{where}: non-English quotes need quote_text_en")
