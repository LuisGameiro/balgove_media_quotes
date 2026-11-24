"""Unit tests for newspaper discovery (RSS/Atom, Google News Sitemap), Trafilatura + JSON-LD
HTML scraping, multi-format quote & speaker extraction, and content-addressed BlobCache.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest
from scripts.test_scraper import (
    format_live_fetch_error,
    run_live_feed,
    validate_all_sources,
    validate_single_source,
)

from media_quotes.adapters import (
    BlobCache,
    RssAdapter,
    SitemapAdapter,
    SourceNotPermitted,
    build_adapter,
    extract_json_ld_metadata,
    parse_newspaper_html,
    parse_rss_feed,
    parse_sitemap_xml,
)
from media_quotes.models import QuoteCandidate
from media_quotes.registry import load_sources_csv
from media_quotes.verification_stages import verify_pipeline

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCES_CSV = REPO_ROOT / "data" / "sources.csv"
ROWS = {row.source_id: row for row in load_sources_csv(SOURCES_CSV)}

SAMPLE_RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Guardian News Feed</title>
    <link>https://www.theguardian.com</link>
    <item>
      <title>Air defence coalition expands support ahead of winter</title>
      <link>https://www.theguardian.com/world/2026/nov/18/air-defence</link>
      <pubDate>Wed, 18 Nov 2026 10:30:00 +0000</pubDate>
    </item>
    <item>
      <title>Out of window article from September</title>
      <link>https://www.theguardian.com/world/2026/sep/01/old</link>
      <pubDate>Tue, 01 Sep 2026 08:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""

SAMPLE_ATOM_XML = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>AP News Atom Feed</title>
  <entry>
    <title>Sanctions enforcement targets dual-use supply chains</title>
    <link rel="alternate" href="https://apnews.com/article/sanctions-2026-11-19"/>
    <published>2026-11-19T14:15:00Z</published>
  </entry>
</feed>
"""

SAMPLE_GOOGLE_NEWS_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
  <url>
    <loc>https://www.pravda.com.ua/eng/news/2026/11/19/7489991/</loc>
    <news:news>
      <news:publication>
        <news:name>Ukrainska Pravda</news:name>
        <news:language>en</news:language>
      </news:publication>
      <news:publication_date>2026-11-19T09:00:00+02:00</news:publication_date>
      <news:title>Ammunition production contracts signed for 2027</news:title>
    </news:news>
  </url>
  <url>
    <loc>https://www.pravda.com.ua/eng/news/2026/10/01/7480000/</loc>
    <lastmod>2026-10-01</lastmod>
  </url>
</urlset>
"""

REALISTIC_NEWSPAPER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>European partners accelerate air defence deliveries ahead of winter</title>
  <meta property="og:site_name" content="The Guardian">
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "headline": "European partners accelerate air defence deliveries ahead of winter",
    "datePublished": "2026-11-19T11:20:00Z",
    "url": "https://www.theguardian.com/world/2026/nov/19/air-defence-winter",
    "author": [{"@type": "Person", "name": "Olena Kovalenko"}],
    "publisher": {"@type": "Organization", "name": "The Guardian"}
  }
  </script>
</head>
<body>
  <header><nav>Home | War | Politics | Subscribe</nav></header>
  <main>
    <article>
      <h1>European partners accelerate air defence deliveries ahead of winter</h1>
      <p>KYIV — Ukraine and its European partners have finalized an expedited schedule for
      delivering interceptor missiles and mobile radar units before temperatures drop below
      freezing across the country's eastern and southern regions, officials confirmed on
      Thursday.</p>
      <p>Speaking after a joint defense ministry briefing in Kyiv, Defense Minister Rustem
      Umerov said “Every additional air defence battery deployed this month directly protects
      critical energy infrastructure and civilian heating networks across our cities.”</p>
      <p>European logistics hubs have already processed the first shipment of interceptors
      under the new bilateral framework agreed earlier in November, according to defense
      officials familiar with the transfer schedule.</p>
      <p>«Ми також збільшуємо власне виробництво боєприпасів та дронів-перехоплювачів на
      двадцять відсотків цього кварталу,» заявив Денис Шмигаль під час засідання уряду.</p>
      <p>“Our joint production lines are operating around the clock to ensure uninterrupted
      supplies of artillery ammunition and spare parts throughout the winter campaign,” added
      Prime Minister Denys Shmyhal.</p>
    </article>
  </main>
  <footer><p>Copyright 2026 The Guardian.</p></footer>
</body>
</html>
"""


