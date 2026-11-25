r"""Interactive CLI diagnostic tool for testing newspaper scraping, RSS/Sitemap discovery,
Trafilatura + JSON-LD extraction, quote/speaker attribution, and 2-stage deterministic verification.

Usage examples (PowerShell):
  1. Built-in end-to-end demo (RSS + Sitemap + realistic newspaper HTML + 2-stage verifier):
     .\.venv\Scripts\python.exe scripts/test_scraper.py

  2. Get news of the day live from The Guardian (fetches live RSS + top articles + quotes):
     .\.venv\Scripts\python.exe scripts/test_scraper.py `
       --source-id guardian --live-feed --limit 2
     .\.venv\Scripts\python.exe scripts/test_scraper.py `
       --source-id guardian --topic ai-policy --live-feed --limit 2

  3. Validate data getting for a single source (T0 gate, fixtures, and feed connectivity):
     .\.venv\Scripts\python.exe scripts/test_scraper.py --validate-source guardian
     .\.venv\Scripts\python.exe scripts/test_scraper.py --validate-source guardian --live-routes
     .\.venv\Scripts\python.exe scripts/test_scraper.py --validate-source reuters

  4. Audit data getting readiness across all 28 sources in the registry:
     .\.venv\Scripts\python.exe scripts/test_scraper.py --validate-all-sources
     .\.venv\Scripts\python.exe scripts/test_scraper.py --validate-all-sources --live-routes

  5. Test a single real article page (ONLY use a URL discovered by --live-feed):
     .venv/Scripts/python.exe scripts/test_scraper.py `
       --source-id guardian --live --url <real-article-url>

   6. Test scraping on a generated sample (var/test-output/samples/article.html) or fixture HTML:
     .\.venv\Scripts\python.exe scripts/test_scraper.py `
        --source-id guardian --html-file var/test-output/samples/article.html
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
from datetime import date, timedelta
from pathlib import Path

import httpx

from media_quotes import diagnostics
from media_quotes.adapters import BlobCache, parse_rss_feed, parse_sitemap_xml
from media_quotes.config import get_settings
from media_quotes.diagnostics import (
    TopicContext,
    _list_available_fixture_html,
    _load_source_row,
    _resolve_feed_url,
    _route_attr,
    _unapproved_probe_refused,
    detect_article_language,
    discover_sections,
    evaluate_source_topic,
    expected_sections_for_fixture,
    fixture_html_files,
    has_translation_rights,
    inspect_html_pipeline,
    is_source_route_approved,
    load_topic_configuration,
    run_live_feed,
    validate_all_sources,
    validate_single_source,
)
from media_quotes.exceptions import SourceNotPermitted
from media_quotes.fetch import FetchPolicy, fetch_bytes, fetch_policy_from


def _policy() -> FetchPolicy:
    """Build the fetch policy from settings so it can be tuned via environment."""
    return fetch_policy_from(get_settings())


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
                f"  {script} --source-id {source_id} --html-file {SAMPLE_HTML_PATH}",
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
                f"Tip: Pass an existing HTML file (e.g. {SAMPLE_HTML_PATH}), or generate one:",
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


# Canonical generated sample; --write-sample recreates it without touching the repository root.
SAMPLE_HTML_PATH = "var/test-output/samples/article.html"

__all__ = [
    "DEMO_NEWSPAPER_HTML",
    "DEMO_RSS_XML",
    "DEMO_SITEMAP_XML",
    "SAMPLE_HTML_PATH",
    "TopicContext",
    "_list_available_fixture_html",
    "_load_source_row",
    "_resolve_feed_url",
    "_route_attr",
    "_unapproved_probe_refused",
    "detect_article_language",
    "diagnostics",
    "discover_sections",
    "evaluate_source_topic",
    "expected_sections_for_fixture",
    "fixture_html_files",
    "format_live_fetch_error",
    "has_translation_rights",
    "inspect_html_pipeline",
    "is_source_route_approved",
    "load_topic_configuration",
    "main",
    "run_live_feed",
    "validate_all_sources",
    "validate_single_source",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Test newspaper discovery, Trafilatura scraping & 2-stage verification."
    )
    parser.add_argument(
        "--source-id",
        default="guardian",
        help="Source ID from data/sources/<source_id>.json (default: guardian)",
    )
    parser.add_argument(
        "--topic",
        default=None,
        help="Topic pack ID from data/topics/<topic_id>.json (default: ACTIVE_TOPIC / ukraine-war)",
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
        "--source-topics-csv",
        type=Path,
        default=None,
        help="Override Settings.source_topics_csv for topic route validation",
    )
    parser.add_argument(
        "--topic-rules",
        type=Path,
        default=None,
        help="Override Settings.topic_rules_path for topic gate validation",
    )
    parser.add_argument(
        "--live-routes",
        action="store_true",
        help=(
            "Opt in to live route health checks during validation; only approved sources are "
            "probed and blocked sources are reported as blocked_not_probed"
        ),
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
    context = load_topic_configuration(
        args.source_topics_csv, args.topic_rules, topic_id=args.topic
    )

    if args.validate_all_sources:
        report = validate_all_sources(context=context, live_routes=args.live_routes)
        print(json.dumps(report, indent=2, ensure_ascii=True))
        return 1 if report["failures"] else 0

    if args.validate_source:
        res = validate_single_source(
            args.validate_source, context=context, live_routes=args.live_routes
        )
        print(json.dumps(res, indent=2, ensure_ascii=True))
        return 1 if res["failures"] else 0

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
            topic_id=args.topic,
        )
        print(json.dumps(feed_report, indent=2, ensure_ascii=True))
        if feed_report.get("t0_gate_permitted") is False or feed_report.get("status") in {
            "blocked_by_t0_gate",
            "error_no_feed_url",
        }:
            return 1
        if any(
            article.get("status") == "fetch_or_parse_error"
            for article in feed_report.get("articles", [])
        ):
            return 1
        return 0

    settings = get_settings()
    window_end = settings.report_date
    window_start = window_end - timedelta(days=settings.report_window_days - 1)

    if args.rss_file:
        raw_xml = _read_required_file(args.rss_file, "--rss-file")
        items = parse_rss_feed(raw_xml, topic_pack=context.topic_pack, require_topic=require_topic)
        print(json.dumps({"mode": "rss_file", "discovered": items}, indent=2, ensure_ascii=True))
        return 0

    if args.sitemap_file:
        raw_xml = _read_required_file(args.sitemap_file, "--sitemap-file")
        items = parse_sitemap_xml(
            raw_xml, topic_pack=context.topic_pack, require_topic=require_topic
        )
        print(
            json.dumps({"mode": "sitemap_file", "discovered": items}, indent=2, ensure_ascii=True)
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
                    policy=_policy(),
                    track_a_approved=row.usable,
                    check_dns=True,
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
            # Manual inspection of an arbitrary discovered URL: keep a wide verification
            # window so diagnostics do not depend on the configured report date.
            window_start=date(2020, 1, 1),
            window_end=date(2030, 12, 31),
            require_topic=require_topic,
            context=context,
        )
        report["live_fetch"] = {"sha256": sha256, "cached_blob": str(blob_path)}
        print(json.dumps(report, indent=2, ensure_ascii=True))
        return 0

    if args.html_file:
        raw_html = _read_required_file(args.html_file, "--html-file")
        report = inspect_html_pipeline(
            raw_html,
            source_row=row,
            source_url=args.url or "https://example.invalid/local-html",
            # Manual local-file inspection intentionally uses a wide window so synthetic
            # samples with fixed publication dates remain verifiable offline.
            window_start=date(2020, 1, 1),
            window_end=date(2030, 12, 31),
            require_topic=require_topic,
            context=context,
        )
        print(json.dumps(report, indent=2, ensure_ascii=True))
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
        context=context,
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
    print(json.dumps(full_demo, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
