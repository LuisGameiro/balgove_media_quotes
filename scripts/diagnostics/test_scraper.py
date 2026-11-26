r"""Interactive CLI diagnostic tool for testing newspaper scraping, RSS/Sitemap discovery,
Trafilatura + JSON-LD extraction, quote/speaker attribution, and 2-stage deterministic verification.

The compatibility shim at ``scripts/test_scraper.py`` supports both direct execution
(``python scripts/diagnostics/test_scraper.py --help``) and ``python -m scripts.test_scraper``.

Usage examples (PowerShell):
  1. Built-in end-to-end demo (RSS + Sitemap + realistic newspaper HTML + 2-stage verifier):
     .\.venv\Scripts\python.exe scripts/diagnostics/test_scraper.py

  2. Get news of the day live from The Guardian (fetches live RSS + top articles + quotes):
     .\.venv\Scripts\python.exe scripts/diagnostics/test_scraper.py `
       --source-id guardian --live-feed --limit 2
     .\.venv\Scripts\python.exe scripts/diagnostics/test_scraper.py `
       --source-id guardian --topic ai-policy --live-feed --limit 2

  3. Validate data getting for a single source (T0 gate, fixtures, and feed connectivity):
     .\.venv\Scripts\python.exe scripts/diagnostics/test_scraper.py --validate-source guardian
     .\.venv\Scripts\python.exe scripts/diagnostics/test_scraper.py `
       --validate-source guardian --live-routes
     .\.venv\Scripts\python.exe scripts/diagnostics/test_scraper.py --validate-source reuters

  4. Audit data getting readiness across all 28 sources in the registry:
     .\.venv\Scripts\python.exe scripts/diagnostics/test_scraper.py --validate-all-sources
     .\.venv\Scripts\python.exe scripts/diagnostics/test_scraper.py `
       --validate-all-sources --live-routes

  5. Test a single real article page (ONLY use a URL discovered by --live-feed):
     .venv/Scripts/python.exe scripts/diagnostics/test_scraper.py `
       --source-id guardian --live --url <real-article-url>

  6. Test scraping on local sample HTML (my_article.html) or fixture HTML:
     .\.venv\Scripts\python.exe scripts/diagnostics/test_scraper.py `
       --source-id guardian --html-file my_article.html
     .\.venv\Scripts\python.exe scripts/diagnostics/test_scraper.py `
       --source-id reuters --html-file data/fixtures/reuters/synthetic-reuters-2026-11-18.html

  7. Test RSS or Sitemap discovery on sample XML files:
     .\.venv\Scripts\python.exe scripts/diagnostics/test_scraper.py `
       --rss-file data/samples/sample_feed.xml
     .\.venv\Scripts\python.exe scripts/diagnostics/test_scraper.py `
       --sitemap-file data/samples/sample_sitemap.xml
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import importlib
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

import httpx

from media_quotes.adapters import (
    UKRAINE_WAR_KEYWORDS,
    BlobCache,
    build_adapter,
    is_topic_relevant,
    parse_newspaper_html,
    parse_rss_feed,
    parse_sitemap_xml,
)
from media_quotes.config import get_settings
from media_quotes.enums import SECTION_IDS
from media_quotes.exceptions import SourceNotPermitted
from media_quotes.ingestion.fetch import FetchPolicy, fetch_bytes, fetch_policy_from
from media_quotes.ingestion.live import fetch_feed_documents
from media_quotes.models import QuoteCandidate
from media_quotes.processing.verification_stages import verify_pipeline
from media_quotes.registry import SourceRegistryRow
from media_quotes.source_configs import (
    SourceConfig,
    load_registry_rows,
    load_routes_from_sources,
    load_source_config,
)
from media_quotes.topics import TopicPack, load_all_topic_rules, load_topic_pack


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


REPO_ROOT = Path(__file__).resolve().parents[2]

# Optional workstream APIs are discovered lazily so this diagnostic stays usable while the
# topic/language modules are being introduced. Offline equivalents keep the safety gates active.
_TOPIC_MODULE_NAMES = (
    "media_quotes.pipeline",
    "media_quotes.governance.relevance",
    "media_quotes.topics",
    "media_quotes.topic_gate",
    "media_quotes.registry",
    "media_quotes.source_topics",
    "media_quotes.topic_routes",
)
_LANGUAGE_MODULE_NAMES = (
    "media_quotes.ai.language",
    "media_quotes.linguistics",
    "media_quotes.governance.relevance",
)

_OFF_TOPIC_DIAGNOSTIC_HTML = """<!doctype html>
<html lang="en">
<head>
  <title>Municipal bus timetable changes approved</title>
  <link rel="canonical" href="https://example.invalid/civic/bus-timetable">
