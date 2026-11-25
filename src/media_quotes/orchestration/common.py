"""Shared constants, configs, resources and helpers for Dagster orchestration."""

import json
import re
import sys
import warnings
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, TypeVar, cast

import dagster as dg

from ..config import DEFAULT_TOPIC_ID, Settings
from ..config import get_settings as get_settings
from ..db.resources import DatabaseResourceHandle
from ..graph import Neo4jResourceHandle

PARTITION_START = "2026-10-01"
CODE_VERSION = "c4-daily-pipeline-v3"
GRAPH_CODE_VERSION = "c4-neo4j-projection-v1"
SCHEDULE_CRON = "0 6 * * *"
TIER1_SCHEDULE_CRON = "0 */6 * * *"
SCHEDULE_TIMEZONE = "UTC"

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_Asset = TypeVar("_Asset", bound=Callable[..., Any])


def _compat(name: str) -> Any:
    """Late-bound façade lookup: tests monkeypatch these names on the package."""
    return getattr(sys.modules[__package__], name)


def _clean_slug(val: str | None, default: str) -> str:
    candidate = str(val or default).strip()
    if not _SLUG_RE.match(candidate):
        raise ValueError(f"Invalid topic/gold-set slug: {candidate!r}")
    return candidate


class DailyPipelineConfig(dg.Config):
    """Run config exposed to Dagster; no source rights are changed by this config."""

    dry_run: bool = False
    persist_to_postgres: bool | None = None
    topic_id: str | None = None
    source_mode: str = "live"
    tier: int | None = None
    max_cost: float | None = None
    fetch_archive: bool = True
    max_archive_articles: int = 200


class CheckConfig(dg.Config):
    """Topic and optional source mode used by the asset under check.

    Dagster does not expose the asset's materialization metadata to a check, so when a
    check is executed on its own it may be given the same topic and source mode as the
    asset run it validates. If source mode is omitted, the check detects the output root.
    """

    topic_id: str | None = None
    source_mode: str | None = None


class GoldSetCheckConfig(dg.Config):
    """Gold set whose evaluation report this check validates."""

    gold_set: str = "synthetic-v1"


class GoldSetEvalConfig(dg.Config):
    """Run config for on-demand gold-set evaluation."""

    gold_set: str = "synthetic-v1"
    report_date: str | None = None


@dg.resource
def settings_resource(_: dg.InitResourceContext) -> Settings:
    """Injectable settings resource; avoids hidden module-level configuration calls."""
    return cast(Settings, _compat("get_settings")())


@dg.resource
def database_resource(_: dg.InitResourceContext) -> DatabaseResourceHandle:
    """Database session factory resource with engine disposal owned by the service."""
    from ..pipeline import _default_session_context

    return DatabaseResourceHandle(_default_session_context)


@dg.resource
def neo4j_resource(_: dg.InitResourceContext) -> Iterator[Neo4jResourceHandle]:
    """Own the lazy Neo4j driver for the duration of a Dagster run."""
    handle = Neo4jResourceHandle(_compat("get_settings")())
    try:
        yield handle
    finally:
        handle.close()


def _load_audit_report(
    settings: Settings, report_date: date, topic_id: str | None = None
) -> dict[str, Any]:
    safe_topic = _clean_slug(topic_id, settings.active_topic)
    name = f"audit-{report_date.isoformat()}.json"
    from ..cli.audit import _audit_target_dir

    target = _audit_target_dir(settings, safe_topic) / name
    if target.is_file():
        return cast(dict[str, Any], json.loads(target.read_text(encoding="utf-8")))
    return {}


def _store_path(settings: Settings, report_date: date, topic_id: str | None = None) -> Path:
    """Resolve the export path for the topic the partition actually ran with."""
    from ..export import local_store_path

    safe_topic = _clean_slug(topic_id, settings.active_topic)
    return local_store_path(settings.export_dir, report_date, topic_id=safe_topic)


def _check_topic(settings: Settings, report_date: date, requested: str | None) -> str:
    """Pick the topic of the newest audit report when a check has no explicit topic."""
    if requested:
        return _clean_slug(requested, settings.active_topic)
    from ..cli.audit import _audit_target_dir

    name = f"audit-{report_date.isoformat()}.json"
    candidates = [(_audit_target_dir(settings, DEFAULT_TOPIC_ID) / name, DEFAULT_TOPIC_ID)]
    if settings.audit_dir.is_dir():
        candidates.extend(
            (sub / name, sub.name) for sub in sorted(settings.audit_dir.iterdir()) if sub.is_dir()
        )
    found = [(path.stat().st_mtime, topic) for path, topic in candidates if path.is_file()]
    return max(found)[1] if found else settings.active_topic


def _context_partition_range(context: Any) -> dg.PartitionKeyRange | None:
    """Return a single-run backfill range when Dagster supplies one."""
    for attribute in ("partition_key_range", "asset_partition_key_range"):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                value = getattr(context, attribute, None)
        except (AttributeError, KeyError, IndexError, TypeError):
            continue
        if value is None:
            continue
        start = getattr(value, "start", None)
        end = getattr(value, "end", None)
        if isinstance(start, str) and isinstance(end, str):
            return dg.PartitionKeyRange(start=start, end=end)
    return None


