r"""Interactive CLI diagnostic tool for testing newspaper scraping, RSS/Sitemap discovery,
Trafilatura + JSON-LD extraction, quote/speaker attribution, and 2-stage deterministic verification.

Usage examples (PowerShell):
  1. Built-in end-to-end demo (RSS + Sitemap + realistic newspaper HTML + 2-stage verifier):
     .\.venv\Scripts\python.exe scripts/test_scraper.py

  2. Get news of the day live from The Guardian (fetches live RSS + top articles + quotes):
     .\.venv\Scripts\python.exe scripts/test_scraper.py `
       --source-id guardian --live-feed --limit 2

  3. Validate data getting for a single source (T0 gate, fixtures, and feed connectivity):
     .\.venv\Scripts\python.exe scripts/test_scraper.py --validate-source guardian
     .\.venv\Scripts\python.exe scripts/test_scraper.py --validate-source reuters

  4. Audit data getting readiness across all 28 sources in the registry:
     .\.venv\Scripts\python.exe scripts/test_scraper.py --validate-all-sources

  5. Test a single real article page (ONLY use a URL discovered by --live-feed):
     .venv/Scripts/python.exe scripts/test_scraper.py `
       --source-id guardian --live --url <real-article-url>

  6. Test scraping on local sample HTML (my_article.html) or fixture HTML:
     .\.venv\Scripts\python.exe scripts/test_scraper.py `
       --source-id guardian --html-file my_article.html
     .\.venv\Scripts\python.exe scripts/test_scraper.py `
       --source-id reuters --html-file data/fixtures/reuters/synthetic-reuters-2026-11-18.html

  7. Test RSS or Sitemap discovery on sample XML files:
     .\.venv\Scripts\python.exe scripts/test_scraper.py --rss-file data/samples/sample_feed.xml
     .\.venv\Scripts\python.exe scripts/test_scraper.py `
       --sitemap-file data/samples/sample_sitemap.xml
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import httpx

from media_quotes.adapters import (
    BlobCache,
    build_adapter,
    is_ukraine_war_relevant,
    parse_newspaper_html,
    parse_rss_feed,
    parse_sitemap_xml,
)
from media_quotes.config import get_settings
from media_quotes.exceptions import SourceNotPermitted
from media_quotes.fetch import FetchPolicy, fetch_bytes
from media_quotes.models import QuoteCandidate
from media_quotes.registry import SourceRegistryRow, load_sources_csv
from media_quotes.verification_stages import verify_pipeline

# Public discovery feeds for sources that offer them
KNOWN_FEEDS: dict[str, str] = {
    "guardian": "https://www.theguardian.com/world/ukraine/rss",
    "dw": "https://rss.dw.com/rdf/rss-en-all",
    "ap": "https://apnews.com/rss",
    "ukrainskapravda": "https://www.pravda.com.ua/rss/",
    "suspilne": "https://suspilne.media/rss/",
    "kyivindependent": "https://kyivindependent.com/feed/",
    "interfaxukraine": "https://en.interfax.com.ua/news/general.rss",
    "aljazeera": "https://www.aljazeera.com/xml/rss/all.xml",
    "anadolu": "https://www.aa.com.tr/en/rss/default?cat=live",
    "bbc": "https://feeds.bbci.co.uk/news/rss.xml",
}

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


def _list_available_fixture_html() -> list[str]:
    settings = get_settings()
    if not settings.fixtures_dir.exists():
        return []
    return [
        str(p.relative_to(Path.cwd())).replace("\\", "/")
        if p.is_relative_to(Path.cwd())
        else str(p)
        for p in sorted(settings.fixtures_dir.rglob("*.html"))
    ]


def format_live_fetch_error(url: str, status: int, reason: str, source_id: str) -> str:
    """Explain a failed live article fetch without hiding the HTTP status."""
    script = ".\\.venv\\Scripts\\python.exe scripts/test_scraper.py"
    lines = [
        f"Live fetch failed: {status} {reason}",
        f"URL: {url}",
        "",
    ]
    if status == 404:
        lines.extend(
            [
                "This looks like a placeholder or stale article URL. Do not invent slugs.",
                "Discover a real article URL first:",
                f"  {script} --source-id {source_id} --validate-source",
                f"  {script} --source-id {source_id} --live-feed --limit 2",
                "Then pass an exact discovered URL (articles[].url):",
                f"  {script} --source-id {source_id} --live --url <real-article-url>",
                "",
                "To test extraction from saved HTML instead:",
                f"  {script} --source-id {source_id} --html-file my_article.html",
            ]
        )
    else:
        lines.append("Check the URL, source rights, and network before retrying.")
    return "\n".join(lines)


def _read_required_file(path: Path, flag_name: str) -> str:
    if not path.exists():
        sample_xml = {
            "--rss-file": "data/samples/sample_feed.xml",
            "--sitemap-file": "data/samples/sample_sitemap.xml",
        }.get(flag_name)
        if sample_xml:
            cmd = f".\\.venv\\Scripts\\python.exe scripts/test_scraper.py {flag_name} {sample_xml}"
            hint_lines = [
                f"ERROR: File not found for {flag_name}: '{path}'",
                "",
                f"Tip: Pass an existing XML file, such as {sample_xml}:",
                f"  {cmd}",
            ]
        else:
            available = _list_available_fixture_html()
            cmd = (
                ".\\.venv\\Scripts\\python.exe scripts/test_scraper.py "
                f"--write-sample {path} --html-file {path}"
            )
            hint_lines = [
                f"ERROR: File not found for {flag_name}: '{path}'",
                "",
                "Tip: Pass an existing HTML file (e.g. my_article.html), or generate one:",
                f"  {cmd}",
            ]
            if available:
                hint_lines.append("")
                hint_lines.append("Existing HTML fixture files in data/fixtures/:")
                for item in available:
                    hint_lines.append(f"  - {item}")
        print("\n".join(hint_lines), file=sys.stderr)
        raise SystemExit(2)
    return path.read_text(encoding="utf-8")


def inspect_html_pipeline(
    raw_html: str,
    *,
    source_row: SourceRegistryRow,
    source_url: str,
    window_start: date,
    window_end: date,
    require_topic: bool = True,
) -> dict[str, Any]:
    """Run Trafilatura + JSON-LD extraction, quote detection, and 2-stage verification."""
    parsed = parse_newspaper_html(
        raw_html, source_url=source_url, require_ukraine_topic=require_topic
    )
    text = str(parsed.get("article_text") or "")
    canonical_url = str(parsed.get("canonical_url") or source_url)
    topic_ok = bool(parsed.get("topic_relevant", True))

    if require_topic and not topic_ok:
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
                "topic_relevant": False,
            },
            "topic_status": "rejected_off_topic (only Russia-Ukraine war articles are accepted)",
            "quotes_detected": 0,
            "quotes_verified_publishable": 0,
            "stage1_prefilter_rejected": {"off_topic": len(parsed.get("quote_spans", []))},
            "quotes": [],
        }

    raw_spans = parsed.get("quote_spans", [])
    if require_topic:
        spans = [
            s
            for s in raw_spans
            if s.get("section_id") != "other"
            or is_ukraine_war_relevant(str(s.get("quote_text") or ""))
        ]
    else:
        spans = raw_spans

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

    # Map stage-2 results by (char_start, char_end) so status matches even if stage-1 filters one
    result_by_span = {
        (res.candidate.char_start, res.candidate.char_end): res for res in outcome.results
    }

    quotes_report: list[dict[str, Any]] = []
    for idx, span in enumerate(spans):
        q_text = str(span["quote_text"])
        c_start = int(span["char_start"])
        c_end = int(span["char_end"])
        slice_matches = text[c_start:c_end] == q_text
        res = result_by_span.get((c_start, c_end))
        v_status = res.status if res is not None else "filtered_stage1"
        v_reasons = list(res.reasons) if res is not None else []
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
            "topic_relevant": topic_ok,
        },
        "topic_status": "accepted_ukraine_war_domain" if topic_ok else "off_topic",
        "quotes_detected": len(quotes_report),
        "quotes_verified_publishable": len(outcome.publishable),
        "stage1_prefilter_rejected": dict(outcome.prefilter.rejected),
        "quotes": quotes_report,
    }


def run_live_feed(
    row: SourceRegistryRow,
    feed_url_override: str = "",
    limit: int = 2,
    require_topic: bool = True,
) -> dict[str, Any]:
    """Fetch live feed for source, discover articles, and scrape top N articles."""
    feed_url = feed_url_override or row.feed_url or KNOWN_FEEDS.get(row.source_id, "")
    if not feed_url:
        return {
            "source_id": row.source_id,
            "status": "error_no_feed_url",
            "message": f"No feed URL registered or known for source '{row.source_id}'. "
            "Pass --feed-url <https://...> to specify one.",
        }

    if not row.usable:
        return {
            "source_id": row.source_id,
            "t0_gate_permitted": False,
            "rights_status": str(row.rights_status),
            "status": "blocked_by_t0_gate",
            "feed_url": feed_url,
            "message": (
                "Source is blocked under T0 rights policy. Automated live fetching is strictly "
                "prohibited without verified written licence. Replay fixture mode is used instead."
            ),
        }

    # Fetch live feed XML
    raw_feed_bytes, _ = asyncio.run(
        fetch_bytes(feed_url, row=row, policy=FetchPolicy(), track_a_approved=row.usable)
    )
    raw_feed_text = raw_feed_bytes.decode("utf-8", errors="replace")

    if "<urlset" in raw_feed_text or "<sitemapindex" in raw_feed_text:
        discovered = parse_sitemap_xml(raw_feed_text, require_ukraine_topic=require_topic)
    else:
        discovered = parse_rss_feed(raw_feed_text, require_ukraine_topic=require_topic)

    cache = BlobCache()
    articles_results: list[dict[str, Any]] = []

    for item in discovered[: max(1, limit)]:
        a_url = item["url"]
        try:
            a_bytes, _ = asyncio.run(
                fetch_bytes(a_url, row=row, policy=FetchPolicy(), track_a_approved=row.usable)
            )
            _, blob_path = cache.store(row.source_id, a_bytes)
            rep = inspect_html_pipeline(
                a_bytes.decode("utf-8", errors="replace"),
                source_row=row,
                source_url=a_url,
                window_start=date(2020, 1, 1),
                window_end=date(2030, 12, 31),
                require_topic=require_topic,
            )
            rep["url"] = a_url
            rep["cached_blob"] = str(blob_path)
            articles_results.append(rep)
        except Exception as exc:
            articles_results.append(
                {
                    "url": a_url,
                    "title": item.get("title"),
                    "status": "fetch_or_parse_error",
                    "error": str(exc),
                }
            )

    return {
        "source_id": row.source_id,
        "source_name": row.source_name,
        "feed_url": feed_url,
        "t0_gate_permitted": True,
        "articles_discovered_in_feed": len(discovered),
        "articles_scraped_and_verified": len(articles_results),
        "articles": articles_results,
    }


def validate_single_source(source_id: str) -> dict[str, Any]:
    """Audit single source ingestion: registry, T0 boundary, fixtures, and feed connectivity."""
    settings = get_settings()
    row = _load_source_row(source_id)

    # 1. Fixture check
    fixture_dir = settings.fixtures_dir / source_id
    fixture_files = sorted(fixture_dir.glob("*.html")) if fixture_dir.exists() else []

    # 2. Known feed
    known_feed = row.feed_url or KNOWN_FEEDS.get(source_id, "")

    # 3. T0 enforcement check: an unapproved probe must be refused for every source.
    t0_boundary_intact = False
    try:
        asyncio.run(
            fetch_bytes(
                "https://example.invalid/probe",
                row=row,
                policy=FetchPolicy(),
                track_a_approved=False,
            )
        )
    except SourceNotPermitted:
        t0_boundary_intact = True

    # 4. Replay pipeline validation
    replay_status = "no_fixtures"
    quotes_in_fixture = 0
    if fixture_files:
        try:
            sample_html = fixture_files[0].read_text(encoding="utf-8")
            rep = inspect_html_pipeline(
                sample_html,
                source_row=row,
                source_url=f"https://{source_id}.invalid/fixture",
                window_start=date(2020, 1, 1),
                window_end=date(2030, 12, 31),
            )
            quotes_in_fixture = rep.get("quotes_verified_publishable", 0)
            replay_status = f"ok ({quotes_in_fixture} quotes verified)"
        except Exception as exc:
            replay_status = f"error ({exc})"

    return {
        "source_id": row.source_id,
        "source_name": row.source_name,
        "tier": row.tier,
        "rights_status": str(row.rights_status),
        "access_mode": str(row.access_mode),
        "t0_permitted": row.usable,
        "t0_security_boundary_verified": t0_boundary_intact,
        "fixtures": {
            "count": len(fixture_files),
            "files": [f.name for f in fixture_files],
            "replay_status": replay_status,
        },
        "feed": {
            "url": known_feed or "(none registered)",
            "live_supported": bool(known_feed and row.usable),
        },
    }


def validate_all_sources() -> dict[str, Any]:
    """Validate all 28 registered sources across tiers, T0 gates, and fixtures."""
    settings = get_settings()
    rows = load_sources_csv(settings.sources_csv)

    results: list[dict[str, Any]] = []
    for r in rows:
        results.append(validate_single_source(r.source_id))

    t0_blocked = sum(1 for item in results if not item["t0_permitted"])
    t0_permitted = sum(1 for item in results if item["t0_permitted"])
    with_fixtures = sum(1 for item in results if item["fixtures"]["count"] > 0)
    with_feeds = sum(1 for item in results if item["feed"]["live_supported"])

    return {
        "total_sources": len(results),
        "t0_permitted_sources": t0_permitted,
        "t0_blocked_sources": t0_blocked,
        "sources_with_fixtures": with_fixtures,
        "sources_with_live_feeds": with_feeds,
        "sources": results,
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
        "--live-feed",
        action="store_true",
        help="Fetch live RSS feed for --source-id, discover today's articles, and scrape quotes",
    )
    parser.add_argument(
        "--all-topics",
        action="store_true",
        help=(
            "Disable the default Russia-Ukraine war topic filter "
            "to inspect arbitrary non-war articles"
        ),
    )
    parser.add_argument(
        "--feed-url",
        default="",
        help="Override default RSS/Sitemap feed URL for --live-feed",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=2,
        help="Maximum number of live articles to scrape and verify from the feed (default: 2)",
    )
    parser.add_argument(
        "--validate-source",
        metavar="SOURCE_ID",
        default="",
        help="Run an end-to-end data ingestion health-check for a single source",
    )
    parser.add_argument(
        "--validate-all-sources",
        action="store_true",
        help="Run a registry, T0 gate, and fixture/live readiness audit across all 28 sources",
    )
    parser.add_argument(
        "--write-sample",
        type=Path,
        default=None,
        help="Write a realistic sample newspaper HTML file (JSON-LD + EN/UK quotes) to this path",
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

    if args.validate_all_sources:
        report = validate_all_sources()
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    if args.validate_source:
        res = validate_single_source(args.validate_source)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0

    if args.write_sample is not None:
        args.write_sample.parent.mkdir(parents=True, exist_ok=True)
        args.write_sample.write_text(DEMO_NEWSPAPER_HTML, encoding="utf-8")
        if args.html_file is None:
            print(
                json.dumps(
                    {
                        "sample_written": str(args.write_sample),
                        "next_command": (
                            f".\\.venv\\Scripts\\python.exe scripts/test_scraper.py "
                            f"--source-id {args.source_id} --html-file {args.write_sample}"
                        ),
                    },
                    indent=2,
                )
            )
            return 0

    row = _load_source_row(args.source_id)

    require_topic = not args.all_topics

    if args.live_feed:
        feed_report = run_live_feed(
            row,
            feed_url_override=args.feed_url,
            limit=args.limit,
            require_topic=require_topic,
        )
        print(json.dumps(feed_report, indent=2, ensure_ascii=False))
        return 0

    window_start = date(2026, 11, 13)
    window_end = date(2026, 11, 19)

    if args.rss_file:
        raw_xml = _read_required_file(args.rss_file, "--rss-file")
        items = parse_rss_feed(raw_xml)
        print(json.dumps({"mode": "rss_file", "discovered": items}, indent=2, ensure_ascii=False))
        return 0

    if args.sitemap_file:
        raw_xml = _read_required_file(args.sitemap_file, "--sitemap-file")
        items = parse_sitemap_xml(raw_xml)
        print(
            json.dumps({"mode": "sitemap_file", "discovered": items}, indent=2, ensure_ascii=False)
        )
        return 0

    if args.live:
        if not args.url:
            raise SystemExit("--live requires --url <https://...>")
        try:
            raw_bytes, sha256 = asyncio.run(
                fetch_bytes(
                    args.url,
                    row=row,
                    policy=FetchPolicy(),
                    track_a_approved=row.usable,
                )
            )
        except SourceNotPermitted as exc:
            raise SystemExit(f"Live fetch blocked by T0 gate: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            print(
                format_live_fetch_error(
                    args.url,
                    exc.response.status_code,
                    exc.response.reason_phrase,
                    args.source_id,
                ),
                file=sys.stderr,
            )
            raise SystemExit(2) from exc
        cache = BlobCache()
        _, blob_path = cache.store(row.source_id, raw_bytes)
        report = inspect_html_pipeline(
            raw_bytes.decode("utf-8", errors="replace"),
            source_row=row,
            source_url=args.url,
            window_start=date(2020, 1, 1),
            window_end=date(2030, 12, 31),
            require_topic=require_topic,
        )
        report["live_fetch"] = {"sha256": sha256, "cached_blob": str(blob_path)}
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    if args.html_file:
        raw_html = _read_required_file(args.html_file, "--html-file")
        report = inspect_html_pipeline(
            raw_html,
            source_row=row,
            source_url=args.url or "https://example.invalid/local-html",
            window_start=date(2020, 1, 1),
            window_end=date(2030, 12, 31),
            require_topic=require_topic,
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
