"""Route, topic and language helpers shared by the diagnostics submodules.

Both ``scripts/test_scraper.py`` and ``scripts/source_proof.py`` use these helpers;
CLI parsers and sample/demo data stay in the scripts. Nothing here runs at import
time or writes artifacts.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from media_quotes.adapters import UKRAINE_WAR_KEYWORDS, is_topic_relevant
from media_quotes.config import PROJECT_ROOT, get_settings
from media_quotes.enums import SECTION_IDS
from media_quotes.language import detect_language
from media_quotes.registry import SourceRegistryRow
from media_quotes.relevance import evaluate_topic, load_topic_rules
from media_quotes.source_configs import SourceConfig, load_routes_from_sources, load_source_config
from media_quotes.source_topics import load_source_topic_routes
from media_quotes.topics import TopicPack, load_all_topic_rules, load_topic_pack

REPO_ROOT = PROJECT_ROOT


def _as_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "approved"}


def _as_str(value: object) -> str:
    return "" if value is None else str(value).strip()


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
        loaded_routes: object = None
        if routes_path.exists():
            try:
                loaded_routes = load_source_topic_routes(routes_path)
            except (OSError, ValueError, TypeError):
                loaded_routes = None
        routes = _normalise_routes(loaded_routes)

    if topic_rules_path is None and settings.topics_dir.is_dir():
        rules = load_all_topic_rules(settings.topics_dir, fallback_rules_path=rules_file)
    else:
        loaded_rules: object = None
        if rules_file.exists():
            try:
                loaded_rules = load_topic_rules(rules_file)
            except (OSError, ValueError, TypeError):
                loaded_rules = None
        if isinstance(loaded_rules, dict) and all(
            isinstance(value, dict) for value in loaded_rules.values()
        ):
            rules = {str(key): dict(value) for key, value in loaded_rules.items()}
        else:
            rules = {}

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
    if rule:
        try:
            decision = _normalise_topic_decision(evaluate_topic(rule, enriched))
        except TypeError:
            decision = _fallback_evaluate_topic(rule, enriched, topic_pack=pack)
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
    """Classify quote language with the deterministic language module."""
    try:
        decision = detect_language(text)
    except TypeError:
        decision = detect_language(text=text)
    if isinstance(decision, dict):
        raw = decision
    else:
        raw = {
            name: getattr(decision, name, None) for name in ("language", "confidence", "evidence")
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


def discover_sections(sections_csv: Path | None = None) -> list[str]:
    """Return canonical section ids in registry order; tolerate a missing catalog during rollout."""
    settings = get_settings()
    path = Path(sections_csv or settings.sections_csv)
    if not path.exists():
        return list(SECTION_IDS)
    with path.open(encoding="utf-8", newline="") as handle:
        sections = [row["section_id"] for row in csv.DictReader(handle) if row.get("section_id")]
    return list(dict.fromkeys(sections)) or list(SECTION_IDS)