def _context_partition_keys(context: Any) -> list[str]:
    """Read one or many partition keys from any asset/check context variant."""
    partition_range = _context_partition_range(context)
    if partition_range is not None:
        start = date.fromisoformat(partition_range.start)
        end = date.fromisoformat(partition_range.end)
        return [
            (start + timedelta(days=offset)).isoformat() for offset in range((end - start).days + 1)
        ]
    for attribute in ("asset_partition_key", "partition_key"):
        try:
            value = getattr(context, attribute, None)
        except (AttributeError, KeyError, IndexError, TypeError):
            continue
        if isinstance(value, str) and value:
            return [value]
    try:
        keys = getattr(context, "asset_partition_keys", None)
        if keys:
            return [str(key) for key in keys]
    except (AttributeError, KeyError, IndexError, TypeError):
        pass
    return []


def _check_dates(context: dg.AssetCheckExecutionContext, settings: Settings) -> list[date]:
    dates: list[date] = []
    for key in _context_partition_keys(context):
        try:
            dates.append(date.fromisoformat(key))
        except ValueError:
            continue
    return dates or [settings.report_date]


def _check_settings(settings: Settings, config: CheckConfig, dates: Sequence[date]) -> Settings:
    """Checks validate operational output unless fixture output is explicitly requested."""
    del dates
    return settings.for_source_mode(config.source_mode or "blobs")


def _graph_check_target(
    context: dg.AssetCheckExecutionContext, settings: Settings, config: CheckConfig
) -> tuple[list[date], Settings, str, bool]:
    dates = _check_dates(context, settings)
    app_settings = _check_settings(settings, config, dates)
    topic = _check_topic(app_settings, dates[0], config.topic_id)
    fixture_root = settings.for_source_mode("fixtures").export_dir
    skipped = (
        not app_settings.sync_to_neo4j
        or config.source_mode == "fixtures"
        or app_settings.export_dir == fixture_root
    )
    return dates, app_settings, topic, skipped


def _graph_export_quotes(
    settings: Settings, dates: Sequence[date], topic: str
) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """Quote ID -> (content hash, own partition start), plus dates with no export file."""
    from ..export import read_jsonl

    expected: dict[str, tuple[str, str]] = {}
    missing: list[str] = []
    for report_date in dates:
        path = _store_path(settings, report_date, topic)
        if not path.is_file():
            missing.append(report_date.isoformat())
            continue
        for record in read_jsonl(path):
            expected[record.quote_id] = (
                record.content_hash,
                datetime.combine(record.publication_date, time.min, tzinfo=UTC).isoformat(),
            )
    return expected, missing


def _graph_projected_quotes(
    session: Any, quote_ids: Sequence[str], topic: str
) -> dict[str, tuple[str, str]]:
    """Read this topic's projected quotes as quote ID -> (content hash, projected_at)."""
    rows = session.run(
        "MATCH (q:Quote) WHERE q.canonical_quote_id IN $quote_ids "
        "AND q.topic_id = $topic_id "
        "RETURN q.canonical_quote_id AS quote_id, q.content_hash AS content_hash, "
        "q.projected_at AS projected_at",
        {"quote_ids": list(quote_ids), "topic_id": topic},
    ).data()
    return {
        str(row["quote_id"]): (
            str(row.get("content_hash") or ""),
            str(row.get("projected_at") or ""),
        )
        for row in rows
    }


def _graph_stale_quotes(
    expected: Mapping[str, tuple[str, str]],
    projected: Mapping[str, tuple[str, str]],
    *,
    check_content: bool = False,
) -> list[str]:
    """Quotes absent, projected before their own partition, or changed in content."""
    stale: list[str] = []
    for quote_id, (content_hash, partition_start) in expected.items():
        row = projected.get(quote_id)
        if row is None or row[1] < partition_start or (check_content and row[0] != content_hash):
            stale.append(quote_id)
    return sorted(stale)


def _graph_skip_reason(settings: Settings, daily_result: Mapping[str, Any]) -> str:
    if not settings.sync_to_neo4j:
        return "graph sync disabled"
    source_mode = str(daily_result.get("source_mode") or "")
    if source_mode == "fixtures":
        return "fixture replay cannot write to the live graph"
    if source_mode not in {"live", "blobs"}:
        return "source mode missing or unsafe"
    if daily_result.get("dry_run"):
        return "dry run"
    if "backfill_range" in daily_result:
        return "range backfill already projected the complete topic"
    if not daily_result.get("quotes"):
        return "partition has no verified quotes"
    return ""


def _backfillable_asset(**kwargs: Any) -> Callable[[_Asset], dg.AssetsDefinition]:
    """Declare the beta single-run policy without leaking its warning into strict test runs."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", dg.BetaWarning)
        return cast(
            Callable[[_Asset], dg.AssetsDefinition],
            dg.asset(backfill_policy=dg.BackfillPolicy.single_run(), **kwargs),
        )
