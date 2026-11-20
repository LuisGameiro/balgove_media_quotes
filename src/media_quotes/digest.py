"""Deterministic Markdown digest renderer.

Quote text and metadata are copied verbatim from validated local records; only the short
section inference comes from an injectable ``InferenceProvider``. In blocked/degraded mode the
provider is a clearly labelled deterministic stand-in.
"""

from __future__ import annotations

import csv
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path

from .llm import InferenceProvider, LocalStandInProvider
from .models import ExportQuoteRecord
from .risk_controls import ObsolescenceFlag

__all__ = ["load_sections", "render_digest", "write_digest"]


def load_sections(path: Path) -> list[tuple[str, str]]:
    """Read the canonical section order and display names from data/sections.csv."""
    with Path(path).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [
        (str(row.get("section_id") or ""), str(row.get("section_name") or ""))
        for row in rows
        if row.get("section_id")
    ]


def render_digest(
    records: Sequence[ExportQuoteRecord],
    *,
    report_date: date,
    window_start: date,
    window_end: date,
    sections: Sequence[tuple[str, str]] | None = None,
    sections_csv: Path | None = None,
    provider: InferenceProvider | None = None,
    obsolescence_flags: Sequence[ObsolescenceFlag] = (),
    review_badges: Mapping[str, str] | None = None,
    notice: str = "",
) -> str:
    """Render the Markdown for digests/REPORT_DATE.md without changing quote text."""
    section_list = (
        list(sections)
        if sections is not None
        else (load_sections(sections_csv) if sections_csv is not None else [])
    )
    inference_provider = provider or LocalStandInProvider()
    hidden = {flag.evidence.old_quote_id for flag in obsolescence_flags if flag.may_hide_quote()}
    grouped: dict[str, list[ExportQuoteRecord]] = {section_id: [] for section_id, _ in section_list}
    for record in records:
        if record.is_obsolete or record.quote_id in hidden:
            continue
        target_section = str(record.section_id) if record.section_id in grouped else "other"
        grouped.setdefault(target_section, []).append(record)
    for group in grouped.values():
        group.sort(key=lambda item: (item.publication_date, item.quote_id))

    lines = [
        f"# Media quotes digest: {window_start.isoformat()} to {window_end.isoformat()}",
        "",
        f"_Report date: {report_date.isoformat()}; source sections: {len(section_list)}._",
        "",
    ]
    if notice:
        lines.extend([f"> {notice}", ""])
    for section_id, section_name in section_list:
        selected = grouped.get(section_id, [])
        lines.append(f"## {section_name}")
        lines.append("")
        if selected:
            for record in selected:
                lines.append(f"- {record.quote_text_original}")
                if record.quote_text_en and record.source_language != "en":
                    lines.append(f"  - Translation (EN): {record.quote_text_en}")
                lines.append(
                    f"  - Source: {record.source_name}; publication: "
                    f"{record.publication_name or record.source_name}"
                )
                lines.append(
                    f"  - Article: {record.article_name} "
                    f"({record.publication_date.isoformat()}); "
                    f"[Original article]({record.article_url})"
                )
                if record.article_author:
                    lines.append(f"  - Byline: {record.article_author}")
                badge = (review_badges or {}).get(record.quote_id, "")
                if badge:
                    lines.append(f"  - Status: {badge} (provisional hold)")
        else:
            lines.append("_No verified quotes in this window._")
        lines.append("")
        lines.append(
            inference_provider.infer(
                section_id=section_id,
                section_name=section_name,
                quote_texts=[item.quote_text_original for item in selected],
            )
        )
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def write_digest(
    records: Sequence[ExportQuoteRecord],
    *,
    report_date: date,
    window_start: date,
    window_end: date,
    out_path: Path,
    sections: Sequence[tuple[str, str]] | None = None,
    sections_csv: Path | None = None,
    provider: InferenceProvider | None = None,
    obsolescence_flags: Sequence[ObsolescenceFlag] = (),
    review_badges: Mapping[str, str] | None = None,
    notice: str = "",
) -> Path:
    """Render and write the canonical MD file (UTF-8, no BOM, deterministic bytes)."""
    markdown = render_digest(
        records,
        report_date=report_date,
        window_start=window_start,
        window_end=window_end,
        sections=sections,
        sections_csv=sections_csv,
        provider=provider,
        obsolescence_flags=obsolescence_flags,
        review_badges=review_badges,
        notice=notice,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown, encoding="utf-8", newline="\n")
    return out_path