def test_parse_rss_and_atom_feeds_with_date_window() -> None:
    rss_entries = parse_rss_feed(SAMPLE_RSS_XML, start=date(2026, 11, 13), end=date(2026, 11, 19))
    assert len(rss_entries) == 1
    assert rss_entries[0]["url"] == "https://www.theguardian.com/world/2026/nov/18/air-defence"
    assert rss_entries[0]["publication_date"] == "2026-11-18"
    assert "Air defence coalition" in rss_entries[0]["title"]

    atom_entries = parse_rss_feed(SAMPLE_ATOM_XML, start=date(2026, 11, 13), end=date(2026, 11, 19))
    assert len(atom_entries) == 1
    assert atom_entries[0]["url"] == "https://apnews.com/article/sanctions-2026-11-19"
    assert atom_entries[0]["publication_date"] == "2026-11-19"


def test_parse_google_news_sitemap_with_date_window() -> None:
    entries = parse_sitemap_xml(
        SAMPLE_GOOGLE_NEWS_SITEMAP, start=date(2026, 11, 13), end=date(2026, 11, 19)
    )
    assert len(entries) == 1
    assert entries[0]["url"] == "https://www.pravda.com.ua/eng/news/2026/11/19/7489991/"
    assert entries[0]["publication_date"] == "2026-11-19"
    assert entries[0]["title"] == "Ammunition production contracts signed for 2027"


def test_sitemap_index_loc_is_not_an_article() -> None:
    index = (
        "<sitemapindex><sitemap><loc>https://www.theguardian.com/sitemap-ukraine.xml</loc>"
        "</sitemap></sitemapindex>"
    )
    assert parse_sitemap_xml(index, require_topic=True) == []


def test_rss_and_sitemap_adapters_enforce_t0_gate_before_xml_discovery() -> None:
    rss_row = ROWS["guardian"].model_copy(
        update={"discovery_method": "rss", "access_mode": "public_rss_permitted"}
    )
    rss_adapter = build_adapter(rss_row)
    assert isinstance(rss_adapter, RssAdapter)
    discovered = rss_adapter.discover_from_xml(
        SAMPLE_RSS_XML, date(2026, 11, 13), date(2026, 11, 19)
    )
    assert len(discovered) == 1

    sitemap_row = ROWS["ap"].model_copy(update={"discovery_method": "sitemap"})
    sitemap_adapter = build_adapter(sitemap_row)
    assert isinstance(sitemap_adapter, SitemapAdapter)
    sitemap_discovered = sitemap_adapter.discover_from_xml(
        SAMPLE_GOOGLE_NEWS_SITEMAP, date(2026, 11, 13), date(2026, 11, 19)
    )
    assert len(sitemap_discovered) == 1

    blocked_bbc = build_adapter(
        ROWS["bbc"].model_copy(
            update={"rights_status": "blocked", "license_reference": "", "enabled": False}
        )
    )
    assert isinstance(blocked_bbc, RssAdapter)
    with pytest.raises(SourceNotPermitted):
        blocked_bbc.discover_from_xml(SAMPLE_RSS_XML, date(2026, 11, 13), date(2026, 11, 19))


def test_extract_json_ld_metadata() -> None:
    meta = extract_json_ld_metadata(REALISTIC_NEWSPAPER_HTML)
    assert meta["title"] == "European partners accelerate air defence deliveries ahead of winter"
    assert meta["publication_date"] == "2026-11-19"
    assert (
        meta["canonical_url"] == "https://www.theguardian.com/world/2026/nov/19/air-defence-winter"
    )
    assert meta["byline"] == "Olena Kovalenko"
    assert meta["publication_name"] == "The Guardian"


def test_parse_newspaper_html_extracts_clean_body_and_multi_format_quotes() -> None:
    parsed = parse_newspaper_html(
        REALISTIC_NEWSPAPER_HTML,
        source_url="https://www.theguardian.com/world/2026/nov/19/air-defence-winter",
    )
    assert parsed["quality_ok"] is True
    assert parsed["extraction_method"] == "trafilatura_dom_v2"
    assert parsed["publication_date"] == "2026-11-19"
    assert parsed["byline"] == "Olena Kovalenko"
    assert parsed["publication_name"] == "The Guardian"

    text = parsed["article_text"]
    spans = parsed["quote_spans"]
    assert len(spans) == 3

    for span in spans:
        q_text = span["quote_text"]
        c_start = span["char_start"]
        c_end = span["char_end"]
        assert text[c_start:c_end] == q_text
        assert span["speaker"] is not None

    speakers = [span["speaker"] for span in spans]
    assert any("Rustem Umerov" in s for s in speakers if s)
    assert any("Денис Шмигаль" in s for s in speakers if s)
    assert any("Denys Shmyhal" in s for s in speakers if s)


