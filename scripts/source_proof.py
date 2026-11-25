"""Build a per-source proof report using live public routes, samples or fixtures.

Usage:
    python -m scripts.source_proof --live --limit 1
    python -m scripts.source_proof --offline
    python -m scripts.source_proof --source reuters --url reuters=<article-url>
    python -m scripts.source_proof --source reuters --sample reuters=<article.html>
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from media_quotes.adapters import BlobCache
from media_quotes.collection import collect_bing, collect_urls
from media_quotes.config import get_settings
from media_quotes.diagnostics import (
    TopicContext,
    _resolve_feed_url,
    inspect_html_pipeline,
    load_topic_configuration,
    run_live_feed,
    validate_single_source,
)
from media_quotes.export import atomic_write_text
from media_quotes.fetch import FetchPolicy, fetch_policy_from
from media_quotes.registry import SourceRegistryRow
from media_quotes.source_configs import load_registry_rows, load_source_config

_DISCOVERY_QUERIES = {
    "ukrainskapravda": ("Ukraine", 'Україна "заявив"', 'Україна "повідомив"'),
    "ria": ("Украина", 'Украина "заявил"', 'Украина "сообщил"'),
}


def _proof_policy() -> FetchPolicy:
    return fetch_policy_from(get_settings()).model_copy(update={"follow_redirects": True})


def _approved_redirect_hosts(row: SourceRegistryRow) -> frozenset[str]:
    try:
        config = load_source_config(row.source_id, get_settings().sources_dir)
    except (FileNotFoundError, ValueError):
        return frozenset()
    return frozenset(config.redirect_hosts)


def _parse_assignments(values: list[str] | None, flag: str) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for raw in values or []:
        source_id, separator, target = raw.partition("=")
        if not separator or not source_id.strip() or not target.strip():
            raise ValueError(f"{flag} expects SOURCE_ID=VALUE")
        assignments[source_id.strip().lower()] = target.strip()
    return assignments


def _blocker_from_text(text: str) -> str:
    lowered = text.lower()
    if "401" in lowered or "unauthorized" in lowered:
        return "unauthorized"
    if "403" in lowered or "forbidden" in lowered:
        return "forbidden"
    if "404" in lowered or "not found" in lowered:
        return "not_found"
    if "301" in lowered or "302" in lowered or "redirect" in lowered:
        return "redirect"
    if "wrong_content_type" in lowered or "neither rss" in lowered or "response is html" in lowered:
        return "wrong_content_type"
    if (
        "connecterror" in lowered
        or "connecttimeout" in lowered
        or "connection" in lowered
        or "network" in lowered
        or "timeout" in lowered
    ):
        return "network"
    if "article url must be absolute https" in lowered:
        return "insecure_feed_url"
    if "not registered for source" in lowered:
        return "unapproved_redirect_or_host"
    if "no feed url" in lowered or "no_public_feed" in lowered:
        return "no_public_feed"
    if "unsupported_route_type" in lowered:
        return "unsupported_route_type"
    return "live_route_failed"


def _sections_from_articles(articles: list[dict[str, Any]]) -> list[str]:
    sections: set[str] = set()
    for article in articles:
        categories = article.get("categories")
        if isinstance(categories, dict):
            sections.update(str(value) for value in categories.get("covered", []))
        sections.update(
            str(quote.get("section_id"))
            for quote in article.get("quotes", [])
            if quote.get("section_id")
        )
    return sorted(sections)


def _metadata_complete_count(articles: list[dict[str, Any]]) -> int:
    required = ("title", "canonical_url", "publication_date", "byline", "publication_name")
    complete = 0
    for article in articles:
        metadata = article.get("metadata")
        if isinstance(metadata, dict) and all(metadata.get(field) for field in required):
            complete += 1
    return complete


def _article_urls(articles: list[dict[str, Any]]) -> list[str]:
    urls: set[str] = set()
    for article in articles:
        if article.get("url"):
            urls.add(str(article["url"]))
        metadata = article.get("metadata")
        if isinstance(metadata, dict) and metadata.get("canonical_url"):
            urls.add(str(metadata["canonical_url"]))
    return sorted(urls)


def _max_text_length(articles: list[dict[str, Any]]) -> int:
    lengths: list[int] = []
    for article in articles:
        metadata = article.get("metadata")
        if isinstance(metadata, dict):
            try:
                lengths.append(int(metadata.get("extracted_text_length", 0)))
            except (TypeError, ValueError):
                continue
    return max(lengths, default=0)


def _no_quote_status(row: SourceRegistryRow, articles: list[dict[str, Any]]) -> str:
    """Decide from the registry availability field, never from a list of source ids."""
    if row.availability_status == "partial_body":
        return "partial_body"
    return "accessible_no_quotes"


def _base_entry(row: SourceRegistryRow, mode: str) -> dict[str, Any]:
    return {
        "source_id": row.source_id,
        "source_name": row.source_name,
        "tier": row.tier,
        "tier_block": row.tier_block,
        "blocker_reason": row.blocker_reason,
        "availability_status": row.availability_status,
        "claim_only": row.claim_only,
        "rights_status": row.rights_status,
        "access_mode": row.access_mode,
        "mode": mode,
        "status": "blocked",
        "collected": False,
        "articles_discovered": 0,
        "articles_fetched": 0,
        "quotes_verified": 0,
        "sections": [],
        "metadata_complete": 0,
        "max_text_length": 0,
        "urls": [],
        "blocker": "",
        "detail": "",
    }


def _fixture_entry(
    row: SourceRegistryRow,
    context: TopicContext,
    *,
    mode: str = "fixture",
    blocker: str = "",
    detail: str = "",
) -> dict[str, Any]:
    entry = _base_entry(row, mode)
    try:
        report = validate_single_source(row.source_id, context=context, live_routes=False)
        fixtures = report.get("fixtures", {})
        results = fixtures.get("results", [])
        quotes = sum(int(item.get("quotes_verified_publishable", 0)) for item in results)
        sections = sorted(
            {str(section) for item in results for section in item.get("sections", [])}
        )
        entry.update(
            {
                "status": "fixture_only" if quotes else "blocked",
                "collected": False,
                "articles_fetched": int(fixtures.get("count", 0)),
                "quotes_verified": quotes,
                "sections": sections,
                "blocker": row.blocker_reason or blocker or "fixture_only_not_live_data",
                "detail": detail or str(fixtures.get("replay_status", "")),
            }
        )
    except Exception as exc:
        entry.update({"blocker": blocker or "fixture_error", "detail": str(exc)})
    return entry


def _inspect_bytes(
    row: SourceRegistryRow,
    raw: bytes,
    source_url: str,
    context: TopicContext,
    cache: BlobCache,
) -> dict[str, Any]:
    digest, blob_path = cache.store(row.source_id, raw)
    cache.store_meta(row.source_id, digest, {"url": source_url, "proof_mode": True})
    report = inspect_html_pipeline(
        raw.decode("utf-8", errors="replace"),
        source_row=row,
        source_url=source_url,
        window_start=date(2020, 1, 1),
        window_end=date(2030, 12, 31),
        require_topic=True,
        context=context,
    )
    report["url"] = source_url
    report["cached_blob"] = str(blob_path)
    return report


def _sample_entry(
    row: SourceRegistryRow,
    path: Path,
    context: TopicContext,
    cache: BlobCache,
) -> dict[str, Any]:
    entry = _base_entry(row, "manual_sample")
    try:
        report = _inspect_bytes(row, path.read_bytes(), path.as_uri(), context, cache)
        quotes = int(report.get("quotes_verified_publishable", 0))
        entry.update(
            {
                "status": "manual_sample_verified" if quotes else "manual_sample_no_quotes",
                "collected": quotes > 0,
                "articles_fetched": 1,
                "quotes_verified": quotes,
                "sections": _sections_from_articles([report]),
                "metadata_complete": _metadata_complete_count([report]),
                "max_text_length": _max_text_length([report]),
                "urls": _article_urls([report]),
                "blocker": "" if quotes else "sample_no_verified_quotes",
                "detail": str(path),
            }
        )
    except Exception as exc:
        entry.update({"blocker": "sample_error", "detail": str(exc)})
    return entry


def _url_entry(
    row: SourceRegistryRow,
    url: str,
    context: TopicContext,
    cache: BlobCache,
) -> dict[str, Any]:
    entry = _base_entry(row, "direct_url")
    try:
        result = collect_urls(
            row,
            [url],
            policy=_proof_policy(),
            extra_hosts=_approved_redirect_hosts(row),
            cache=cache,
        )
        if not result.articles:
            raise RuntimeError("direct URL returned no articles")
        article = result.articles[0]
        if article.error:
            raise RuntimeError(article.error)
        report = _inspect_bytes(row, article.raw, url, context, cache)
        quotes = int(report.get("quotes_verified_publishable", 0))
        entry.update(
            {
                "status": "direct_url_verified" if quotes else "direct_url_no_quotes",
                "collected": quotes > 0,
                "articles_fetched": 1,
                "quotes_verified": quotes,
                "sections": _sections_from_articles([report]),
                "metadata_complete": _metadata_complete_count([report]),
                "max_text_length": _max_text_length([report]),
                "urls": _article_urls([report]),
                "blocker": "" if quotes else "direct_url_no_verified_quotes",
                "detail": url,
            }
        )
    except Exception as exc:
        message = str(exc) or type(exc).__name__
        return _fixture_entry(
            row,
            context,
            mode="fixture_fallback",
            blocker=_blocker_from_text(message),
            detail=message,
        )
    return entry


def _discovery_entry(row: SourceRegistryRow) -> dict[str, Any]:
    entry = _base_entry(row, "discovery_only")
    entry.update(
        {
            "status": "discovery_only",
            "collected": False,
            "blocker": "",
            "detail": "Discovery metadata only; publisher full text is required for quotes.",
        }
    )
    return entry


def _bing_entry(
    row: SourceRegistryRow,
    context: TopicContext,
    cache: BlobCache,
    *,
    limit: int,
) -> dict[str, Any]:
    entry = _base_entry(row, "bing_discovery")
    domain = (urlparse(row.homepage_url).hostname or "").lower()
    if not domain:
        return _fixture_entry(
            row,
            context,
            mode="fixture_fallback",
            blocker="no_public_feed",
            detail="No publisher domain is configured.",
        )

    queries = _DISCOVERY_QUERIES.get(
        row.source_id,
        ("Ukraine", 'Russia Ukraine "said"'),
    )
    settings = get_settings()
    report_end = settings.report_date
    report_start = report_end - timedelta(days=settings.report_window_days - 1)
    try:
        result = collect_bing(
            row,
            domain,
            queries=queries,
            limit=limit,
            policy=_proof_policy(),
            extra_hosts=_approved_redirect_hosts(row),
            cache=cache,
            start=report_start,
            end=report_end,
        )
    except Exception as exc:
        entry.update(
            {
                "status": "blocked",
                "blocker": _blocker_from_text(str(exc) or type(exc).__name__),
                "detail": str(exc) or type(exc).__name__,
            }
        )
        return entry

    candidates = result.discovered
    discovery_errors = [item["error"] for item in result.errors]
    reports: list[dict[str, Any]] = []
    errors: list[str] = []
    for article in result.articles:
        if article.error:
            errors.append(article.error)
            continue
        reports.append(_inspect_bytes(row, article.raw, article.url, context, cache))
        if sum(int(item.get("quotes_verified_publishable", 0)) for item in reports) >= 3:
            break

    quotes = sum(int(item.get("quotes_verified_publishable", 0)) for item in reports)
    entry.update(
        {
            "articles_discovered": len(candidates),
            "articles_fetched": len(reports),
            "quotes_verified": quotes,
            "sections": _sections_from_articles(reports),
            "metadata_complete": _metadata_complete_count(reports),
            "max_text_length": _max_text_length(reports),
            "urls": _article_urls(reports),
        }
    )
    if quotes:
        if row.availability_status == "partial_body":
            entry.update({"status": "partial_body", "collected": True, "blocker": "partial_body"})
        else:
            entry.update({"status": "live_verified", "collected": True, "blocker": ""})
    elif reports:
        status = _no_quote_status(row, reports)
        entry.update({"status": status, "collected": False, "blocker": status})
    else:
        blocker = _blocker_from_text(errors[0]) if errors else "no_bing_results"
        if discovery_errors and not errors:
            blocker = _blocker_from_text(discovery_errors[0])
        entry.update({"status": "blocked", "collected": False, "blocker": blocker})
    if errors:
        entry["detail"] = errors[0]
    elif discovery_errors:
        entry["detail"] = discovery_errors[0]
    else:
        entry["detail"] = domain
    return entry


def _live_entry(
    row: SourceRegistryRow,
    context: TopicContext,
    cache: BlobCache,
    *,
    topic_id: str,
    limit: int,
) -> dict[str, Any]:
    if row.tier_block:
        entry = _base_entry(row, "blocked")
        entry.update(
            {
                "status": "blocked",
                "collected": False,
                "blocker": row.blocker_reason or "source is tier_blocked",
                "detail": row.blocker_reason,
            }
        )
        return entry

    if row.availability_status == "discovery_only":
        return _discovery_entry(row)

    entry = _base_entry(row, "live_feed")
    feed_url = _resolve_feed_url(row, topic_id=topic_id)
    entry["detail"] = feed_url
    articles: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    discovered = 0
    feed_error = ""
    if feed_url:
        try:
            report = run_live_feed(
                row,
                limit=limit,
                topic_id=topic_id,
                cache=cache,
                policy=_proof_policy(),
                extra_hosts=_approved_redirect_hosts(row),
                start=row.archive_start_date,
                end=get_settings().report_date,
            )
            if report.get("status"):
                feed_error = str(report.get("message") or report.get("status"))
            else:
                articles = [item for item in report.get("articles", []) if isinstance(item, dict)]
                errors = [item for item in articles if item.get("status") == "fetch_or_parse_error"]
                discovered = int(report.get("articles_discovered_in_feed", 0))
        except Exception as exc:
            feed_error = str(exc) or type(exc).__name__

    successful = [item for item in articles if item.get("status") != "fetch_or_parse_error"]
    quotes = sum(int(item.get("quotes_verified_publishable", 0)) for item in successful)
    if quotes:
        entry.update(
            {
                "status": (
                    "partial_body"
                    if row.availability_status == "partial_body"
                    else "live_partial"
                    if errors
                    else "live_verified"
                ),
                "collected": True,
                "articles_discovered": discovered,
                "articles_fetched": len(successful),
                "quotes_verified": quotes,
                "sections": _sections_from_articles(successful),
                "metadata_complete": _metadata_complete_count(successful),
                "max_text_length": _max_text_length(successful),
                "urls": _article_urls(successful),
                "blocker": (
                    "partial_body"
                    if row.availability_status == "partial_body"
                    else "partial_article_failures"
                    if errors
                    else ""
                ),
            }
        )
        if errors:
            entry["detail"] = str(errors[0].get("error") or feed_url)
        return entry

    bing = _bing_entry(row, context, cache, limit=limit)
    if bing["collected"]:
        return bing
    if bing["articles_fetched"] and bing["status"] in {"partial_body", "accessible_no_quotes"}:
        return bing

    if successful:
        status = _no_quote_status(row, successful)
        entry.update(
            {
                "status": status,
                "collected": False,
                "articles_discovered": discovered,
                "articles_fetched": len(successful),
                "sections": _sections_from_articles(successful),
                "metadata_complete": _metadata_complete_count(successful),
                "max_text_length": _max_text_length(successful),
                "urls": _article_urls(successful),
                "blocker": status,
            }
        )
        return entry

    blocker = row.blocker_reason or bing["blocker"] or _blocker_from_text(feed_error)
    detail = feed_error or bing["detail"]
    return _fixture_entry(
        row,
        context,
        mode="fixture_fallback",
        blocker=blocker,
        detail=detail,
    )


def build_report(
    entries: list[dict[str, Any]],
    *,
    generated_at: str,
    topic_id: str,
    live: bool,
) -> dict[str, Any]:
    statuses: dict[str, int] = {}
    for entry in entries:
        status = str(entry["status"])
        statuses[status] = statuses.get(status, 0) + 1
    collected = [entry for entry in entries if entry.get("collected") is True]
    fixture_only = [entry for entry in entries if str(entry.get("mode", "")).startswith("fixture")]
    return {
        "generated_at": generated_at,
        "topic_id": topic_id,
        "live_requested": live,
        "summary": {
            "total_sources": len(entries),
            "statuses": statuses,
            "sources_with_collected_data": len(collected),
            "sources_with_verified_quotes": len(collected),
            "sources_fixture_only": len(fixture_only),
            "sources_not_collected": len(entries) - len(collected),
            "sources_with_blockers": sum(
                bool(entry.get("blocker")) and entry.get("status") != "discovery_only"
                for entry in entries
            ),
            "live_quotes_verified": sum(
                int(entry["quotes_verified"])
                for entry in collected
                if entry.get("mode") != "manual_sample"
            ),
            "sample_quotes_verified": sum(
                int(entry["quotes_verified"])
                for entry in collected
                if entry.get("mode") == "manual_sample"
            ),
            "fixture_quotes_verified": sum(int(entry["quotes_verified"]) for entry in fixture_only),
        },
        "sources": entries,
    }


def render_markdown(report: dict[str, Any], report_date: str) -> str:
    summary = report["summary"]
    lines = [
        f"# Source collection proof — {report_date}",
        "",
        f"Topic: `{report['topic_id']}`. "
        f"Live requested: `{str(report['live_requested']).lower()}`.",
        "",
        f"Sources: **{summary['total_sources']}**; with collected data: "
        f"**{summary['sources_with_collected_data']}**; fixture-only: "
        f"**{summary['sources_fixture_only']}**; not collected: "
        f"**{summary['sources_not_collected']}**; with blockers: "
        f"**{summary['sources_with_blockers']}**.",
        "",
        "| Source | Tier | Availability | Mode | Status | Articles | Quotes | Sections | Blocker |",
        "|---|---:|---|---|---|---:|---:|---|---|",
    ]
    for entry in report["sources"]:
        lines.append(
            "| {source_name} (`{source_id}`) | {tier} | {availability_status} | "
            "{mode} | {status} | {articles_fetched} | {quotes_verified} | "
            "{sections} | {blocker} |".format(
                **{
                    **entry,
                    "sections": ", ".join(entry["sections"]) or "—",
                    "blocker": entry["blocker"] or "—",
                }
            )
        )
    lines.extend(["", "## Blockers", ""])
    blocked = [entry for entry in report["sources"] if entry["blocker"]]
    if not blocked:
        lines.append("None.")
    else:
        for entry in blocked:
            lines.append(
                f"- **{entry['source_name']}** (`{entry['source_id']}`): "
                f"`{entry['blocker']}` — {entry['detail'] or 'no additional detail'}"
            )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="attempt public live routes")
    parser.add_argument("--offline", action="store_true", help="use fixtures only")
    parser.add_argument("--source", action="append", default=[], help="limit to SOURCE_ID")
    parser.add_argument("--sample", action="append", default=[], metavar="SOURCE_ID=PATH")
    parser.add_argument("--url", action="append", default=[], metavar="SOURCE_ID=URL")
    parser.add_argument("--topic", default=settings.active_topic)
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.live and args.offline:
        parser.error("--live and --offline are mutually exclusive")
    if args.limit < 1:
        parser.error("--limit must be positive")
    fixture_only = not args.live and not args.sample and not args.url
    out_settings = settings.for_source_mode("fixtures") if fixture_only else settings
    out_dir = args.out or out_settings.output_dir / "proof"

    try:
        samples = _parse_assignments(args.sample, "--sample")
        urls = _parse_assignments(args.url, "--url")
    except ValueError as exc:
        parser.error(str(exc))

    rows = load_registry_rows(settings.sources_dir, fallback_csv=settings.sources_csv)
    by_id = {row.source_id: row for row in rows}
    requested = {value.strip().lower() for value in args.source}
    requested.update(samples)
    requested.update(urls)
    unknown = sorted(requested - set(by_id))
    if unknown:
        parser.error(f"unknown source id(s): {', '.join(unknown)}")

    context = load_topic_configuration(topic_id=args.topic)
    cache = BlobCache(out_dir / "raw")
    entries: list[dict[str, Any]] = []
    for row in rows:
        if requested and row.source_id not in requested:
            continue
        if row.source_id in samples:
            entry = _sample_entry(row, Path(samples[row.source_id]), context, cache)
        elif row.source_id in urls:
            entry = _url_entry(row, urls[row.source_id], context, cache)
        elif args.live:
            entry = _live_entry(
                row,
                context,
                cache,
                topic_id=args.topic,
                limit=args.limit,
            )
        else:
            entry = _fixture_entry(row, context)
        entries.append(entry)

    generated_at = datetime.now(UTC).isoformat()
    report = build_report(
        entries,
        generated_at=generated_at,
        topic_id=args.topic,
        live=args.live,
    )
    report_date = settings.report_date.isoformat()
    json_path = out_dir / f"source-proof-{report_date}.json"
    md_path = out_dir / f"source-proof-{report_date}.md"
    atomic_write_text(json_path, json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    atomic_write_text(md_path, render_markdown(report, report_date))
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
    print(f"JSON: {json_path}")
    print(f"Markdown: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