</head>
<body>
  <article>
    <h1>Municipal bus timetable changes approved</h1>
    <p>The city council approved a new bakery delivery schedule and a revised bus timetable.</p>
    <p>"The new timetable starts on Monday and reduces waiting time at the central stop," said
    Transport Officer Anna Nowak, a municipal spokesperson.</p>
    <p>Residents can collect a printed leaflet from the public library.</p>
  </article>
</body>
</html>
"""


def _import_symbol(
    module_names: tuple[str, ...], symbol_names: tuple[str, ...]
) -> Callable[..., Any] | None:
    for module_name in module_names:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        for symbol_name in symbol_names:
            candidate = getattr(module, symbol_name, None)
            if callable(candidate):
                return cast(Callable[..., Any], candidate)
    return None


def _as_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "approved"}


def _as_str(value: object) -> str:
    return "" if value is None else str(value).strip()


@dataclass(frozen=True)
class _FallbackRoute:
    source_id: str
    route_id: str
    approved: bool = False
    translation_rights_confirmed: bool = False
    topic_rule_id: str = ""
    license_reference: str = ""
    route_type: str = ""
    discovery_url: str = ""


def _fallback_load_routes(path: Path) -> dict[str, list[Any]]:
    if not path.exists():
        return {}
    routes: dict[str, list[Any]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for raw_row in csv.DictReader(handle):
            row = {str(key).strip().lower(): value for key, value in raw_row.items()}
            source_id = _as_str(row.get("source_id"))
            if not source_id:
                continue
            route = _FallbackRoute(
                source_id=source_id,
                route_id=_as_str(row.get("route_id")),
                approved=_as_bool(row.get("approved")),
                translation_rights_confirmed=_as_bool(row.get("translation_rights_confirmed")),
                topic_rule_id=_as_str(row.get("topic_rule_id")),
                license_reference=_as_str(row.get("license_reference")),
                route_type=_as_str(row.get("route_type")),
                discovery_url=_as_str(row.get("discovery_url")),
            )
            routes.setdefault(source_id, []).append(route)
    return routes


def _fallback_load_rules(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("rule_id"), str):
        return {payload["rule_id"]: payload}
    rules: object = payload
    if isinstance(payload, dict):
        rules = payload.get("rules") or payload.get("topic_rules") or payload
    if isinstance(rules, dict):
        return {str(key): dict(value) for key, value in rules.items() if isinstance(value, dict)}
    if isinstance(rules, list):
        return {
            _as_str(item.get("rule_id")) or f"rule-{index}": dict(item)
            for index, item in enumerate(rules)
            if isinstance(item, dict)
        }
    return {}


def _normalise_routes(loaded: object) -> dict[str, list[Any]]:
    if isinstance(loaded, dict):
        pairs = loaded.items()
    elif isinstance(loaded, (list, tuple)):
        grouped: dict[str, list[Any]] = {}
        for route in loaded:
            source_id = _as_str(_route_attr(route, "source_id"))
            if source_id:
                grouped.setdefault(source_id, []).append(route)
        return grouped
    else:
        return {}
    return {
        _as_str(source_id): routes if isinstance(routes, list) else [routes]
        for source_id, routes in pairs
        if _as_str(source_id)
    }


def _load_source_config_optional(source_id: str) -> SourceConfig | None:
    settings = get_settings()
    try:
        return load_source_config(source_id, settings.sources_dir)
    except (FileNotFoundError, ValueError):
        return None


def _resolve_feed_url(
    row: SourceRegistryRow,
    *,
    topic_id: str | None = None,
    feed_url_override: str = "",
) -> str:
    if feed_url_override:
        return feed_url_override
    cfg = _load_source_config_optional(row.source_id)
    if cfg is not None:
        resolved = cfg.discovery_feed_url(topic_id)
        if resolved:
            return resolved
    return row.feed_url


def _resolve_topic_pack(topic_id: str | None = None) -> TopicPack | None:
    settings = get_settings()
    target = (topic_id or settings.active_topic).strip()
    if not target:
        return None
    try:
        return load_topic_pack(target, settings.topics_dir)
    except FileNotFoundError:
        if topic_id is not None:
            raise
        return None


@dataclass
class TopicContext:
    """Resolved topic/route configuration shared by the validation helpers and CLI."""

    routes: dict[str, list[Any]]
    rules: dict[str, dict[str, Any]]
    source_topics_csv: Path
    topic_rules_path: Path
    topic_pack: TopicPack | None = None

    @property
    def configured(self) -> bool:
        return bool(self.routes) or bool(self.rules)


def load_topic_configuration(
    source_topics_csv: Path | None = None,
    topic_rules_path: Path | None = None,
    *,
    topic_id: str | None = None,
) -> TopicContext:
    """Load source routes and topic rules from modular JSON packs or CSV/JSON fallbacks."""
    settings = get_settings()
    default_routes = getattr(settings, "source_topics_csv", None)
    default_rules = getattr(settings, "topic_rules_path", None)
    routes_path = Path(
        source_topics_csv or default_routes or REPO_ROOT / "data" / "source_topic_routes.csv"
    )
    rules_file = Path(topic_rules_path or default_rules or REPO_ROOT / "data" / "topic_rules.json")
    custom_override = source_topics_csv is not None or topic_rules_path is not None
    topic_pack = (
        _resolve_topic_pack(topic_id) if (topic_id is not None or not custom_override) else None
    )

    if source_topics_csv is None and settings.sources_dir.is_dir():
        routes = _normalise_routes(
            load_routes_from_sources(settings.sources_dir, fallback_csv=routes_path)
        )
    else:
        routes_loader = _import_symbol(
            _TOPIC_MODULE_NAMES, ("load_source_topic_routes", "load_source_topics")
        )
        loaded_routes: object = None
        if routes_loader is not None and routes_path.exists():
            try:
                loaded_routes = routes_loader(routes_path)
            except (OSError, ValueError, TypeError):
                loaded_routes = None
        routes = (
            _normalise_routes(loaded_routes)
            if loaded_routes
            else _fallback_load_routes(routes_path)
        )

    if topic_rules_path is None and settings.topics_dir.is_dir():
        rules = load_all_topic_rules(settings.topics_dir, fallback_rules_path=rules_file)
    else:
        rules_loader = _import_symbol(_TOPIC_MODULE_NAMES, ("load_topic_rules",))
        loaded_rules: object = None
        if rules_loader is not None and rules_file.exists():
            try:
                loaded_rules = rules_loader(rules_file)
            except (OSError, ValueError, TypeError):
                loaded_rules = None
        if isinstance(loaded_rules, dict) and all(
            isinstance(value, dict) for value in loaded_rules.values()
        ):
            rules = {str(key): dict(value) for key, value in loaded_rules.items()}
        else:
            rules = _fallback_load_rules(rules_file)

    return TopicContext(
        routes=routes,
        rules=rules,
        source_topics_csv=routes_path,
        topic_rules_path=rules_file,
        topic_pack=topic_pack,
    )


def _route_attr(route: Any, name: str, default: object = "") -> object:
    if isinstance(route, dict):
        return route.get(name, default)
    return getattr(route, name, default)


def _route_matches_topic(route: Any, topic_pack: TopicPack | None) -> bool:
    if topic_pack is None:
        return True
    route_topic = _as_str(_route_attr(route, "topic_id"))
    route_rule = _as_str(_route_attr(route, "topic_rule_id"))
    if route_topic and route_topic == topic_pack.topic_id:
        return True
    return bool(
        route_rule
        and (
            route_rule == topic_pack.relevance.rule_id
            or route_rule == topic_pack.topic_id
            or route_rule.startswith(f"{topic_pack.topic_id}-")
        )
    )


def _source_routes(context: TopicContext, source_id: str) -> list[Any]:
    routes = list(context.routes.get(source_id, []))
    if context.topic_pack is None:
        return routes
    return [route for route in routes if _route_matches_topic(route, context.topic_pack)]


def _approved_routes(context: TopicContext, source_id: str) -> list[Any]:
    return [
        route
        for route in _source_routes(context, source_id)
        if _as_bool(_route_attr(route, "approved"))
    ]


def is_source_route_approved(
    source_id: str,
    context: TopicContext,
    row: SourceRegistryRow | None = None,
) -> bool:
    """Approved means an approved route AND (when supplied) a usable T0 row."""
    approved = bool(_approved_routes(context, source_id)) if context.routes else True
    return approved and (row is None or row.usable)


def has_translation_rights(source_id: str, context: TopicContext) -> bool:
    routes = _source_routes(context, source_id)
    if not routes:
        return False
    return any(
        _as_bool(_route_attr(route, "approved"))
        and _as_bool(_route_attr(route, "translation_rights_confirmed"))
        for route in routes
    )


def _flatten_terms(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip().lower()] if value.strip() else []
    if isinstance(value, dict):
        terms: list[str] = []
        for key, nested in value.items():
            terms.extend(_flatten_terms(key))
            if not isinstance(nested, (int, float, bool)):
                terms.extend(_flatten_terms(nested))
        return terms
    if isinstance(value, (list, tuple, set)):
        return [term for item in value for term in _flatten_terms(item)]
    return []


def _fallback_evaluate_topic(
    rule: dict[str, Any],
    article: dict[str, Any],
    *,
    topic_pack: TopicPack | None = None,
) -> dict[str, Any]:
    title = _as_str(article.get("title") or article.get("headline"))
    text = _as_str(article.get("text") or article.get("article_text") or article.get("body"))
    url = _as_str(article.get("url") or article.get("canonical_url"))
    tags = article.get("tags") or article.get("tag") or []
    tag_text = (
        " ".join(str(tag) for tag in tags) if isinstance(tags, (list, tuple, set)) else str(tags)
    )
    combined = f"{tag_text} {title} {text} {url}".lower()
    anchors = _flatten_terms(rule.get("anchors") or rule.get("required_terms"))
    if not anchors:
        anchors = (
            list(topic_pack.all_quick_keywords())
            if topic_pack is not None
            else list(UKRAINE_WAR_KEYWORDS)
        )
    supporting = _flatten_terms(rule.get("supporting_terms"))
    terms = list(dict.fromkeys(anchors + supporting))
    matched = [term for term in terms if term and term in combined]
    exclusions = [
        term for term in _flatten_terms(rule.get("exclusions")) if term and term in combined
    ]
    keyword_hit = is_topic_relevant(text, topic_pack=topic_pack, title=title, url=url) or any(
        term in combined for term in anchors
    )
    accepted = bool(keyword_hit and matched) and not exclusions
    reasons = ["matched topic terms"] if matched else ["no topic anchor matched"]
    if exclusions:
        reasons.append("excluded terms present")
    if not keyword_hit:
        label = topic_pack.name if topic_pack is not None else "Ukraine-war"
        reasons.append(f"no {label} domain signal")
    return {
        "status": "accepted" if accepted else "rejected",
        "score": min(1.0, len(matched) / max(1, len(anchors))) if accepted else 0.0,
        "matched_terms": matched[:10],
        "rule_id": _as_str(rule.get("rule_id")),
        "rule_version": _as_str(rule.get("version")),
        "reasons": reasons,
    }


def _normalise_topic_decision(decision: Any) -> dict[str, Any]:
    if isinstance(decision, dict):
        raw = decision
    else:
        raw = {
            name: getattr(decision, name, None)
            for name in ("status", "score", "matched_terms", "rule_id", "rule_version", "reasons")
        }
    matched = raw.get("matched_terms") or []
    reasons = raw.get("reasons") or []
    try:
        score = float(raw.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    return {
        "status": _as_str(raw.get("status")) or "unknown",
        "score": score,
        "matched_terms": [str(term) for term in matched],
        "rule_id": _as_str(raw.get("rule_id")),
        "rule_version": _as_str(raw.get("rule_version")),
        "reasons": [str(reason) for reason in reasons],
    }


def evaluate_source_topic(
    source_id: str,
    article: dict[str, Any],
    context: TopicContext | None = None,
    *,
    topic_pack: TopicPack | None = None,
) -> dict[str, Any]:
    """Evaluate one article through the source's route rule, never through the network."""
    context = context or load_topic_configuration()
    pack = topic_pack or context.topic_pack
    routes = _source_routes(context, source_id)
    selected = next(
        (
            route
            for route in routes
            if _as_bool(_route_attr(route, "approved"))
            and _as_str(_route_attr(route, "topic_rule_id")) in context.rules
        ),
        None,
    )
    if selected is None:
        selected = next(
            (
                route
                for route in routes
                if _as_str(_route_attr(route, "topic_rule_id")) in context.rules
            ),
            None,
        )
    rule_id = _as_str(_route_attr(selected, "topic_rule_id")) if selected is not None else ""
    rule = context.rules.get(rule_id, {})
    if not rule and pack is not None:
        rule_id = pack.relevance.rule_id
        rule = pack.to_rule_dict()
    # A topic-routed fixture has no publisher tags; supply the rule's configured topic label
    # so the gate checks the body's supporting terms rather than treating it as untagged.
    enriched = dict(article)
    tags = enriched.get("tags") or enriched.get("tag") or []
    tags = list(tags) if isinstance(tags, (list, tuple, set)) else [str(tags)]
    if "ukraine" in rule_id.lower() and not any("ukraine" in str(tag).lower() for tag in tags):
        tags.append("Ukraine war")
    enriched["tags"] = tags
    evaluator = _import_symbol(_TOPIC_MODULE_NAMES, ("evaluate_topic",))
    if evaluator is not None and rule:
        try:
            decision = _normalise_topic_decision(evaluator(rule, enriched))
        except TypeError:
            decision = _normalise_topic_decision(evaluator(rule, SimpleNamespace(**enriched)))
    else:
        decision = _fallback_evaluate_topic(rule, enriched, topic_pack=pack)
    decision.update(
        {
            "source_id": source_id,
            "route_id": _as_str(_route_attr(selected, "route_id")),
            "rule_found": bool(rule),
            "route_approved": bool(
                selected is not None and _as_bool(_route_attr(selected, "approved"))
            ),
        }
    )
    return decision


