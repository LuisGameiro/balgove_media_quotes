"""Unit tests for the PDF document extractor and think-tank parser."""

from __future__ import annotations

from media_quotes.adapters.html_extractor import parse_newspaper_html
from media_quotes.adapters.pdf_extractor import extract_pdf_text, parse_pdf_document


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


def test_adapter_routes_pdf_bytes_before_utf8_decode() -> None:
    from media_quotes.adapters.base import SourceAdapter

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
        b"BT\n" + quote_line + b"ET\n"
        b"endstream\n"
        b"endobj\n"
        b"trailer << /Root 1 0 R >>\n"
        b"%%EOF"
    )
    adapter = SourceAdapter(row=None, replay=True)  # type: ignore[arg-type]
    parsed = adapter.parse_metadata(
        pdf_content, source_url="https://thinktank.example.org/report.pdf"
    )
    assert parsed["extraction_method"] == "pdf_document_v1"
    assert "Security protocols require enhanced monitoring." in parsed["article_text"]
