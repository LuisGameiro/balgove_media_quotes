"""Deterministic Markdown digest renderer.

Quote text and metadata are copied verbatim from validated local records; only the short
section inference comes from an injectable ``InferenceProvider``. In blocked/degraded mode the
provider is a clearly labelled deterministic stand-in.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

from .config import DEFAULT_TOPIC_ID
from .curation import group_by_section, rank_within_section
from .llm import InferenceProvider, LocalStandInProvider
from .models import ExportQuoteRecord
from .risk_controls import ObsolescenceFlag

if TYPE_CHECKING:
    from .topics import TopicPack

__all__ = ["load_sections", "render_digest", "write_digest"]

_MD_ESCAPES = str.maketrans({"\\": "\\\\", "[": "\\[", "]": "\\]", "<": "\\<", ">": "\\>"})
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def _md_inline(value: str) -> str:
    """Collapse whitespace and neutralise Markdown link/HTML syntax on one line."""
    return " ".join(value.split()).translate(_MD_ESCAPES)


def _md_quote(text: str) -> str:
    """Blockquote each source line so injected newlines cannot start a heading or list."""
    return "\n".join(f"> {line.translate(_MD_ESCAPES)}" for line in text.splitlines())


def _url_label(url: str) -> str:
    """Turn a raw URL into a short readable name for a byline."""
    parsed = urlsplit(url.rstrip(".,;:)"))
    tail = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    label = unquote(tail) if tail else parsed.netloc
    label = re.sub(r"\.(?:html?|php|aspx?|shtml)$", "", label, flags=re.IGNORECASE)
    return label.replace("-", " ").replace("_", " ").strip()


def _clean_byline(value: str) -> str:
    """Replace raw URLs with readable names before Markdown escaping."""
    return _URL_RE.sub(lambda match: _url_label(match.group(0)), value)


def _safe_article_url(url: str) -> str:
    """Percent-encode Markdown delimiters and reject non-HTTP schemes in article links."""
    cleaned = (
        url.replace("(", "%28")
        .replace(")", "%29")
        .replace("[", "%5B")
        .replace("]", "%5D")
        .replace(" ", "%20")
        .replace("\n", "")
    )
    return cleaned if cleaned.startswith(("https://", "http://")) else "#"


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
    provenance: Mapping[str, Any] | None = None,
    gaps: Sequence[Mapping[str, Any]] = (),
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
    visible = [r for r in records if not r.is_obsolete and r.quote_id not in hidden]

    # Group once using the canonical curation helper, then reclassify only default-section
    # records at render time. Records themselves are never mutated.
    render_groups: dict[str, list[ExportQuoteRecord]] = {sid: [] for sid, _ in section_list}
    render_groups.setdefault(default_section, [])
    for section_id, group in group_by_section(visible, topic_pack=topic_pack).items():
        for record in group:
            target = section_id if section_id in render_groups else default_section
            if target == default_section and topic_pack is not None:
                inferred = topic_pack.infer_section(
                    f"{record.article_name} {record.quote_text_original}"
                )
                if inferred in render_groups and inferred != default_section:
                    target = inferred
            render_groups[target].append(record)
    for group in render_groups.values():
        group[:] = rank_within_section(group, topic_pack=topic_pack)
    shown = [record for group in render_groups.values() for record in group]

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
        lines.extend([f"> {_md_inline(notice)}", ""])
    section_counts = [
        f"{section_name}: {len(render_groups.get(section_id, []))}"
        for section_id, section_name in section_list
        if render_groups.get(section_id)
    ]
    lines.extend(
        [
            "## At a glance",
            "",
            f"- {len(shown)} verified quote(s) from {len({r.source_id for r in shown})} source(s).",
        ]
    )
    if section_counts:
        lines.append("- Sections: " + "; ".join(section_counts) + ".")
    lines.append("")
    for section_id, section_name in section_list:
        selected = render_groups.get(section_id, [])
        lines.append(f"## {section_name}")
        lines.append("")
        if selected:
            article_groups: dict[tuple[str, str], list[ExportQuoteRecord]] = {}
            for record in selected:
                key = (record.article_url or record.quote_id, record.article_name)
                article_groups.setdefault(key, []).append(record)
            for article_records in article_groups.values():
                first = article_records[0]
                pub_name = first.publication_name or first.source_name
                lines.append(f"### {_md_inline(first.article_name) or 'Article'}")
                lines.append("")
                lines.append(
                    f"- Source: {_md_inline(first.source_name)}; "
                    f"publication: {_md_inline(pub_name)}"
                )
                lines.append(
                    f"- Published: {first.publication_date.isoformat()}; "
                    f"[Original article]({_safe_article_url(first.article_url)})"
                )
                bylines = list(
                    dict.fromkeys(
                        cleaned
                        for record in article_records
                        if (cleaned := _md_inline(_clean_byline(record.article_author)))
                    )
                )
                if bylines:
                    lines.append(f"- Byline: {'; '.join(bylines)}")
                lines.append("")
                for record in article_records:
                    lines.append(_md_quote(record.quote_text_original))
                    lines.append("")
                    quote_language = record.quote_language or record.source_language
                    lines.append(
                        f"- Quote: {record.quote_id}; language: {_md_inline(quote_language)}; "
                        f"claim-only: {'yes' if record.claim_only else 'no'}"
                    )
                    if record.quote_text_en and quote_language != "en":
                        lines.append(f"- Translation (EN): {_md_inline(record.quote_text_en)}")
                    if record.speaker:
                        lines.append(f"- Speaker: {_md_inline(record.speaker)}")
                    badge = (review_badges or {}).get(record.quote_id, "")
                    if badge:
                        lines.append(f"- Status: {badge} (provisional hold)")
                    lines.append("")
        else:
            lines.append("_No verified quotes in this window._")
        lines.append("")
        if selected:
            lines.append(
                inference_provider.infer(
                    section_id=section_id,
                    section_name=section_name,
                    quote_texts=[item.quote_text_original for item in selected],
                )
            )
            lines.append("")

    prov = provenance or {}
    topic_label = str(
        prov.get("topic_id")
        or prov.get("topic")
        or (topic_pack.topic_id if topic_pack is not None else DEFAULT_TOPIC_ID)
    )
    provider_label = str(prov.get("provider_label") or prov.get("provider") or "")
    source_count = prov.get("source_count", len({r.source_id for r in shown}))
    quote_count = prov.get("quote_count", len(shown))
    obsolete_count = prov.get(
        "obsolete_count",
        sum(1 for r in records if r.is_obsolete or r.quote_id in hidden),
    )
    under_review_count = prov.get("under_review_count", len(review_badges or {}))
    gap_labels: list[str] = []
    for gap in gaps:
        label = ":".join(
            part
            for part in (
                str(gap.get("source_id") or gap.get("source") or ""),
                str(gap.get("gap_reason") or gap.get("status") or ""),
            )
            if part
        )
        gap_labels.append(_md_inline(label or "unknown"))
    gap_detail = f" ({', '.join(gap_labels)})" if gap_labels else ""
    footer = (
        "_Provenance: "
        f"window {window_start.isoformat()} to {window_end.isoformat()}; "
        f"topic {_md_inline(topic_label)}; "
        f"sources {source_count}; quotes {quote_count}; "
        f"disclosed gaps {len(gaps)}{gap_detail}; "
        f"obsolete {obsolete_count}; under review {under_review_count}"
    )
    if provider_label:
        footer += f"; provider {_md_inline(provider_label)}"
    lines.extend(["---", "", footer + "._"])
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
    provenance: Mapping[str, Any] | None = None,
    gaps: Sequence[Mapping[str, Any]] = (),
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
        provenance=provenance,
        gaps=gaps,
    )
    from .export import atomic_write_text

    atomic_write_text(out_path, markdown)
    return out_path
