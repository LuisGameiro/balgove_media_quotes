r"""Interactive CLI diagnostic tool for testing newspaper scraping, RSS/Sitemap discovery,
Trafilatura + JSON-LD extraction, quote/speaker attribution, and 2-stage deterministic verification.

Usage examples (PowerShell):
  1. Run built-in end-to-end demo (RSS + Sitemap + realistic newspaper HTML + 2-stage verifier):
     .\.venv\Scripts\python.exe scripts/test_scraper.py

  2. Test scraping on any local HTML file:
     .\.venv\Scripts\python.exe scripts/test_scraper.py `
       --html-file data/fixtures/reuters/synthetic-reuters-2026-11-18.html

  3. Test scraping on a live permitted URL (requires --live and an enabled source_id):
     .\.venv\Scripts\python.exe scripts/test_scraper.py `
       --live --source-id guardian --url https://www.theguardian.com/world/...
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from media_quotes.adapters import (
    BlobCache,
    build_adapter,
    parse_newspaper_html,
    parse_rss_feed,
    parse_sitemap_xml,
)
from media_quotes.config import get_settings
from media_quotes.fetch import FetchPolicy, fetch_bytes
from media_quotes.models import QuoteCandidate
from media_quotes.registry import SourceRegistryRow, load_sources_csv
from media_quotes.verification_stages import verify_pipeline

DEMO_RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Guardian World News RSS</title>
    <link>https://www.theguardian.com/world</link>
    <item>
      <title>European partners accelerate air defence deliveries ahead of winter</title>
      <link>https://www.theguardian.com/world/2026/nov/19/air-defence-winter</link>
      <pubDate>Thu, 19 Nov 2026 11:20:00 +0000</pubDate>
    </item>
    <item>
      <title>Old article outside 7-day window</title>
      <link>https://www.theguardian.com/world/2026/oct/01/old-report</link>
      <pubDate>Thu, 01 Oct 2026 09:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""

DEMO_SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
  <url>
    <loc>https://apnews.com/article/ukraine-ammunition-production-2026-11-19</loc>
    <news:news>
      <news:publication>
        <news:name>Associated Press</news:name>
        <news:language>en</news:language>
      </news:publication>
      <news:publication_date>2026-11-19T09:00:00Z</news:publication_date>
      <news:title>Ammunition production contracts signed for 2027</news:title>
    </news:news>
  </url>
</urlset>
"""

DEMO_NEWSPAPER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>European partners accelerate air defence deliveries — The Guardian</title>
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
  <header><nav>News | Opinion | Sport | Culture | Lifestyle</nav></header>
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
  <footer><p>All rights reserved.</p></footer>
