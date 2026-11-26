"""Modular file-per-source configuration (``data/sources/<source_id>.json``).

Each news outlet lives in its own JSON file containing:
- Authoritative registry & T0 rights fields (mirrored to ``data/sources.csv``)
- Per-topic discovery routes (mirrored to ``data/source_topic_routes.csv``)
- Optional extractor hints and custom adapter plugin selection

Adding or removing a source means adding or removing one JSON file under
``data/sources/`` — zero Python edits required.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .processing.extraction import ExtractionParams

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import DEFAULT_TOPIC_ID, PROJECT_ROOT
from .enums import (
    AccessMode,
    AvailabilityStatus,
    DiscoveryMethod,
    OriginalReportingFlag,
    RightsStatus,
    SourceType,
)
from .processing.export import atomic_write_text
from .registry import EXPECTED_COLUMNS, SourceRegistryRow, load_sources_csv
from .source_topics import SourceTopicRoute, load_source_topic_routes

__all__ = [
    "DEFAULT_SOURCES_DIR",
    "ExtractorHints",
    "SourceConfig",
    "SourceRouteConfig",
    "load_registry_rows",
    "load_routes_from_sources",
    "load_source_config",
    "load_source_config_file",
    "load_source_configs",
    "probe_source_admission",
    "scaffold_source_config",
    "sync_sources_to_csv",
]

DEFAULT_SOURCES_DIR = PROJECT_ROOT / "data" / "sources"
_SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

_ROUTES_COLUMNS: tuple[str, ...] = (
    "source_id",
    "route_id",
    "route_type",
    "discovery_url",
    "topic_rule_id",
    "priority",
    "fallback_route_id",
    "approved",
    "license_reference",
    "translation_rights_confirmed",
    "notes",
)


def robots_parser(robots_txt_content: str) -> Any:
    """Parse robots.txt with the stdlib parser; returns a RobotFileParser-compatible object."""
    from urllib.robotparser import RobotFileParser

    parser = RobotFileParser()
    parser.parse(robots_txt_content.splitlines())
    return parser


def robots_allows(
    robots_txt_content: str | None, url: str, user_agent: str = "MediaQuotesBot/0.1"
) -> bool:
    """Check one URL against robots.txt; absent content is treated as allow-all."""
    if not url or robots_txt_content is None:
        return True
    return bool(robots_parser(robots_txt_content).can_fetch(user_agent, url))


def probe_source_admission(
    homepage_url: str,
    *,
    feed_url: str = "",
    robots_txt_content: str | None = None,
    feed_sample_content: str | None = None,
    sample_article_html: str | None = None,
    sample_article_url: str = "",
    archive_start_date: date | None = None,
    user_agent: str = "MediaQuotesBot/0.1",
) -> dict[str, Any]:
    """Run automated admission probing (robots.txt, archive depth, first-hand check).

    Safe for offline execution via supplied content; live probing can inject HTTP bodies.
    Returns a structured dictionary of admission findings.
    """
    # 1. robots.txt check via the stdlib parser, including group-specific agents.
    checked_urls = [url for url in (feed_url, sample_article_url) if url]
    disallow_rules = [
        f"{url}: disallowed"
        for url in checked_urls
        if not robots_allows(robots_txt_content, url, user_agent)
    ]
    robots_ok = not disallow_rules

    # 2. Archive / feed depth check
    entries_found = 0
    earliest_date = None
    if feed_sample_content:
        import re

        date_matches = re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", feed_sample_content)
        if date_matches:
            earliest_date = min(date_matches)
        entries_found = max(
            feed_sample_content.count("<item>"), feed_sample_content.count("<entry>")
        )
    feed_depth_ok = entries_found >= 1 if feed_sample_content else True
    # archive depth: the feed must reach back at least as far as the declared archive start.
    archive_depth_ok = True
    if earliest_date is not None and archive_start_date is not None:
        archive_depth_ok = date.fromisoformat(earliest_date) <= archive_start_date

    # 3. First-hand quote check
    quote_count = 0
    if sample_article_html:
        from .adapters.quote_extractor import extract_quotes

        found_quotes = extract_quotes(sample_article_html)
        quote_count = len(found_quotes)
    first_hand_ok = quote_count >= 1 if sample_article_html else True

    return {
        "homepage_url": homepage_url,
        "feed_url": feed_url,
        "robots_txt_ok": robots_ok,
        "disallow_rules": disallow_rules,
        "feed_entries_found": entries_found,
        "earliest_feed_date": earliest_date,
        "feed_depth_ok": feed_depth_ok,
        "archive_start_date": archive_start_date.isoformat() if archive_start_date else None,
        "archive_depth_ok": archive_depth_ok,
        "sample_quotes_found": quote_count,
        "first_hand_check_ok": first_hand_ok,
        "admission_ready": bool(robots_ok and feed_depth_ok and first_hand_ok and archive_depth_ok),
    }


class ExtractorHints(BaseModel):
    """Per-source extraction and plugin hints."""

    model_config = ConfigDict(extra="forbid")

    adapter: str | None = None
    default_feed_url: str = ""
    favor_precision: bool = False
    favor_recall: bool = False
    include_comments: bool = False
    include_tables: bool = False
    min_text_length: int = Field(default=200, ge=0)
    fallback_byline: str = ""
    strip_selectors: list[str] = Field(default_factory=list)
    render_js: bool = False  # opt-in to the optional browser fallback; off for every source today

    def to_extraction_params(self) -> ExtractionParams:
        from .processing.extraction import ExtractionParams

        return ExtractionParams(
            favor_precision=self.favor_precision,
            favor_recall=self.favor_recall,
            include_comments=self.include_comments,
            include_tables=self.include_tables,
        )


class SourceRouteConfig(BaseModel):
    """One rights-gated topic discovery route for a source."""

    model_config = ConfigDict(extra="forbid")

    route_id: str = Field(min_length=1)
    route_type: str = Field(min_length=1)
    discovery_url: str = ""
    topic_rule_id: str = Field(min_length=1)
    topic_id: str = ""
    priority: int = 50
    fallback_route_id: str = ""
    approved: bool = False
    license_reference: str = ""
    translation_rights_confirmed: bool = False
    notes: str = ""

    def to_source_topic_route(self, source_id: str) -> SourceTopicRoute:
        return SourceTopicRoute(
            source_id=source_id,
            route_id=self.route_id,
            route_type=self.route_type,
            discovery_url=self.discovery_url,
            topic_rule_id=self.topic_rule_id,
            priority=self.priority,
            fallback_route_id=self.fallback_route_id,
            approved=self.approved,
            license_reference=self.license_reference,
            translation_rights_confirmed=self.translation_rights_confirmed,
            notes=self.notes,
        )


class SourceConfig(BaseModel):
    """Complete per-source JSON definition combining registry, routes and extractor hints."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    source_name: str = Field(min_length=1)
    homepage_url: str = Field(min_length=1)
    feed_url: str = ""
    tier: int = Field(ge=1, le=4)
    cadence_hours: int = Field(ge=0)
    country_region: str = Field(min_length=1)
    source_type: SourceType
    claim_only: bool = False
    language: str = Field(pattern=r"^[a-z]{2}$")
    requires_translation: bool = False
    paywalled: bool | None = None
    rights_status: RightsStatus
    access_mode: AccessMode
    license_reference: str = ""
    # Legacy configs predate this field and were approved as first-hand; new scaffolds opt out.
    original_reporting_flag: OriginalReportingFlag = "first-hand"
    discovery_method: DiscoveryMethod
    archive_start_date: date
    enabled: bool = False
    tier_block: bool = False
    blocker_reason: str = ""
    availability_status: AvailabilityStatus = "live"
    redirect_hosts: list[str] = Field(default_factory=list)
    bing_discovery_enabled: bool = False
    content_hosts: list[str] = Field(default_factory=list)
    notes: str = ""
    routes: list[SourceRouteConfig] = Field(default_factory=list)
    extractor_hints: ExtractorHints = Field(default_factory=ExtractorHints)

    @field_validator("archive_start_date", mode="before")
    @classmethod
    def _parse_archive_date(cls, value: Any) -> Any:
        return date.fromisoformat(value) if isinstance(value, str) else value

    @field_validator("redirect_hosts", "content_hosts")
    @classmethod
    def _validate_hostnames(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip().lower() for value in values]
        if any(not re.fullmatch(r"[a-z0-9.-]+", value) for value in cleaned):
            raise ValueError("host lists must contain hostnames only")
        return cleaned

    def to_registry_row(self) -> SourceRegistryRow:
        """Project to the strict ``SourceRegistryRow`` used by T0 gates and Postgres sync."""
        return SourceRegistryRow(
            source_id=self.source_id,
            source_name=self.source_name,
            homepage_url=self.homepage_url,
            feed_url=self.feed_url,
            tier=self.tier,
            cadence_hours=self.cadence_hours,
            country_region=self.country_region,
            source_type=self.source_type,
            claim_only=self.claim_only,
            language=self.language,
            requires_translation=self.requires_translation,
            paywalled=self.paywalled,
            rights_status=self.rights_status,
            access_mode=self.access_mode,
            license_reference=self.license_reference,
            discovery_method=self.discovery_method,
            archive_start_date=self.archive_start_date,
            enabled=self.enabled,
            tier_block=self.tier_block,
            blocker_reason=self.blocker_reason,
            availability_status=self.availability_status,
            notes=self.notes,
        )

    def routes_for_topic(
        self,
        topic_id: str | None = None,
        *,
        rule_id: str | None = None,
    ) -> list[SourceRouteConfig]:
        """Return matching routes sorted by ``(priority, route_id)``."""
        matched: list[SourceRouteConfig] = []
        for route in self.routes:
            if (
                (rule_id and route.topic_rule_id == rule_id)
                or (
                    topic_id
                    and (
                        route.topic_id == topic_id
                        or route.topic_rule_id == topic_id
                        or route.topic_rule_id.startswith(f"{topic_id}-")
                    )
                )
                or (topic_id is None and rule_id is None)
            ):
                matched.append(route)
        matched.sort(key=lambda item: (item.priority, item.route_id))
        return matched

    def discovery_feed_url(self, topic_id: str | None = None) -> str:
        """Resolve the best discovery feed URL for ``topic_id`` (or default feed)."""
        if topic_id:
            topic_routes = self.routes_for_topic(topic_id)
            for route in topic_routes:
                if not route.approved:
                    continue
                if route.discovery_url and (
                    "rss" in route.route_type.lower()
                    or "sitemap" in route.route_type.lower()
                    or "rss" in route.discovery_url.lower()
                    or "feed" in route.discovery_url.lower()
                    or route.discovery_url.lower().endswith(".xml")
                ):
                    return route.discovery_url
        if self.feed_url:
            return self.feed_url
        if self.extractor_hints.default_feed_url:
            return self.extractor_hints.default_feed_url
        for route in sorted(self.routes, key=lambda item: (item.priority, item.route_id)):
            if not route.approved:
                continue
            if route.discovery_url and (
                "rss" in route.route_type.lower()
                or "rss" in route.discovery_url.lower()
                or "feed" in route.discovery_url.lower()
            ):
                return route.discovery_url
        return ""


