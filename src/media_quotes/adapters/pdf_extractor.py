"""PDF and think-tank document text and quote extractor (DEC-08).

Extracts clean text streams, metadata (/Title, /Author, /CreationDate), and verbatim
quoted spans with exact character offsets from standard PDF documents.
Pure stdlib implementation with zero binary dependencies.
"""

from __future__ import annotations

import contextlib
import re
import zlib
from typing import Any

from ..topics import TopicPack
from .quote_extractor import extract_quotes, infer_section

__all__ = ["extract_pdf_text", "parse_pdf_document"]

_BT_ET_RE = re.compile(rb"BT\s+(.*?)\s+ET", re.DOTALL)
# A PDF string ends at the first unescaped ')'; escaped delimiters stay inside it.
_STRING_BODY = rb"((?:[^()\\]|\\.)*)"
_TEXT_OP_RE = re.compile(
    rb"\(" + _STRING_BODY + rb"\)\s*Tj" + rb"|\[((?:[^\]\\]|\\.)*)\]\s*TJ", re.DOTALL
)
_PDF_STRING_RE = re.compile(rb"\(((?:[^()\\]|\\.)*)\)", re.DOTALL)
_TITLE_RE = re.compile(rb"/Title\s*" + _PDF_STRING_RE.pattern)
_AUTHOR_RE = re.compile(rb"/Author\s*" + _PDF_STRING_RE.pattern)
_DATE_RE = re.compile(rb"/CreationDate\s*\(D:(\d{4})(\d{2})(\d{2})")
_ESCAPE_MAP = {
    b"n": b"\n",
    b"r": b"\r",
    b"t": b"\t",
    b"b": b"\b",
    b"f": b"\f",
    b"(": b"(",
    b")": b")",
    b"\\": b"\\",
}
_NAMED_ESCAPE_RE = re.compile(rb"\\([()\\nrtbf])")
_LINE_CONTINUATION_RE = re.compile(rb"\\\r?\n")


def _unescape_pdf_string(value: bytes) -> str:
    """Resolve PDF string escapes without losing the original character offsets."""
    value = _LINE_CONTINUATION_RE.sub(b"", value)
    value = _NAMED_ESCAPE_RE.sub(lambda match: _ESCAPE_MAP[match.group(1)], value)
    return value.decode("latin1", errors="replace")


def _decode_stream(stream_bytes: bytes) -> str:
    """Extract text chunks from uncompressed or FlateDecode PDF content streams."""
    decompressed: list[bytes] = []
    # Try finding FlateDecode streams
    for match in re.finditer(rb"stream[\r\n]+(.*?)[\r\n]+endstream", stream_bytes, re.DOTALL):
        chunk = match.group(1)
        try:
            decompressed.append(zlib.decompress(chunk))
        except Exception:
            decompressed.append(chunk)

    data = b"\n".join(decompressed) if decompressed else stream_bytes
    text_parts: list[str] = []

    for bt_match in _BT_ET_RE.finditer(data):
        block = bt_match.group(1)
        for op in _TEXT_OP_RE.finditer(block):
            if op.group(1) is not None:
                # One Tj operator is one word boundary.
                text_parts.append(_unescape_pdf_string(op.group(1)))
            else:
                # TJ array fragments are kerning pieces of a single word: no injected spaces.
                fragments = b"".join(
                    sub.group(1) for sub in _PDF_STRING_RE.finditer(op.group(2) or b"")
                )
                text_parts.append(_unescape_pdf_string(fragments))

    return " ".join(text_parts).strip()


def extract_pdf_text(raw_bytes: bytes) -> tuple[str, dict[str, str]]:
    """Extract text and metadata from PDF bytes."""
    meta: dict[str, str] = {}
    title_m = _TITLE_RE.search(raw_bytes)
    if title_m:
        with contextlib.suppress(Exception):
            meta["title"] = _unescape_pdf_string(title_m.group(1)).strip()

    author_m = _AUTHOR_RE.search(raw_bytes)
    if author_m:
        with contextlib.suppress(Exception):
            meta["byline"] = _unescape_pdf_string(author_m.group(1)).strip()

    date_m = _DATE_RE.search(raw_bytes)
    if date_m:
        year, month, day = (
            date_m.group(1).decode("ascii"),
            date_m.group(2).decode("ascii"),
            date_m.group(3).decode("ascii"),
        )
        meta["publication_date"] = f"{year}-{month}-{day}"

    body_text = _decode_stream(raw_bytes)
    return body_text, meta


def parse_pdf_document(
    raw_content: bytes | str,
    *,
    source_url: str = "",
    topic_pack: TopicPack | None = None,
) -> dict[str, Any]:
    """Parse a think-tank or policy research PDF document and extract verbatim quotes."""
    raw_bytes = (
        raw_content
        if isinstance(raw_content, bytes)
        else raw_content.encode("latin1", errors="replace")
    )
    body_text, meta = extract_pdf_text(raw_bytes)

    quotes: list[dict[str, Any]] = []
    if body_text:
        raw_quotes = extract_quotes(body_text)
        for q in raw_quotes:
            q_text = str(q.get("quote_text", "")).strip()
            if not q_text:
                continue
            section = str(q.get("section_id") or infer_section(q_text, topic_pack=topic_pack))
            quotes.append(
                {
                    "quote_text": q_text,
                    "char_start": int(q.get("char_start", 0)),
                    "char_end": int(q.get("char_end", 0)),
                    "dom_path": "pdf/body/text",
                    "speaker": str(q.get("speaker") or ""),
                    "role": str(q.get("role") or ""),
                    "attribution_span": str(q.get("attribution_span") or ""),
                    "section_id": section,
                }
            )

    # No invented metadata: absent fields stay empty so verification fails closed.
    return {
        "canonical_url": source_url or "",
        "publication_date": meta.get("publication_date", ""),
        "title": meta.get("title", ""),
        "byline": meta.get("byline", ""),
        "publication_name": meta.get("publication_name", ""),
        "article_text": body_text,
        "quote_spans": quotes,
        "extraction_method": "pdf_document_v1",
        "extractor_version": "pdf-extractor-v1",
    }
