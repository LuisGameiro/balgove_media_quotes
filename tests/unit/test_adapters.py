from datetime import date
from pathlib import Path
from typing import Any

import pytest

from media_quotes.adapters import (
    BlobCache,
    FixtureSource,
    SourceAdapter,
    SourceNotPermitted,
    build_adapter,
    parse_rss_feed,
    parse_sitemap_xml,
)
from media_quotes.enums import DISCOVERY_METHODS
from media_quotes.llm_live import (
    escape_quote_delimiter,
    is_refusal_text,
    validate_pinned_snapshot,
)
from media_quotes.registry import load_sources_csv

SOURCES_CSV = Path(__file__).resolve().parents[2] / "data" / "sources.csv"
ROWS = {row.source_id: row for row in load_sources_csv(SOURCES_CSV)}


def test_factory_builds_adapter_for_every_registered_method() -> None:
    for method in DISCOVERY_METHODS:
        row = ROWS["reuters"].model_copy(update={"discovery_method": method})
        assert isinstance(build_adapter(row), SourceAdapter)


def test_blocked_source_cannot_discover() -> None:
    # All registered sources are licensed now; use a synthetic unlicensed row.
    blocked = ROWS["reuters"].model_copy(
        update={"rights_status": "blocked", "enabled": False, "license_reference": ""}
    )
    adapter = build_adapter(blocked)
    assert adapter.permitted is False
    with pytest.raises(SourceNotPermitted):
        adapter.discover(date(2026, 10, 1), date(2026, 10, 2))


def test_web_discovery_is_never_permitted() -> None:
    web_row = ROWS["ap"].model_copy(update={"discovery_method": "web"})
    adapter = SourceAdapter(web_row)
    with pytest.raises(SourceNotPermitted):
        adapter.fetch("https://example.invalid/article")


def test_policy_flags_come_from_registry_without_network() -> None:
    adapter = build_adapter(ROWS["anadolu"])
    assert adapter.policy_flags()["claim_only"] is True
    assert adapter.policy_flags()["source_type"] == "state-controlled"


def test_licensed_row_is_permitted() -> None:
    row = ROWS["reuters"].model_copy(
        update={
            "rights_status": "license_held",
            "access_mode": "licensed_database",
            "license_reference": "LIC-1",
            "enabled": True,
            "discovery_method": "licensed_database",
        }
    )
    adapter = build_adapter(row)
    assert adapter.permitted is True
    with pytest.raises(NotImplementedError):
        adapter.fetch("licensed://article/1")