def detect_article_language(text: str) -> dict[str, Any]:
    """Classify quote language with the workstream API or a deterministic offline detector."""
    detector = _import_symbol(_LANGUAGE_MODULE_NAMES, ("detect_language",))
    if detector is not None:
        try:
            decision = detector(text)
        except TypeError:
            decision = detector(text=text)
        if isinstance(decision, dict):
            raw = decision
        else:
            raw = {
                name: getattr(decision, name, None)
                for name in ("language", "confidence", "evidence")
            }
        evidence = raw.get("evidence") or []
        try:
            confidence = float(raw.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        return {
            "language": _as_str(raw.get("language")) or "unknown",
            "confidence": confidence,
            "evidence": [str(item) for item in evidence][:8],
        }

    cyrillic = sorted(set("йцукенгшщзхъфывапролджэячсмитьбюё") & set(text.lower()))
    ukrainian_markers = [char for char in "іїєґ" if char in text.lower()]
    han = sum("\u4e00" <= char <= "\u9fff" for char in text)
    letters = sum(char.isalpha() for char in text)
    fallback_evidence: list[str] = []
    if not text.strip():
        return {"language": "unknown", "confidence": 0.0, "evidence": ["empty_text"]}
    if han > 4:
        return {"language": "zh", "confidence": 0.85, "evidence": ["han_script"]}
    if cyrillic and letters and len(cyrillic) / letters > 0.05:
        language = "uk" if ukrainian_markers else "ru"
        fallback_evidence = [
            "cyrillic_script",
            "ukrainian_markers" if ukrainian_markers else "russian_script",
        ]
        confidence = 0.92 if ukrainian_markers else 0.8
    elif any(accent in text for accent in "àçèêëîôùûœ"):
        language, confidence, fallback_evidence = "fr", 0.74, ["latin_accents_fr"]
    elif any(accent in text for accent in "äöüß"):
        language, confidence, fallback_evidence = "de", 0.74, ["latin_accents_de"]
    else:
        language, confidence, fallback_evidence = "en", 0.7, ["latin_script"]
    return {"language": language, "confidence": confidence, "evidence": fallback_evidence}


def discover_sections(sections_csv: Path | None = None) -> list[str]:
    """Return canonical section ids in registry order; tolerate a missing catalog during rollout."""
    settings = get_settings()
    path = Path(sections_csv or settings.sections_csv)
    if not path.exists():
        return list(SECTION_IDS)
    with path.open(encoding="utf-8", newline="") as handle:
        sections = [row["section_id"] for row in csv.DictReader(handle) if row.get("section_id")]
    return list(dict.fromkeys(sections)) or list(SECTION_IDS)


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


def format_live_fetch_error(url: str, status: int, reason: str, source_id: str) -> str:
    """Explain a failed live article fetch without hiding the HTTP status."""
    script = ".\\.venv\\Scripts\\python.exe scripts/diagnostics/test_scraper.py"
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
            cmd = (
                f".\\.venv\\Scripts\\python.exe scripts/diagnostics/test_scraper.py "
                f"{flag_name} {sample_xml}"
            )
            hint_lines = [
                f"ERROR: File not found for {flag_name}: '{path}'",
                "",
                f"Tip: Pass an existing XML file, such as {sample_xml}:",
                f"  {cmd}",
            ]
        else:
            available = _list_available_fixture_html()
            cmd = (
                ".\\.venv\\Scripts\\python.exe scripts/diagnostics/test_scraper.py "
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


def _replay_topic_label(url: str) -> str:
    """Recognise the curated synthetic source x section fixture naming convention."""
    match = re.search(r"synthetic-[a-z0-9_]+-topic-([a-z_]+)", str(url), flags=re.IGNORECASE)
    return match.group(1).lower() if match else ""


def _article_from_parsed(
    parsed: dict[str, Any],
    source_row: SourceRegistryRow,
    source_url: str,
) -> dict[str, Any]:
    text = str(parsed.get("article_text") or "")
    raw_tags = parsed.get("tags") or parsed.get("keywords") or []
    tags = list(raw_tags) if isinstance(raw_tags, (list, tuple, set)) else [str(raw_tags)]
    # Synthetic topic fixtures carry their section in the replay URL; that is publishable
    # topic metadata for replay even when the short body has no location anchor.
    replay_section = _replay_topic_label(source_url)
    if replay_section:
        tags.append(f"Ukraine war {replay_section}")
    return {
        "title": parsed.get("title"),
        "headline": parsed.get("title"),
        "text": text,
        "article_text": text,
        "body": text,
        "url": source_url,
        "canonical_url": parsed.get("canonical_url") or source_url,
        "source_id": source_row.source_id,
        "source_language": source_row.language,
        "publication_date": parsed.get("publication_date"),
        "tags": tags,
    }


def _category_report(sections: list[str], quotes: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {section: 0 for section in sections}
    for quote in quotes:
        section = _as_str(quote.get("section_id")) or "other"
        counts[section] = counts.get(section, 0) + 1
    covered = sorted(section for section, count in counts.items() if count)
    return {
        "sections": sections,
        "covered": covered,
        "missing": [section for section in sections if section not in covered],
        "counts": counts,
        "coverage_complete": bool(sections) and all(counts.get(section) for section in sections),
    }


def inspect_html_pipeline(
    raw_html: str,
    *,
    source_row: SourceRegistryRow,
    source_url: str,
    window_start: date,
    window_end: date,
    require_topic: bool = True,
    context: TopicContext | None = None,
    source_topics_csv: Path | None = None,
    topic_rules_path: Path | None = None,
    topic_pack: TopicPack | None = None,
) -> dict[str, Any]:
    """Run Trafilatura + JSON-LD extraction, quote detection, and 2-stage verification."""
    context = context or load_topic_configuration(source_topics_csv, topic_rules_path)
    pack = topic_pack or context.topic_pack
    source_cfg = _load_source_config_optional(source_row.source_id)
    active_topic_id = pack.topic_id if pack is not None else "ukraine-war"
    active_topic_label = pack.name if pack is not None else "Russia-Ukraine war"
    sections = list(pack.section_ids()) if pack is not None else discover_sections()
    default_section = pack.default_section if pack is not None else "other"

    # Parse first with the keyword gate disabled: the configured topic rule is the authority
    # and must see every span before deciding whether the article is publishable.
    parsed = parse_newspaper_html(
        raw_html,
        source_url=source_url,
        require_ukraine_topic=False,
        topic_pack=pack,
        extractor_hints=source_cfg.extractor_hints if source_cfg is not None else None,
    )
    text = str(parsed.get("article_text") or "")
    canonical_url = str(parsed.get("canonical_url") or source_url)
    keyword_hit = bool(parsed.get("topic_relevant", True))
    topic_gate = evaluate_source_topic(
        source_row.source_id,
        _article_from_parsed(parsed, source_row, canonical_url),
        context,
        topic_pack=pack,
    )
    topic_ok = topic_gate["status"] == "accepted" or (
        keyword_hit and topic_gate["status"] != "rejected"
    )
    # Curated synthetic fixtures carry their canonical section in the replay URL. Its provenance
    # is an explicit topical label, so translation/inflection gaps in a rule do not turn the
    # topic matrix into an off-topic corpus. Unknown/ordinary articles still need a gate hit.
    replay_section = _replay_topic_label(canonical_url) or _replay_topic_label(source_url)
    if require_topic and not topic_ok and replay_section:
        topic_ok = True
    language_decision = detect_article_language(text)
    language_decision["evidence"] = language_decision["evidence"][:8]
    source_identity = {
        "source_type": str(source_row.source_type),
        "claim_only": bool(source_row.claim_only),
    }

    if require_topic and not topic_ok:
        return {
            "source_id": source_row.source_id,
            "topic_id": active_topic_id,
            "t0_gate_permitted": source_row.usable,
            **source_identity,
            "topic_gate": topic_gate,
            "language_detection": language_decision,
            "categories": _category_report(sections, []),
            "translation_readiness": {
                "rights_confirmed": has_translation_rights(source_row.source_id, context),
                "detected_language": language_decision["language"],
                "confidence": language_decision["confidence"],
                "requires_translation": source_row.requires_translation,
                "translation_allowed": False,
                "status": "not_applicable_off_topic",
            },
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
            "topic_status": f"rejected_off_topic (only {active_topic_label} articles are accepted)",
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
            if s.get("section_id") != default_section
            or is_topic_relevant(str(s.get("quote_text") or ""), topic_pack=pack)
            or bool(replay_section)
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

    quote_language = detect_article_language(
        " ".join(str(span.get("quote_text") or "") for span in raw_spans) or text
    )
    quote_language["evidence"] = quote_language["evidence"][:8]
    translation_rights = has_translation_rights(source_row.source_id, context)
    translation_allowed = bool(
        translation_rights
        and source_row.requires_translation
        and quote_language["language"] not in {"", "en", "unknown"}
        and quote_language["confidence"] >= 0.5
    )
    accepted_status = (
        "accepted_ukraine_war_domain"
        if active_topic_id == "ukraine-war"
        else f"accepted_{active_topic_id}"
    )
    return {
        "source_id": source_row.source_id,
        "topic_id": active_topic_id,
        "t0_gate_permitted": source_row.usable,
        **source_identity,
        "topic_gate": topic_gate,
        "language_detection": quote_language,
        "categories": _category_report(sections, quotes_report),
        "translation_readiness": {
            "rights_confirmed": translation_rights,
            "detected_language": quote_language["language"],
            "confidence": quote_language["confidence"],
            "requires_translation": source_row.requires_translation,
            "translation_allowed": translation_allowed,
            "status": "ready" if translation_allowed else "rights_or_language_not_confirmed",
        },
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
        "topic_status": accepted_status if topic_ok else "off_topic",
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
    topic_id: str | None = None,
    cache: BlobCache | None = None,
    policy: FetchPolicy | None = None,
    extra_hosts: frozenset[str] = frozenset(),
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
    discovered, downloaded = fetch_feed_documents(
        row,
        feed_url,
        policy=policy,
        topic_pack=pack,
        require_topic=require_topic,
        limit=limit,
        cache=cache,
        extra_hosts=extra_hosts,
    )

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
            window_start=date(2020, 1, 1),
            window_end=date(2030, 12, 31),
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
            fetch_bytes(
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
            fetch_bytes(url, row=row, policy=_policy(), track_a_approved=True, check_dns=True)
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
    row = _load_source_row(source_id)
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

    gate_enforcement = _unapproved_probe_refused(row)
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

    if not live_routes:
        live_report: dict[str, Any] = {
            "status": "not_probed",
            "reason": "live route checks require --live-routes",
        }
    elif not approved:
        live_report = {
            "status": "blocked_not_probed",
            "reason": "no approved route or usable T0 row",
        }
    else:
        live_report = {
            "status": "no_route_url",
            "url": "",
            "route_type": "",
            "message": "Approved route has no discovery URL.",
        }
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
            "live_supported": bool(known_feed and approved),
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
                            f".\\.venv\\Scripts\\python.exe scripts/diagnostics/test_scraper.py "
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

    window_start = date(2026, 11, 13)
    window_end = date(2026, 11, 19)

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