def test_scraped_newspaper_quotes_pass_two_stage_deterministic_verifier() -> None:
    guardian_row = ROWS["guardian"]
    adapter = build_adapter(guardian_row)
    metadata = adapter.parse_metadata(
        REALISTIC_NEWSPAPER_HTML.encode("utf-8"),
        use_trafilatura=True,
        source_url="https://www.theguardian.com/world/2026/nov/19/air-defence-winter",
    )
    text = metadata["article_text"]
    candidates = [
        QuoteCandidate(
            quote_text=span["quote_text"],
            article_id=uuid5(NAMESPACE_URL, metadata["canonical_url"]),
            char_start=span["char_start"],
            char_end=span["char_end"],
            dom_path=span["dom_path"],
            speaker=span["speaker"],
            attribution_span=span["attribution_span"] or None,
        )
        for span in metadata["quote_spans"]
    ]
    policy_flags = dict(adapter.policy_flags())
    policy_flags["original_reporting_flag"] = "first-hand"

    outcome = verify_pipeline(
        candidates,
        text=text,
        source_row=guardian_row,
        policy_flags=policy_flags,
        metadata=metadata,
        window_start=date(2026, 11, 13),
        window_end=date(2026, 11, 19),
        max_quote_words=30,
    )
    assert len(outcome.publishable) == 3
    assert all(res.status == "verified" for res in outcome.results)


def test_blob_cache_stores_and_reads_content_addressed_html(tmp_path: Path) -> None:
    cache = BlobCache(tmp_path / "blobs")
    raw_bytes = REALISTIC_NEWSPAPER_HTML.encode("utf-8")
    digest, path = cache.store("guardian", raw_bytes)
    assert len(digest) == 64
    assert path.is_file()
    assert cache.read("guardian", digest) == raw_bytes

    with pytest.raises(ValueError, match="invalid source id"):
        cache.store("../escape", raw_bytes)


def test_validate_single_source_and_t0_safety() -> None:
    # 1. All registered sources are licensed; Reuters is now T0-permitted.
    reuters_card = validate_single_source("reuters")
    assert reuters_card["tier"] == 1
    assert reuters_card["t0_permitted"] is True
    assert reuters_card["t0_security_boundary_verified"] is True
    assert reuters_card["fixtures"]["count"] >= 1
    assert "ok" in reuters_card["fixtures"]["replay_status"]

    # 2. Permitted source Guardian
    guardian_card = validate_single_source("guardian")
    assert guardian_card["tier"] == 2
    assert guardian_card["t0_permitted"] is True
    assert guardian_card["feed"]["live_supported"] is True


def test_validate_all_sources_summary() -> None:
    summary = validate_all_sources()
    assert summary["total_sources"] == 28
    assert summary["t0_blocked_sources"] == 5  # tier-4 claim-only rows stay disabled
    assert summary["t0_permitted_sources"] == 23
    assert len(summary["sources"]) == 28


def test_format_live_fetch_error_404_is_actionable() -> None:
    message = format_live_fetch_error(
        "https://www.theguardian.com/world/2024/nov/20/sample-article-slug",
        404,
        "Not Found",
        "guardian",
    )
    assert "Live fetch failed: 404 Not Found" in message
    assert "placeholder or stale" in message.lower()
    assert "--live-feed" in message
    assert "--html-file" in message


def test_format_live_fetch_error_other_status_keeps_status() -> None:
    message = format_live_fetch_error(
        "https://example.invalid/article", 503, "Service Unavailable", "guardian"
    )
    assert "Live fetch failed: 503 Service Unavailable" in message
    assert "placeholder" not in message.lower()


def test_run_live_feed_blocked_source_safeguard() -> None:
    # All registered sources are licensed; use a synthetic unlicensed row.
    blocked_row = ROWS["bbc"].model_copy(
        update={"rights_status": "blocked", "license_reference": "", "enabled": False}
    )
    result = run_live_feed(blocked_row)
    assert result["status"] == "blocked_by_t0_gate"
    assert result["t0_gate_permitted"] is False