def test_rendered_fallback_is_rights_gated_and_opt_in(monkeypatch: Any) -> None:
    from media_quotes.adapters import base
    from media_quotes.source_configs import ExtractorHints

    seen: dict[str, Any] = {}

    def fake_parse(raw_html: str, **kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {}

    monkeypatch.setattr(base, "parse_newspaper_html", fake_parse)
    html = b"<html><article><p>Some article body text.</p></article></html>"
    hints = ExtractorHints(render_js=True)
    denied = ROWS["reuters"].model_copy(
        update={"enabled": False, "rights_status": "blocked", "license_reference": ""}
    )

    base.SourceAdapter(denied, extraction_fallback="rendered")._parse(
        html, use_trafilatura=True, source_url="https://example.invalid/a", extractor_hints=hints
    )
    assert seen["fallback"] is None

    base.SourceAdapter(ROWS["guardian"], extraction_fallback="rendered")._parse(
        html,
        use_trafilatura=True,
        source_url="https://example.invalid/a",
        extractor_hints=ExtractorHints(),
    )
    assert seen["fallback"] is None

    base.SourceAdapter(ROWS["guardian"], extraction_fallback="rendered")._parse(
        html, use_trafilatura=True, source_url="https://example.invalid/a", extractor_hints=hints
    )
    assert seen["fallback"] is not None


def test_fixture_parser_ignores_script_and_style_bodies() -> None:
    from media_quotes.adapters.html_extractor import _FixtureHTMLParser

    parser = _FixtureHTMLParser()
    parser.feed(
        "<html><head><title>Real headline</title>"
        '<script type="application/ld+json">'
        '{"headline":"SYNTHETIC FIXTURE - Other / Cross-cutting",'
        '"publisher":{"name":"New York Times APIs (synthetic fixture)"}}'
        '</script><style>.x { content: "css-only quote text here"; }</style></head>'
        '<body><article><p><span class="quote">Minister said "Every synthetic JSON quote must '
        'stay out of the published digest."</span></p></article></body></html>'
    )
    meta = parser.metadata()
    text = str(meta["article_text"])
    assert "SYNTHETIC FIXTURE" not in text
    assert "New York Times APIs" not in text
    assert "css-only quote text here" not in text
    assert str(meta["title"]) == "Real headline"
    assert len(meta["quote_spans"]) == 1
    assert "Every synthetic JSON quote must stay out of the published digest." in str(
        meta["quote_spans"][0]["quote_text"]
    )


def test_fixture_source_list_refs_and_read(tmp_path: Path) -> None:
    source_dir = tmp_path / "test-source"
    source_dir.mkdir(parents=True)
    (source_dir / "doc1.html").write_text("<p>Doc 1</p>", encoding="utf-8")
    (source_dir / "doc2.json").write_text("{}", encoding="utf-8")
    (source_dir / "ignored.txt").write_text("ignore", encoding="utf-8")

    fs = FixtureSource(root=tmp_path)
    refs = fs.list_refs("test-source")
    assert refs == [
        "fixture://test-source/doc1.html",
        "fixture://test-source/doc2.json",
    ]
    content = fs.read("fixture://test-source/doc1.html")
    assert content == b"<p>Doc 1</p>"

    # Nonexistent folder returns empty list
    assert fs.list_refs("nonexistent-source") == []


def test_fixture_source_path_traversal_and_invalid_refs(tmp_path: Path) -> None:
    fs = FixtureSource(root=tmp_path)
    # Missing scheme
    with pytest.raises(ValueError, match="not a fixture ref"):
        fs.read("https://example.com/file.html")
    # Invalid characters / source id
    with pytest.raises(ValueError, match="invalid fixture ref"):
        fs.read("fixture://invalid$source/doc.html")
    with pytest.raises(ValueError, match="invalid fixture ref"):
        fs.read("fixture://source/../escape.html")
    with pytest.raises(ValueError, match="invalid fixture source id"):
        fs.list_refs("../escape")

    source_dir = tmp_path / "valid-src"
    source_dir.mkdir()
    # Missing file inside valid source dir
    with pytest.raises(FileNotFoundError, match="fixture not found"):
        fs.read("fixture://valid-src/missing.html")


def test_blob_cache_store_and_read(tmp_path: Path) -> None:
    cache = BlobCache(root=tmp_path)
    data = b"<h1>Article Title</h1><p>Body text</p>"
    digest, path = cache.store("guardian", data)
    assert path.is_file()
    assert cache.read("guardian", digest) == data

    # Idempotent second store returns existing file immediately
    digest2, path2 = cache.store("guardian", data)
    assert digest2 == digest
    assert path2 == path

    # Validation guards
    with pytest.raises(ValueError, match="invalid source id"):
        cache.store("bad/source", data)
    with pytest.raises(ValueError, match="invalid suffix"):
        cache.store("guardian", data, suffix=".exe;rm -rf")
    with pytest.raises(ValueError, match="invalid source_id or sha256 digest"):
        cache.read("guardian", "not-a-sha256")
    with pytest.raises(FileNotFoundError, match="blob not found"):
        cache.read("guardian", "a" * 64)


def test_rss_and_sitemap_payload_limits() -> None:
    tiny_rss = (
        '<?xml version="1.0"?><rss><channel>'
        "<item><title>Item 1</title><link>https://example.com/1</link></item>"
        "<item><title>Item 2</title><link>https://example.com/2</link></item>"
        "</channel></rss>"
    )
    # max_items cap
    items = parse_rss_feed(tiny_rss, max_items=1)
    assert len(items) == 1
    assert items[0]["url"] == "https://example.com/1"

    # max_bytes cap
    with pytest.raises(ValueError, match="exceeds size limit"):
        parse_rss_feed(tiny_rss, max_bytes=10)

    tiny_sitemap = (
        '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://example.com/a</loc></url>"
        "<url><loc>https://example.com/b</loc></url>"
        "</urlset>"
    )
    entries = parse_sitemap_xml(tiny_sitemap, max_items=1)
    assert len(entries) == 1
    assert entries[0]["url"] == "https://example.com/a"

    with pytest.raises(ValueError, match="exceeds size limit"):
        parse_sitemap_xml(tiny_sitemap, max_bytes=10)


def test_escape_quote_delimiter_and_refusal_heuristics() -> None:
    # Delimiter escaping
    assert escape_quote_delimiter("<quote>hello</quote>") == "&lt;quote>hello&lt;/quote>"
    assert (
        escape_quote_delimiter("<QUOTE attr='1'>test</QUOTE>")
        == "&lt;QUOTE attr='1'>test&lt;/QUOTE>"
    )
    assert escape_quote_delimiter("Normal text without tags") == "Normal text without tags"

    # Refusal heuristic precision
    assert is_refusal_text("I refuse to answer this.") is True
    assert is_refusal_text("As an AI model, I cannot complete this task.") is True
    assert is_refusal_text("I must refuse to fulfill this request.") is True

    # Valid news content discussing refusal is NOT flagged as model refusal
    assert is_refusal_text('{"summary": "The minister refused to sign the treaty."}') is False
    assert (
        is_refusal_text("The minister refused to sign the treaty at yesterday's summit.") is False
    )
    assert is_refusal_text("", message_refusal="explicit refusal") is True

    # Pin validation
    assert (
        validate_pinned_snapshot("openai/gpt-4o-mini-2024-07-18") == "openai/gpt-4o-mini-2024-07-18"
    )
    from media_quotes.exceptions import ConfigError

    for bad in ("", "unset", "latest", "null", "none"):
        with pytest.raises(ConfigError, match="must be pinned"):
            validate_pinned_snapshot(bad)
