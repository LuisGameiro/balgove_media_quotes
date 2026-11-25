"""Unit tests for the PDF document extractor and think-tank parser."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from media_quotes.adapters.html_extractor import parse_newspaper_html
from media_quotes.adapters.pdf_extractor import extract_pdf_text, parse_pdf_document
from media_quotes.models import QuoteCandidate
from media_quotes.registry import load_sources_csv
from media_quotes.verification import verify_quote

_QUOTE_LINE = (
    b'(Dr. Jane Doe noted, \\"Security protocols require enhanced monitoring.\\" '
    b"on Wednesday.) Tj\n"
)


def _pdf(metadata: bytes) -> bytes:
    return (
        b"%PDF-1.4\n1 0 obj << "
        + metadata
        + b" >> endobj\n2 0 obj << /Length 120 >>\nstream\nBT\n"
        + _QUOTE_LINE
        + b"ET\nendstream\nendobj\ntrailer << /Root 1 0 R >>\n%%EOF"
    )


def test_pdf_extractor_basic_stream() -> None:
    # Build a minimal valid PDF payload containing text and a quote
    quote_line = (
        b'(Dr. Jane Doe noted, \\"Security protocols require enhanced monitoring.\\" '
        b"on Wednesday.) Tj\n"
    )
    pdf_content = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Title (Think Tank Strategic Assessment) /Author (Dr. Jane Doe) "
        b"/CreationDate (D:20261119120000) >> endobj\n"
        b"2 0 obj << /Length 120 >>\n"
        b"stream\n"
        b"BT\n"
        b"/F1 12 Tf\n" + quote_line + b"ET\n"
        b"endstream\n"
        b"endobj\n"
        b"trailer << /Root 1 0 R >>\n"
        b"%%EOF"
    )

    text, meta = extract_pdf_text(pdf_content)
    assert "Security protocols require enhanced monitoring." in text
    assert meta["title"] == "Think Tank Strategic Assessment"
    assert meta["byline"] == "Dr. Jane Doe"
    assert meta["publication_date"] == "2026-11-19"

    parsed = parse_pdf_document(pdf_content, source_url="https://thinktank.example.org/report.pdf")
    assert parsed["extraction_method"] == "pdf_document_v1"
    assert len(parsed["quote_spans"]) >= 1
    quote = parsed["quote_spans"][0]
    assert "Security protocols require enhanced monitoring." in quote["quote_text"]
    assert quote["char_start"] >= 0
    assert quote["char_end"] > quote["char_start"]


def test_pdf_extraction_via_newspaper_html_router() -> None:
    quote_line = (
        '(General Vance said, "Frontline stability has improved this quarter." '
        "during the briefing.) Tj\n"
    )
    pdf_content = (
        "%PDF-1.4\n"
        "1 0 obj << /Title (Policy Analysis) >> endobj\n"
        "2 0 obj stream\n"
        "BT\n" + quote_line + "ET\n"
        "endstream endobj\n"
        "%%EOF"
    )
    result = parse_newspaper_html(pdf_content, source_url="https://rusi.example.org/brief.pdf")
    assert result["extraction_method"] == "pdf_document_v1"
    assert "Frontline stability has improved this quarter." in result["article_text"]
    assert len(result["quote_spans"]) == 1
    assert (
        result["quote_spans"][0]["quote_text"] == "Frontline stability has improved this quarter."
    )


def test_pdf_without_metadata_keeps_fields_empty() -> None:
    parsed = parse_pdf_document(_pdf(b""))
    assert parsed["canonical_url"] == ""
    assert parsed["title"] == ""
    assert parsed["byline"] == ""
    assert parsed["publication_date"] == ""
    assert parsed["publication_name"] == ""

    with_url = parse_pdf_document(_pdf(b""), source_url="https://example.org/report.pdf")
    assert with_url["canonical_url"] == "https://example.org/report.pdf"
    assert with_url["title"] == ""


def test_metadata_less_pdf_is_unverified() -> None:
    parsed = parse_pdf_document(_pdf(b""))
    span = parsed["quote_spans"][0]
    candidate = QuoteCandidate(
        quote_text=str(span["quote_text"]),
        article_id=uuid5(NAMESPACE_URL, "pdf"),
        char_start=int(span["char_start"]),
        char_end=int(span["char_end"]),
        speaker="Dr. Jane Doe",
        attribution_span="noted",
    )
    row = load_sources_csv(Path(__file__).resolve().parents[2] / "data" / "sources.csv")[0]
    flags = dict(row.policy_flags)
    flags.update({"source_id": row.source_id, "original_reporting_flag": "first-hand"})
    result = verify_quote(
        candidate,
        str(parsed["article_text"]),
        row,
        flags,
        metadata=parsed,
        window_start=date(2026, 1, 1),
        window_end=date(2026, 12, 31),
    )
    assert result.checks["metadata_non_empty"] is False
    assert result.status != "verified"
