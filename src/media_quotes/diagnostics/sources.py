"""Live feed execution, fixture and source validation helpers for diagnostics."""

from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from media_quotes.adapters import BlobCache, parse_rss_feed, parse_sitemap_xml
from media_quotes.collection import collect_feed
from media_quotes.config import get_settings
from media_quotes.exceptions import SourceBlockedError, SourceNotPermitted
from media_quotes.fetch import FetchPolicy, fetch_policy_from
from media_quotes.fetch import fetch_bytes as fetch_bytes
from media_quotes.registry import SourceRegistryRow
from media_quotes.source_configs import load_registry_rows
from media_quotes.topics import TopicPack

from .html import inspect_html_pipeline
from .routes import (
    TopicContext,
    _approved_routes,
    _as_str,
    _resolve_feed_url,
    _resolve_topic_pack,
    _route_attr,
    _source_routes,
    detect_article_language,
    discover_sections,
    evaluate_source_topic,
    has_translation_rights,
    is_source_route_approved,
    load_topic_configuration,
)


def _policy() -> FetchPolicy:
    """Build the fetch policy from settings so it can be tuned via environment."""
    return fetch_policy_from(get_settings())


def _compat(name: str) -> Any:
    """Late-bound façade lookup: scripts/tests monkeypatch these names on the package."""
    return getattr(sys.modules[__package__], name)


def fixture_html_files(source_id: str) -> list[Path]:
    settings = get_settings()
    fixture_dir = settings.fixtures_dir / source_id
    return sorted(fixture_dir.glob("*.html")) if fixture_dir.exists() else []


def expected_sections_for_fixture(path: Path, sections: list[str] | None = None) -> list[str]:
    """Read a fixture's declared sections from its filename and HTML labels."""
    valid = set(sections or discover_sections())
    if not path.exists():
        return []
    found: set[str] = set()
    for token in re.split(r"[^a-z0-9_]+", path.stem.lower()):
        if token in valid:
            found.add(token)
    html = path.read_text(encoding="utf-8", errors="replace")
    for pattern in (
        r"data-(?:section|topic-section)\s*=\s*[\"']([^\"']+)[\"']",
        r"<meta\s+name=[\"']section[\"']\s+content=[\"']([^\"']+)[\"']",
        r"<!--\s*section:\s*([a-z_]+)\s*-->",
    ):
        for match in re.findall(pattern, html, flags=re.IGNORECASE):
            value = str(match).strip().lower()
            if value in valid:
                found.add(value)
    return sorted(found)


def _load_source_row(source_id: str) -> SourceRegistryRow:
    settings = get_settings()
    rows = {
        row.source_id: row
        for row in load_registry_rows(settings.sources_dir, fallback_csv=settings.sources_csv)
    }
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