def _resolve_sources_dir(sources_dir: Path | None) -> Path:
    return Path(sources_dir) if sources_dir is not None else DEFAULT_SOURCES_DIR


def load_source_config_file(path: Path) -> SourceConfig:
    """Load and validate one ``data/sources/<source_id>.json`` file."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    config = SourceConfig.model_validate(payload)
    stem = Path(path).stem
    if stem != config.source_id:
        raise ValueError(
            f"source file {Path(path).name!r} stem {stem!r} does not match "
            f"source_id {config.source_id!r}"
        )
    return config


def load_source_configs(sources_dir: Path | None = None) -> list[SourceConfig]:
    """Load all ``*.json`` source configs sorted by ``(tier, source_id)``."""
    root = _resolve_sources_dir(sources_dir)
    if not root.is_dir():
        return []
    configs: list[SourceConfig] = []
    for path in sorted(root.glob("*.json")):
        if path.name.endswith(".schema.json"):
            continue
        configs.append(load_source_config_file(path))
    configs.sort(key=lambda item: (item.tier, item.source_id))
    return configs


def load_source_config(source_id: str, sources_dir: Path | None = None) -> SourceConfig:
    """Load one source config by ``source_id``."""
    cleaned = source_id.strip()
    if not _SOURCE_ID_RE.fullmatch(cleaned):
        raise ValueError(f"invalid source_id {source_id!r}")
    root = _resolve_sources_dir(sources_dir)
    path = root / f"{cleaned}.json"
    if not path.is_file():
        available = sorted(p.stem for p in root.glob("*.json")) if root.is_dir() else []
        raise FileNotFoundError(
            f"source config {cleaned!r} not found at {path}; available={available}"
        )
    return load_source_config_file(path)


def load_registry_rows(
    sources_dir: Path | None = None,
    *,
    fallback_csv: Path | None = None,
) -> list[SourceRegistryRow]:
    """Load registry rows from ``data/sources/*.json``, falling back to ``sources.csv``.

    When ``fallback_csv`` exists alongside ``sources_dir``, preserves the CSV row order
    for existing sources and appends any extra JSON-only sources sorted by ``source_id``.
    """
    configs = load_source_configs(sources_dir)
    if not configs:
        if fallback_csv is not None and Path(fallback_csv).is_file():
            return load_sources_csv(Path(fallback_csv))
        return []
    by_id = {cfg.source_id: cfg.to_registry_row() for cfg in configs}
    ordered: list[SourceRegistryRow] = []
    seen: set[str] = set()
    if fallback_csv is not None and Path(fallback_csv).is_file():
        for csv_row in load_sources_csv(Path(fallback_csv)):
            if csv_row.source_id in by_id:
                ordered.append(by_id[csv_row.source_id])
                seen.add(csv_row.source_id)
    for cfg in configs:
        if cfg.source_id not in seen:
            ordered.append(by_id[cfg.source_id])
    return ordered


def load_routes_from_sources(
    sources_dir: Path | None = None,
    *,
    fallback_csv: Path | None = None,
) -> dict[str, list[SourceTopicRoute]]:
    """Load per-source routes from ``data/sources/*.json``, falling back to CSV."""
    configs = load_source_configs(sources_dir)
    routes: dict[str, list[SourceTopicRoute]] = {}
    for cfg in configs:
        if not cfg.routes:
            continue
        source_routes = [route.to_source_topic_route(cfg.source_id) for route in cfg.routes]
        source_routes.sort(key=lambda item: (item.priority, item.route_id))
        routes[cfg.source_id] = source_routes
    # CSV routes are additive per source: a JSON route wins on route_id collision,
    # but a source with only CSV routes must not disappear from the combined view.
    if fallback_csv is not None and Path(fallback_csv).is_file():
        for source_id, csv_routes in load_source_topic_routes(Path(fallback_csv)).items():
            combined = routes.setdefault(source_id, [])
            known = {route.route_id for route in combined}
            combined.extend(route for route in csv_routes if route.route_id not in known)
    for source_routes in routes.values():
        source_routes.sort(key=lambda item: (item.priority, item.route_id))
    return {source_id: routes[source_id] for source_id in sorted(routes)}


def _csv_bool_str(value: bool) -> str:
    return "true" if value else "false"


def _csv_paywalled_str(value: bool | None) -> str:
    if value is None:
        return "unknown"
    return "true" if value else "false"


def sync_sources_to_csv(
    sources_dir: Path,
    *,
    sources_csv: Path,
    routes_csv: Path | None = None,
) -> tuple[int, int]:
    """Regenerate ``data/sources.csv`` (and optionally ``data/source_topic_routes.csv``)
    from ``data/sources/*.json`` so tabular contracts and documentation stay in sync.
    """
    configs = load_source_configs(sources_dir)
    by_id = {cfg.source_id: cfg for cfg in configs}

    # Preserve existing CSV ordering where present so diffs stay minimal.
    ordered_configs: list[SourceConfig] = []
    seen: set[str] = set()
    if sources_csv.is_file():
        with sources_csv.open(encoding="utf-8", newline="") as handle:
            for existing in csv.DictReader(handle):
                sid = (existing.get("source_id") or "").strip()
                if sid in by_id and sid not in seen:
                    ordered_configs.append(by_id[sid])
                    seen.add(sid)
    for cfg in configs:
        if cfg.source_id not in seen:
            ordered_configs.append(cfg)

    sources_buffer = io.StringIO(newline="")
    writer = csv.DictWriter(sources_buffer, fieldnames=EXPECTED_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for cfg in ordered_configs:
        writer.writerow(
            {
                "source_id": cfg.source_id,
                "source_name": cfg.source_name,
                "homepage_url": cfg.homepage_url,
                "feed_url": cfg.feed_url,
                "tier": str(cfg.tier),
                "cadence_hours": str(cfg.cadence_hours),
                "country_region": cfg.country_region,
                "source_type": cfg.source_type,
                "claim_only": _csv_bool_str(cfg.claim_only),
                "language": cfg.language,
                "requires_translation": _csv_bool_str(cfg.requires_translation),
                "paywalled": _csv_paywalled_str(cfg.paywalled),
                "rights_status": cfg.rights_status,
                "access_mode": cfg.access_mode,
                "license_reference": cfg.license_reference,
                "discovery_method": cfg.discovery_method,
                "archive_start_date": cfg.archive_start_date.isoformat(),
                "enabled": _csv_bool_str(cfg.enabled),
                "tier_block": _csv_bool_str(cfg.tier_block),
                "blocker_reason": cfg.blocker_reason,
                "availability_status": cfg.availability_status,
                "notes": cfg.notes,
            }
        )
    atomic_write_text(sources_csv, sources_buffer.getvalue())

    routes_written = 0
    if routes_csv is not None:
        routes_buffer = io.StringIO(newline="")
        writer = csv.DictWriter(
            routes_buffer, fieldnames=list(_ROUTES_COLUMNS), lineterminator="\n"
        )
        writer.writeheader()
        for cfg in ordered_configs:
            for route in sorted(cfg.routes, key=lambda item: (item.priority, item.route_id)):
                writer.writerow(
                    {
                        "source_id": cfg.source_id,
                        "route_id": route.route_id,
                        "route_type": route.route_type,
                        "discovery_url": route.discovery_url,
                        "topic_rule_id": route.topic_rule_id,
                        "priority": str(route.priority),
                        "fallback_route_id": route.fallback_route_id,
                        "approved": _csv_bool_str(route.approved),
                        "license_reference": route.license_reference,
                        "translation_rights_confirmed": _csv_bool_str(
                            route.translation_rights_confirmed
                        ),
                        "notes": route.notes,
                    }
                )
                routes_written += 1
        atomic_write_text(routes_csv, routes_buffer.getvalue())
    return len(ordered_configs), routes_written


def scaffold_source_config(
    source_id: str,
    *,
    source_name: str,
    homepage_url: str,
    feed_url: str = "",
    tier: int = 3,
    country_region: str = "Global",
    language: str = "en",
    topic_id: str = DEFAULT_TOPIC_ID,
    out_dir: Path | None = None,
) -> Path:
    """Create a safe T0-gated starter ``<source_id>.json`` ready for rights review."""
    cleaned = source_id.strip().lower()
    if not _SOURCE_ID_RE.fullmatch(cleaned):
        raise ValueError(
            f"invalid source_id {source_id!r}; use lowercase letters, digits, '-' or '_'"
        )
    root = _resolve_sources_dir(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{cleaned}.json"
    if target.exists():
        raise FileExistsError(f"source config already exists: {target}")
    cfg = SourceConfig(
        source_id=cleaned,
        source_name=source_name.strip() or cleaned.upper(),
        homepage_url=homepage_url.strip(),
        feed_url=feed_url.strip(),
        tier=tier,
        cadence_hours=24,
        country_region=country_region.strip() or "Global",
        source_type="independent",
        claim_only=False,
        language=language.strip().lower(),
        requires_translation=language.strip().lower() != "en",
        paywalled=None,
        rights_status="pending_counsel_review",
        access_mode="pending_review",
        license_reference="",
        original_reporting_flag="unknown",
        discovery_method="web",
        archive_start_date=date(2026, 10, 1),
        enabled=False,
        notes="Scaffolded source; complete T0 rights review before enabling.",
        routes=[
            SourceRouteConfig(
                route_id=f"route-{cleaned}-{topic_id}-default",
                route_type="topic_rss" if feed_url.strip() else "web",
                discovery_url=feed_url.strip() or homepage_url.strip(),
                topic_rule_id=f"{topic_id}-v1",
                topic_id=topic_id,
                priority=90,
                approved=False,
                license_reference="",
                translation_rights_confirmed=False,
                notes="Pending T0 rights approval.",
            )
        ],
        extractor_hints=ExtractorHints(default_feed_url=feed_url.strip()),
    )
    target.write_text(
        json.dumps(cfg.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return target