</body>
</html>
"""


def _load_source_row(source_id: str) -> SourceRegistryRow:
    settings = get_settings()
    rows = {row.source_id: row for row in load_sources_csv(settings.sources_csv)}
    if source_id not in rows:
        raise ValueError(f"Unknown source_id '{source_id}'. Available: {sorted(rows)}")
    return rows[source_id]


def inspect_html_pipeline(
    raw_html: str,
    *,
    source_row: SourceRegistryRow,
    source_url: str,
    window_start: date,
    window_end: date,
) -> dict[str, Any]:
    """Run Trafilatura + JSON-LD extraction, quote detection, and 2-stage verification."""
    parsed = parse_newspaper_html(raw_html, source_url=source_url)
    text = str(parsed.get("article_text") or "")
    canonical_url = str(parsed.get("canonical_url") or source_url)
    spans = parsed.get("quote_spans", [])

    candidates = [
        QuoteCandidate(
            quote_text=str(span["quote_text"]),
            article_id=uuid5(NAMESPACE_URL, canonical_url),
            char_start=int(span["char_start"]),
            char_end=int(span["char_end"]),
            dom_path=str(span.get("dom_path") or ""),
            speaker=str(span.get("speaker") or "") or None,
            attribution_span=str(span.get("attribution_span") or "") or None,
        )
        for span in spans
        if isinstance(span, dict)
    ]

    adapter = build_adapter(source_row, replay=not source_row.usable)
    policy_flags = dict(adapter.policy_flags())
    policy_flags["original_reporting_flag"] = "first-hand"

    settings = get_settings()
    outcome = verify_pipeline(
        candidates,
        text=text,
        source_row=source_row,
        policy_flags=policy_flags,
        metadata=parsed,
        window_start=window_start,
        window_end=window_end,
        max_quote_words=settings.max_quote_words,
    )

    quotes_report: list[dict[str, Any]] = []
    for idx, span in enumerate(spans):
        q_text = str(span["quote_text"])
        c_start = int(span["char_start"])
        c_end = int(span["char_end"])
        slice_matches = text[c_start:c_end] == q_text
        v_status = outcome.results[idx].status if idx < len(outcome.results) else "filtered_stage1"
        v_reasons = list(outcome.results[idx].reasons) if idx < len(outcome.results) else []
        quotes_report.append(
            {
                "index": idx + 1,
                "quote_text": q_text,
                "word_count": len(q_text.split()),
                "speaker": span.get("speaker"),
                "attribution_span": span.get("attribution_span"),
                "section_id": span.get("section_id"),
                "char_start": c_start,
                "char_end": c_end,
                "exact_offset_slice_match": slice_matches,
                "verification_status": v_status,
                "verification_reasons": v_reasons,
            }
        )

    return {
        "source_id": source_row.source_id,
        "t0_gate_permitted": source_row.usable,
        "rights_status": str(source_row.rights_status),
        "license_reference": source_row.license_reference or "(none)",
        "metadata": {
            "title": parsed.get("title"),
            "canonical_url": canonical_url,
            "publication_date": parsed.get("publication_date"),
            "byline": parsed.get("byline"),
            "publication_name": parsed.get("publication_name"),
            "extraction_method": parsed.get("extraction_method"),
            "extractor_version": parsed.get("extractor_version"),
            "quality_ok": parsed.get("quality_ok"),
            "quality_reasons": parsed.get("quality_reasons"),
            "extracted_text_length": len(text),
        },
        "quotes_detected": len(quotes_report),
        "quotes_verified_publishable": len(outcome.publishable),
        "stage1_prefilter_rejected": dict(outcome.prefilter.rejected),
        "quotes": quotes_report,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Test newspaper discovery, Trafilatura scraping & 2-stage verification."
    )
    parser.add_argument(
        "--source-id",
        default="guardian",
        help="Source ID from data/sources.csv (default: guardian)",
    )
    parser.add_argument(
        "--html-file",
        type=Path,
        default=None,
        help="Optional path to a local HTML file to scrape and verify",
    )
    parser.add_argument(
        "--rss-file",
        type=Path,
        default=None,
        help="Optional path to a local RSS/Atom XML file to test feed discovery",
    )
    parser.add_argument(
        "--sitemap-file",
        type=Path,
        default=None,
        help="Optional path to a local Sitemap XML file to test sitemap discovery",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Fetch a live URL over HTTP (enforces T0 gate + FetchPolicy 1 req/s rate limit)",
    )
    parser.add_argument(
        "--url",
        default="",
        help="Article or feed URL when using --live (or override canonical_url for local HTML)",
    )
    args = parser.parse_args(argv)

    row = _load_source_row(args.source_id)
    window_start = date(2026, 11, 13)
    window_end = date(2026, 11, 19)

    if args.rss_file:
        raw_xml = args.rss_file.read_text(encoding="utf-8")
        items = parse_rss_feed(raw_xml)
        print(json.dumps({"mode": "rss_file", "discovered": items}, indent=2, ensure_ascii=False))
        return 0

    if args.sitemap_file:
        raw_xml = args.sitemap_file.read_text(encoding="utf-8")
        items = parse_sitemap_xml(raw_xml)
        print(
            json.dumps({"mode": "sitemap_file", "discovered": items}, indent=2, ensure_ascii=False)
        )
        return 0

    if args.live:
        if not args.url:
            raise SystemExit("--live requires --url <https://...>")
        raw_bytes, sha256 = asyncio.run(
            fetch_bytes(
                args.url,
                row=row,
                policy=FetchPolicy(),
                track_a_approved=row.usable,
            )
        )
        cache = BlobCache()
        _, blob_path = cache.store(row.source_id, raw_bytes)
        report = inspect_html_pipeline(
            raw_bytes.decode("utf-8", errors="replace"),
            source_row=row,
            source_url=args.url,
            window_start=date(2020, 1, 1),
            window_end=date(2030, 12, 31),
        )
        report["live_fetch"] = {"sha256": sha256, "cached_blob": str(blob_path)}
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    if args.html_file:
        raw_html = args.html_file.read_text(encoding="utf-8")
        report = inspect_html_pipeline(
            raw_html,
            source_row=row,
            source_url=args.url or "https://example.invalid/local-html",
            window_start=date(2020, 1, 1),
            window_end=date(2030, 12, 31),
        )
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    # Default: Built-in end-to-end diagnostic demo (RSS + Sitemap + HTML + 2-Stage Verifier)
    rss_found = parse_rss_feed(DEMO_RSS_XML, start=window_start, end=window_end)
    sitemap_found = parse_sitemap_xml(DEMO_SITEMAP_XML, start=window_start, end=window_end)
    scrape_report = inspect_html_pipeline(
        DEMO_NEWSPAPER_HTML,
        source_row=row,
        source_url="https://www.theguardian.com/world/2026/nov/19/air-defence-winter",
        window_start=window_start,
        window_end=window_end,
    )
    full_demo = {
        "1_rss_discovery_demo": {
            "window": f"{window_start}..{window_end}",
            "articles_discovered": rss_found,
        },
        "2_google_news_sitemap_discovery_demo": {
            "window": f"{window_start}..{window_end}",
            "articles_discovered": sitemap_found,
        },
        "3_newspaper_html_scrape_and_verification_demo": scrape_report,
    }
    print(json.dumps(full_demo, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