def run_live_feed(
    row: SourceRegistryRow,
    feed_url_override: str = "",
    limit: int = 2,
    require_topic: bool = True,
    topic_id: str | None = None,
    cache: BlobCache | None = None,
    policy: FetchPolicy | None = None,
    extra_hosts: frozenset[str] = frozenset(),
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    """Fetch live feed for source, discover articles, and scrape top N articles."""
    pack = _resolve_topic_pack(topic_id)
    active_topic_id = pack.topic_id if pack is not None else (topic_id or "ukraine-war")
    if not row.usable:
        return {
            "source_id": row.source_id,
            "topic_id": active_topic_id,
            "t0_gate_permitted": False,
            "rights_status": str(row.rights_status),
            "status": "blocked_by_t0_gate",
            "message": (
                "Source is blocked under T0 rights policy. Automated live fetching is strictly "
                "prohibited without verified written licence. Replay fixture mode is used instead."
            ),
        }

    if row.tier_block:
        return {
            "source_id": row.source_id,
            "topic_id": active_topic_id,
            "t0_gate_permitted": True,
            "status": "blocked_by_tier",
            "message": row.blocker_reason or "source is tier_blocked",
        }

    feed_url = _resolve_feed_url(row, topic_id=active_topic_id, feed_url_override=feed_url_override)
    if not feed_url:
        return {
            "source_id": row.source_id,
            "topic_id": active_topic_id,
            "status": "error_no_feed_url",
            "message": f"No feed URL registered or known for source '{row.source_id}'. "
            "Pass --feed-url <https://...> to specify one.",
        }

    policy = policy or _policy()
    context = load_topic_configuration(topic_id=active_topic_id)
    try:
        result = collect_feed(
            row,
            feed_url,
            policy=policy,
            topic_pack=pack,
            require_topic=require_topic,
            limit=limit,
            start=start,
            end=end,
            cache=cache,
            extra_hosts=extra_hosts,
        )
    except SourceBlockedError as exc:
        return {
            "source_id": row.source_id,
            "topic_id": active_topic_id,
            "t0_gate_permitted": True,
            "status": "blocked_by_tier",
            "message": str(exc),
        }
    discovered = result.discovered
    downloaded = result.articles
    verify_end = end or get_settings().report_date
    verify_start = start or verify_end - timedelta(days=get_settings().report_window_days - 1)

    articles_results: list[dict[str, Any]] = []
    for article in downloaded:
        if article.error:
            articles_results.append(
                {
                    "url": article.url,
                    "title": article.title,
                    "status": "fetch_or_parse_error",
                    "error": article.error,
                }
            )
            continue
        rep = inspect_html_pipeline(
            article.raw.decode("utf-8", errors="replace"),
            source_row=row,
            source_url=article.url,
            window_start=verify_start,
            window_end=verify_end,
            require_topic=require_topic,
            context=context,
            topic_pack=pack,
        )
        rep["url"] = article.url
        rep["cached_blob"] = article.blob_path
        articles_results.append(rep)
    discovered_count = len(discovered)

    return {
        "source_id": row.source_id,
        "topic_id": active_topic_id,
        "source_name": row.source_name,
        "feed_url": feed_url,
        "t0_gate_permitted": True,
        "articles_discovered_in_feed": discovered_count,
        "articles_scraped_and_verified": len(articles_results),
        "articles": articles_results,
    }


def _unapproved_probe_refused(row: SourceRegistryRow) -> bool:
    """Refuse an unapproved fetch; this uses example.invalid and never opens a socket."""
    try:
        asyncio.run(
            _compat("fetch_bytes")(
                "https://example.invalid/probe",
                row=row,
                policy=_policy(),
                track_a_approved=False,
            )
        )
    except SourceNotPermitted:
        return True
    return False


_FEED_ROUTE_TYPES = frozenset({"rss", "sitemap", "topic_rss", "topic_sitemap"})
_API_ROUTE_TYPES = frozenset({"api", "gdelt"})


def _probe_approved_route(
    url: str,
    row: SourceRegistryRow,
    *,
    topic_pack: TopicPack | None = None,
    route_type: str = "",
) -> dict[str, Any]:
    """Opt-in live health check; callers must first prove the route and T0 row are approved."""
    if not url:
        return {"status": "no_route_url", "message": "Approved route has no discovery URL."}
    try:
        raw, _ = asyncio.run(
            _compat("fetch_bytes")(
                url, row=row, policy=_policy(), track_a_approved=True, check_dns=True
            )
        )
    except SourceNotPermitted:
        return {"status": "blocked_not_probed", "url": url}
    except Exception as exc:
        return {"status": "probe_error", "url": url, "error": str(exc)}

    stripped = raw.lstrip()
    looks_xml = stripped.startswith((b"<?xml", b"<rss", b"<feed", b"<urlset", b"<sitemapindex"))
    looks_json = stripped.startswith((b"{", b"["))
    looks_html = stripped.lower().startswith((b"<html", b"<!doctype html"))
    if looks_html:
        return {
            "status": "wrong_content_type",
            "url": url,
            "bytes": len(raw),
            "message": "Response is HTML, not RSS/sitemap XML or JSON.",
        }
    if looks_xml:
        body = raw.decode("utf-8", errors="replace")
        try:
            if "<urlset" in body or "<sitemapindex" in body:
                discovered = parse_sitemap_xml(body, topic_pack=topic_pack, require_topic=True)
            else:
                discovered = parse_rss_feed(body, topic_pack=topic_pack, require_topic=True)
        except Exception as exc:
            return {"status": "invalid_xml", "url": url, "bytes": len(raw), "error": str(exc)}
        return {
            "status": "reachable" if discovered else "empty_feed",
            "url": url,
            "bytes": len(raw),
            "articles_discovered": len(discovered),
        }
    if looks_json or route_type.lower() in _API_ROUTE_TYPES:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return {"status": "invalid_json", "url": url, "bytes": len(raw), "error": str(exc)}
        return {
            "status": "reachable_json",
            "url": url,
            "bytes": len(raw),
            "json_valid": True,
            "json_type": type(payload).__name__,
        }
    return {
        "status": "wrong_content_type",
        "url": url,
        "bytes": len(raw),
        "message": "Response is neither RSS/sitemap XML nor JSON.",
    }


def _validate_single_source_with_context(
    source_id: str,
    context: TopicContext,
    *,
    live_routes: bool,
) -> dict[str, Any]:
    """Implementation shared by the single-source and all-source diagnostics."""
    row = _compat("_load_source_row")(source_id)
    pack = context.topic_pack
    active_topic_id = pack.topic_id if pack is not None else "ukraine-war"
    sections = list(pack.section_ids()) if pack is not None else discover_sections()
    fixture_files = fixture_html_files(source_id)
    known_feed = _resolve_feed_url(row, topic_id=active_topic_id)
    approved = is_source_route_approved(source_id, context, row)
    approved_routes = _approved_routes(context, source_id)
    approved_route = next(
        (
            route
            for route in approved_routes
            if known_feed and _as_str(_route_attr(route, "discovery_url")) == known_feed
        ),
        None,
    )
    if approved_route is None:
        approved_route = next(
            (
                route
                for route in approved_routes
                if _as_str(_route_attr(route, "route_type")).lower()
                in _FEED_ROUTE_TYPES | _API_ROUTE_TYPES
            ),
            None,
        )
    if approved_route is None and approved_routes:
        approved_route = approved_routes[0]

    gate_enforcement = _compat("_unapproved_probe_refused")(row)
    fixture_results: list[dict[str, Any]] = []
    covered_sections: set[str] = set()
    quotes_verified = 0
    replay_status = "no_fixtures"
    for fixture_path in fixture_files:
        try:
            report = inspect_html_pipeline(
                fixture_path.read_text(encoding="utf-8"),
                source_row=row,
                source_url=f"https://{source_id}.invalid/fixture/{fixture_path.stem}",
                window_start=date(2020, 1, 1),
                window_end=date(2030, 12, 31),
                context=context,
            )
        except Exception as exc:
            replay_status = f"error ({exc})"
            fixture_results.append(
                {"file": fixture_path.name, "status": "error", "error": str(exc)}
            )
            continue
        declared = expected_sections_for_fixture(fixture_path, sections)
        covered_sections.update(declared)
        covered_sections.update(report["categories"]["covered"])
        quotes_verified += int(report["quotes_verified_publishable"])
        fixture_results.append(
            {
                "file": fixture_path.name,
                "declared_sections": declared,
                "topic_status": report["topic_status"],
                "topic_gate_status": report["topic_gate"]["status"],
                "sections": report["categories"]["covered"],
                "quotes_verified_publishable": report["quotes_verified_publishable"],
            }
        )
    if fixture_files and not replay_status.startswith("error"):
        replay_status = f"ok ({quotes_verified} quotes verified)"

    if fixture_results:
        representative = fixture_results[0]
        topic_gate = next(
            (
                item.get("topic_gate")
                for item in fixture_results
                if item.get("topic_gate_status") == "accepted"
            ),
            None,
        )
        topic_gate = topic_gate or evaluate_source_topic(
            source_id,
            {
                "source_id": source_id,
                "title": fixture_files[0].stem,
                "text": fixture_files[0].read_text(encoding="utf-8", errors="replace"),
            },
            context,
        )
        language_decision = detect_article_language(
            fixture_files[0].read_text(encoding="utf-8", errors="replace")
        )
        language_source = "first_fixture"
    else:
        topic_gate = evaluate_source_topic(
            source_id,
            {
                "source_id": source_id,
                "title": "Ukraine air defence and ceasefire monitoring",
                "text": (
                    "Synthetic diagnostic article about Ukraine, air defence, sanctions and "
                    "ceasefire negotiations. No real reporting is used."
                ),
                "url": f"https://{source_id}.invalid/diagnostic-topic",
            },
            context,
        )
        language_decision = detect_article_language(row.language)
        language_source = "source_registry_hint"
        representative = None

    categories: dict[str, Any] = {
        "sections": sections,
        "covered": sorted(covered_sections),
        "missing": [section for section in sections if section not in covered_sections],
        "quote_counts_by_section": {
            section: sum(
                item["quotes_verified_publishable"]
                for item in fixture_results
                if section in item.get("sections", [])
            )
            for section in sections
        },
    }
    categories["coverage_complete"] = bool(fixture_files) and not categories["missing"]
    if categories["coverage_complete"]:
        categories["status"] = "full"
    elif fixture_files:
        categories["status"] = "partial"
    else:
        categories["status"] = "missing_fixture"

    rights_confirmed = has_translation_rights(source_id, context)
    translation_allowed = bool(
        rights_confirmed
        and row.requires_translation
        and language_decision["language"] not in {"", "en", "unknown"}
        and language_decision["confidence"] >= 0.5
    )
    translation_readiness = {
        "rights_confirmed": rights_confirmed,
        "detected_language": language_decision["language"],
        "confidence": language_decision["confidence"],
        "evidence": language_decision["evidence"][:8],
        "language_source": language_source,
        "requires_translation": row.requires_translation,
        "translation_allowed": translation_allowed,
        "status": "ready" if translation_allowed else "rights_or_language_not_confirmed",
    }

    if row.tier_block:
        live_report: dict[str, Any] = {
            "status": "blocked_not_probed",
            "reason": row.blocker_reason or "source is tier_blocked",
        }
    elif not live_routes:
        live_report = {
            "status": "not_probed",
            "reason": "live route checks require --live-routes",
        }
    elif not approved:
        live_report = {
            "status": "blocked_not_probed",
            "reason": "no approved route or usable T0 row",
        }
    else:
        route_url = _as_str(_route_attr(approved_route, "discovery_url")) if approved_route else ""
        route_type = _as_str(_route_attr(approved_route, "route_type")).lower()
        if known_feed:
            probe_url = known_feed
        elif route_type in _FEED_ROUTE_TYPES | _API_ROUTE_TYPES:
            probe_url = route_url
        else:
            probe_url = ""
            live_report = {
                "status": "unsupported_route_type",
                "url": route_url,
                "route_type": route_type,
                "message": "Approved route is not an RSS, sitemap or JSON API discovery route.",
            }
        if probe_url:
            live_report = _probe_approved_route(
                probe_url,
                row,
                topic_pack=pack,
                route_type=route_type,
            )
        live_report["route_id"] = _as_str(_route_attr(approved_route, "route_id"))

    warnings: list[str] = []
    failures: list[dict[str, str]] = []
    if not gate_enforcement:
        failures.append(
            {
                "source_id": source_id,
                "code": "gate_enforcement_failed",
                "message": "Unapproved probe was not refused",
            }
        )
    if not fixture_files:
        warnings.append("missing_fixture")
        if context.routes and approved:
            failures.append(
                {
                    "source_id": source_id,
                    "code": "missing_fixture",
                    "message": "Approved source has no replay fixture",
                }
            )
    elif categories["missing"]:
        warnings.append("incomplete_category_coverage")
        if context.routes and approved:
            missing = ", ".join(categories["missing"])
            failures.append(
                {
                    "source_id": source_id,
                    "code": "incomplete_category_coverage",
                    "message": f"Missing sections: {missing}",
                }
            )
    for item in fixture_results:
        gate_status = item.get("topic_gate_status")
        if gate_status == "uncertain":
            warnings.append(f"topic_gate_uncertain:{item['file']}")
        if item.get("status") == "error" or gate_status == "rejected":
            detail = item.get("error") or gate_status
            failures.append(
                {
                    "source_id": source_id,
                    "code": "fixture_replay_failed",
                    "message": f"Fixture {item['file']}: {detail}",
                }
            )
    if replay_status.startswith("error"):
        failures.append({"source_id": source_id, "code": "replay_error", "message": replay_status})
    live_status = live_report["status"]
    if live_status == "empty_feed":
        warnings.append("live_route_empty")
    elif live_status in {
        "probe_error",
        "invalid_xml",
        "invalid_json",
        "wrong_content_type",
        "unsupported_route_type",
        "no_route_url",
    }:
        code = {
            "invalid_xml": "live_route_invalid_xml",
            "invalid_json": "live_route_invalid_json",
            "wrong_content_type": "live_route_wrong_content_type",
            "unsupported_route_type": "live_route_unsupported_type",
            "no_route_url": "live_route_missing_url",
        }.get(live_status, "live_route_probe_failed")
        failures.append(
            {
                "source_id": source_id,
                "code": code,
                "message": str(
                    live_report.get("error")
                    or live_report.get("message")
                    or "live route probe failed"
                ),
            }
        )

    return {
        "source_id": row.source_id,
        "topic_id": active_topic_id,
        "source_name": row.source_name,
        "tier": row.tier,
        "tier_block": row.tier_block,
        "blocker_reason": row.blocker_reason,
        "availability_status": row.availability_status,
        "source_type": str(row.source_type),
        "claim_only": bool(row.claim_only),
        "rights_status": str(row.rights_status),
        "access_mode": str(row.access_mode),
        "t0_permitted": row.usable,
        "t0_security_boundary_verified": gate_enforcement,
        "topic_gate": topic_gate,
        "language_detection": {
            "language": language_decision["language"],
            "confidence": language_decision["confidence"],
            "evidence": language_decision["evidence"][:8],
            "source": language_source,
        },
        "categories": categories,
        "translation_readiness": translation_readiness,
        "gate_enforcement": {
            "unapproved_probe_refused": gate_enforcement,
            "probe_url": "https://example.invalid/probe",
            "network_calls": 0,
        },
        "route": {
            "approved": approved,
            "route_ids": [
                _as_str(_route_attr(route, "route_id"))
                for route in _source_routes(context, source_id)
            ],
            "topic_rule_id": _as_str(
                _route_attr(
                    approved_routes[0]
                    if approved_routes
                    else (_source_routes(context, source_id) or [None])[0],
                    "topic_rule_id",
                )
            ),
            "fixture_replay": any(
                "fixture" in _as_str(_route_attr(route, "route_type"))
                or "fixture" in _as_str(_route_attr(route, "route_id"))
                for route in _source_routes(context, source_id)
            ),
        },
        "fixtures": {
            "count": len(fixture_files),
            "files": [path.name for path in fixture_files],
            "replay_status": replay_status,
            "results": fixture_results,
            "representative_status": (
                representative.get("topic_status") if representative else "no_fixtures"
            ),
            "warnings": warnings,
        },
        "feed": {
            "url": known_feed or "(none registered)",
            "live_supported": bool(known_feed and approved and not row.tier_block),
        },
        "live_routes": live_report,
        "warnings": warnings,
        "failures": failures,
    }


def validate_single_source(
    source_id: str,
    *,
    source_topics_csv: Path | None = None,
    topic_rules_path: Path | None = None,
    context: TopicContext | None = None,
    live_routes: bool = False,
    topic_id: str | None = None,
) -> dict[str, Any]:
    """Audit single source ingestion: registry, topic gate, T0 boundary, fixtures and feed."""
    context = context or load_topic_configuration(
        source_topics_csv, topic_rules_path, topic_id=topic_id
    )
    return _validate_single_source_with_context(source_id, context, live_routes=live_routes)


def validate_all_sources(
    *,
    source_topics_csv: Path | None = None,
    topic_rules_path: Path | None = None,
    context: TopicContext | None = None,
    live_routes: bool = False,
    topic_id: str | None = None,
) -> dict[str, Any]:
    """Validate all registered sources and produce a source x section readiness summary."""
    settings = get_settings()
    rows = load_registry_rows(settings.sources_dir, fallback_csv=settings.sources_csv)
    context = context or load_topic_configuration(
        source_topics_csv, topic_rules_path, topic_id=topic_id
    )
    results = [
        _validate_single_source_with_context(row.source_id, context, live_routes=live_routes)
        for row in rows
    ]

    sections = (
        list(context.topic_pack.section_ids())
        if context.topic_pack is not None
        else discover_sections()
    )
    by_section = {
        section: [item["source_id"] for item in results if section in item["categories"]["covered"]]
        for section in sections
    }
    failures = [failure for item in results for failure in item["failures"]]
    t0_blocked = sum(1 for item in results if not item["t0_permitted"])
    t0_permitted = sum(1 for item in results if item["t0_permitted"])
    approved_routes = sum(1 for item in results if item["route"]["approved"])

    return {
        "total_sources": len(results),
        "t0_permitted_sources": t0_permitted,
        "t0_blocked_sources": t0_blocked,
        "operationally_blocked_sources": sum(1 for item in results if item["tier_block"]),
        "operationally_enabled_sources": sum(1 for item in results if not item["tier_block"]),
        "approved_routes": approved_routes,
        "blocked_routes": len(results) - approved_routes,
        "sources_with_fixtures": sum(1 for item in results if item["fixtures"]["count"] > 0),
        "sources_with_live_feeds": sum(1 for item in results if item["feed"]["live_supported"]),
        "category_coverage": {
            "sections": sections,
            "total_cells": len(results) * len(sections),
            "covered_cells": sum(len(item["categories"]["covered"]) for item in results),
            "by_section": by_section,
            "sources_with_full_coverage": [
                item["source_id"] for item in results if item["categories"]["coverage_complete"]
            ],
            "missing_by_source": {
                item["source_id"]: item["categories"]["missing"]
                for item in results
                if item["categories"]["missing"]
            },
        },
        "live_routes_enabled": live_routes,
        "failures": failures,
        "sources": results,
    }
