"""Deterministic Markdown digest renderer.

Quote text and metadata are copied verbatim from validated local records; only the short
section inference comes from an injectable ``InferenceProvider``. In blocked/degraded mode the
provider is a clearly labelled deterministic stand-in.
"""

from __future__ import annotations

import csv
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..ai.llm import InferenceProvider, LocalStandInProvider
from ..config import DEFAULT_TOPIC_ID
from ..models import ExportQuoteRecord
from ..risk_controls import ObsolescenceFlag

if TYPE_CHECKING:
    from ..topics import TopicPack

_INFERENCE_FAILURE_MARKER = (
    "_Section inference unavailable (provider error); quotes above are unaffected._"
)

__all__ = ["load_sections", "render_digest", "write_digest"]


def _escape_quote_text(text: str) -> str:
    """Keep quote text verbatim while neutralising Markdown link/heading syntax."""
    escaped = text.replace("[", r"\[").replace("]", r"\]")
    return "\n".join(f"\\{line}" if line.startswith("#") else line for line in escaped.split("\n"))


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
    topic_pack: TopicPack | None = None,
    provider: InferenceProvider | None = None,
    obsolescence_flags: Sequence[ObsolescenceFlag] = (),
    review_badges: Mapping[str, str] | None = None,
    notice: str = "",
    now: datetime | None = None,
    inference_failures: list[str] | None = None,
) -> str:
    """Render the Markdown for digests/REPORT_DATE.md without changing quote text."""
    if sections is not None:
        section_list = list(sections)
    elif topic_pack is not None:
        section_list = topic_pack.section_pairs()
    elif sections_csv is not None:
        section_list = load_sections(sections_csv)
    else:
        section_list = []
    default_section = topic_pack.default_section if topic_pack is not None else "other"
    inference_provider = provider or LocalStandInProvider(topic_pack=topic_pack)
    hidden = {
        flag.evidence.old_quote_id
        for flag in obsolescence_flags
        if flag.may_hide_quote(now=now or datetime.now(UTC))
    }
    grouped: dict[str, list[ExportQuoteRecord]] = {section_id: [] for section_id, _ in section_list}
    for record in records:
        if record.is_obsolete or record.quote_id in hidden:
            continue
        target_section = str(record.section_id) if record.section_id in grouped else default_section
        grouped.setdefault(target_section, []).append(record)
    for group in grouped.values():
        group.sort(key=lambda item: (item.publication_date, item.quote_id))

    if topic_pack is not None and topic_pack.topic_id != DEFAULT_TOPIC_ID:
        heading = (
            f"# Media quotes digest ({topic_pack.name}): "
            f"{window_start.isoformat()} to {window_end.isoformat()}"
        )
    else:
        heading = f"# Media quotes digest: {window_start.isoformat()} to {window_end.isoformat()}"

    lines = [
        heading,
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
                # Preserve quote text verbatim; only neutralise Markdown-significant syntax.
                clean_orig = _escape_quote_text(record.quote_text_original)
                lines.append(f"- {clean_orig}")
                quote_language = record.quote_language or record.source_language
                if record.quote_text_en and quote_language != "en":
                    clean_en = _escape_quote_text(record.quote_text_en)
                    lines.append(f"  - Translation (EN): {clean_en}")
                src_clean = " ".join((record.source_name or "").split())
                pub_name = record.publication_name or record.source_name or ""
                pub_clean = " ".join(pub_name.split())
                lines.append(f"  - Source: {src_clean}; publication: {pub_clean}")
                art_name = " ".join((record.article_name or "").split())
                # URL sanitization: ensure https:// scheme and encode parens/newlines
                safe_url = (
                    record.article_url.replace("(", "%28")
                    .replace(")", "%29")
                    .replace(" ", "%20")
                    .replace("\n", "")
                )
                if not safe_url.startswith("https://") and not safe_url.startswith("http://"):
                    safe_url = "#"
                lines.append(
                    f"  - Article: {art_name} "
                    f"({record.publication_date.isoformat()}); "
                    f"[Original article]({safe_url})"
                )
                if record.article_author:
                    byline_clean = " ".join(record.article_author.split())
                    lines.append(f"  - Byline: {byline_clean}")
                badge = (review_badges or {}).get(record.quote_id, "")
                if badge:
                    lines.append(f"  - Status: {badge} (provisional hold)")
        else:
            lines.append("_No verified quotes in this window._")
        lines.append("")
        try:
            inference = inference_provider.infer(
                section_id=section_id,
                section_name=section_name,
                quote_texts=[item.quote_text_original for item in selected],
            )
        except Exception:
            # A failed paid call is disclosed once; never retry or fall back to local text.
            if inference_failures is not None:
                inference_failures.append(section_id)
            inference = _INFERENCE_FAILURE_MARKER
        lines.append(inference)
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
    topic_pack: TopicPack | None = None,
    provider: InferenceProvider | None = None,
    obsolescence_flags: Sequence[ObsolescenceFlag] = (),
    review_badges: Mapping[str, str] | None = None,
    notice: str = "",
    now: datetime | None = None,
    inference_failures: list[str] | None = None,
) -> Path:
    """Render and write the canonical MD file (UTF-8, no BOM, deterministic bytes)."""
    markdown = render_digest(
        records,
        report_date=report_date,
        window_start=window_start,
        window_end=window_end,
        sections=sections,
        sections_csv=sections_csv,
        topic_pack=topic_pack,
        provider=provider,
        obsolescence_flags=obsolescence_flags,
        review_badges=review_badges,
        notice=notice,
        now=now,
        inference_failures=inference_failures,
    )
    from .export import atomic_write_text

    atomic_write_text(out_path, markdown)
    return out_path
