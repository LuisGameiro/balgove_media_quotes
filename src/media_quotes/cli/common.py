"""Shared constants and helpers for the offline CLI commands."""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from .. import __version__
from ..config import DEFAULT_TOPIC_ID, PROJECT_ROOT, Settings, require_safe_id
from ..enums import APPROVED_RIGHTS
from ..exceptions import ConfigError
from ..export import atomic_write_text, content_hash
from ..registry import SourceRegistryRow, load_sources_csv
from ..risk_controls.models import ObsolescenceFlag
from ..source_configs import (
    DEFAULT_SOURCES_DIR,
    SourceConfig,
    load_registry_rows,
    load_source_configs,
)
from ..source_topics import load_source_topic_routes
from ..topics import DEFAULT_TOPICS_DIR, TopicPack, load_topic_pack

DATA_DIR = PROJECT_ROOT / "data"

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_GAPS = 2
EXIT_CONFIG = 3


def exit_code_for(*, ok: bool = True, gaps: Sequence[Any] = (), config_error: bool = False) -> int:
    """One exit-code contract: pass, validation failure, gaps, or config/T0 error."""
    if config_error:
        return EXIT_CONFIG
    if not ok:
        return EXIT_FAILURE
    if gaps:
        return EXIT_GAPS
    return EXIT_OK


_NOTICE = (
    "BLOCKED/DEGRADED MODE: quotes below come from clearly labelled synthetic fixtures for "
    "blocked mandatory sources. Not real reporting; no live source, database or LLM was used."
)
_MANDATORY = ("reuters", "bbc")
_ARTICLES_PER_SOURCE_DAY = 10.0  # planning envelope
_CALLS_PER_ARTICLE = 2.0
_PRICE_PER_CALL = 0.01
_ARTICLES_PER_HOUR = 100.0
_MAX_BACKFILL_DAYS = 365


def render_errors(errors: list[str]) -> int:
    """Print validation errors to stderr and return the validation exit code."""
    if errors:
        print(f"{len(errors)} validation error(s):", file=sys.stderr)
        for message in errors:
            print(f" - {message}", file=sys.stderr)
        return 1
    print("All data files valid.")
    return 0


def _sha256_file(path: Path) -> str:  # pragma: no cover
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_review_flags(settings: Settings) -> list[ObsolescenceFlag]:  # pragma: no cover
    """Read and validate the obsolescence queue. One loader for CLI, sensor and maintenance."""
    queue_path = settings.audit_dir / "review-queue.json"
    if not queue_path.is_file():
        return []
    payload = _read_json(queue_path)
    flags_data = payload.get("flags", []) if isinstance(payload, dict) else []
    flags: list[ObsolescenceFlag] = []
    if isinstance(flags_data, list):
        for item in flags_data:
            if isinstance(item, dict):
                flag = ObsolescenceFlag.model_validate_json(json.dumps(item))
                for field in ("reviewed_at", "raised_at"):
                    value = getattr(flag, field)
                    if isinstance(value, datetime) and value.tzinfo is None:
                        flag = flag.model_copy(update={field: value.replace(tzinfo=UTC)})
                flags.append(flag)
    return flags


def _write_json(path: Path, payload: dict[str, Any]) -> None:  # pragma: no cover
    atomic_write_text(
        path, json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )


def _read_json(path: Path) -> dict[str, Any]:  # pragma: no cover
    if not path.is_file():
        return {}
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _registry_sha256(settings: Settings) -> str:  # pragma: no cover
    """Hash the registry the pipeline actually loads: JSON configs first, CSV as fallback."""
    sources_dir = getattr(settings, "sources_dir", None)
    if sources_dir is not None and Path(sources_dir).is_dir():
        files = sorted(Path(sources_dir).glob("*.json"))
        if files:
            parts = [
                f"{path.name}:{_sha256_file(path)}"
                for path in files
                if not path.name.endswith(".schema.json")
            ]
            return content_hash(parts)
    return _sha256_file(settings.sources_csv)


def _version_manifest(
    settings: Settings, topic_id: str | None = None
) -> dict[str, Any]:  # pragma: no cover
    return {
        "app_version": __version__,
        "cli_version": __version__,
        "schema_path": str(settings.schema_path),
        "schema_sha256": _sha256_file(settings.schema_path),
        "prompt_version": settings.prompt_version,
        "llm_model_snapshot": (
            settings.llm_model_snapshot if settings.llm_provider == "openrouter" else "unset"
        ),
        "inference_provider": (
            "openrouter" if settings.llm_provider == "openrouter" else "local-template-stand-in"
        ),
        "gold_set_version": settings.gold_set_version,
        "registry_sha256": _registry_sha256(settings),
        "sections_sha256": _sha256_file(settings.sections_csv),
        "active_topic": topic_id or getattr(settings, "active_topic", DEFAULT_TOPIC_ID),
        "fixture_replay": True,
    }


def _parse_report_date(value: str | None, settings: Settings) -> date:  # pragma: no cover
    if value is None:
        return settings.report_date
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ConfigError(f"invalid --date {value!r}: expected YYYY-MM-DD") from exc


def _window(report_date: date, settings: Settings) -> tuple[date, date]:  # pragma: no cover
    return report_date - timedelta(days=settings.report_window_days - 1), report_date


def _run_id(
    report_date: date, settings: Settings, *, topic_id: str | None = None
) -> str:  # pragma: no cover
    parts = [
        "run-v1",
        report_date.isoformat(),
        str(settings.report_window_days),
        _version_manifest(settings)["registry_sha256"],
    ]
    if topic_id and topic_id != DEFAULT_TOPIC_ID:
        parts.append(topic_id)
    digest = content_hash(parts)
    return f"run-{digest[:12]}"


def _trace_id(source_id: str, article_ref: str) -> str:  # pragma: no cover
    return f"trace-{content_hash([source_id, article_ref])[:16]}"


def _use_sources_dir(settings: Settings) -> bool:
    sources_dir = getattr(settings, "sources_dir", None)
    if sources_dir is None or not Path(sources_dir).is_dir():
        return False
    # If a caller/test explicitly overrode sources_csv to a custom location while leaving
    # sources_dir at the repo default, honour the custom CSV so isolated tests stay isolated.
    default_csv = (PROJECT_ROOT / "data" / "sources.csv").resolve()
    return not (
        Path(sources_dir).resolve() == DEFAULT_SOURCES_DIR.resolve()
        and settings.sources_csv.resolve() != default_csv
    )


def _load_registry(settings: Settings) -> list[SourceRegistryRow]:  # pragma: no cover
    if _use_sources_dir(settings):
        return load_registry_rows(settings.sources_dir, fallback_csv=settings.sources_csv)
    return load_sources_csv(settings.sources_csv)


def _load_source_configs(settings: Settings) -> dict[str, SourceConfig]:
    if not _use_sources_dir(settings):
        return {}
    try:
        return {cfg.source_id: cfg for cfg in load_source_configs(settings.sources_dir)}
    except (OSError, ValueError) as exc:
        raise ConfigError(f"invalid source config in {settings.sources_dir}: {exc}") from exc


def _translation_readiness_warning(settings: Settings) -> str | None:
    """Explain why translation would skip for every source, without changing exit codes."""
    mode = str(getattr(settings, "translation_mode", "off")).strip().lower()
    if mode != "translate":
        return None
    configs = _load_source_configs(settings)
    if any(route.translation_rights_confirmed for cfg in configs.values() for route in cfg.routes):
        return None
    routes_csv = getattr(settings, "source_topics_csv", None)
    if routes_csv is not None and Path(routes_csv).is_file():
        try:
            routes = load_source_topic_routes(Path(routes_csv))
            if any(
                route.translation_rights_confirmed for group in routes.values() for route in group
            ):
                return None
        except (OSError, ValueError, TypeError):
            pass
    return (
        "TRANSLATION_MODE=translate but no source route has translation_rights_confirmed=true; "
        "every quote will skip translation"
    )


def _load_topic_pack(
    settings: Settings,
    topic_id: str | None = None,
    *,
    strict: bool = False,
) -> TopicPack | None:
    target = str(
        topic_id or getattr(settings, "active_topic", DEFAULT_TOPIC_ID) or DEFAULT_TOPIC_ID
    ).strip()
    topics_dir = getattr(settings, "topics_dir", DEFAULT_TOPICS_DIR)
    try:
        return load_topic_pack(target, topics_dir)
    except (FileNotFoundError, ValueError) as exc:
        if strict or (topic_id is not None and target != DEFAULT_TOPIC_ID):
            raise ConfigError(f"unable to load topic pack {target!r}: {exc}") from exc
        return None


def _digest_path(settings: Settings, report_date: date, *, topic_id: str | None = None) -> Path:
    if topic_id and topic_id != DEFAULT_TOPIC_ID:
        require_safe_id(topic_id, "topic_id")
        return settings.digest_dir / topic_id / f"{report_date.isoformat()}.md"
    return settings.digest_dir / f"{report_date.isoformat()}.md"


def _gap_for(row: SourceRegistryRow) -> tuple[str, str]:  # pragma: no cover
    if row.rights_status == "blocked" or row.rights_status not in APPROVED_RIGHTS:
        return "blocked", "rights_blocked"
    if not row.enabled:
        return "blocked", "not_enabled"
    if not row.license_reference:
        return "blocked", "rights_blocked"
    return "degraded", "no_coverage"


def _gap_record(row: SourceRegistryRow, detail: str = "") -> dict[str, Any]:  # pragma: no cover
    status, reason = _gap_for(row)
    return {
        "source_id": row.source_id,
        "source_name": row.source_name,
        "tier": row.tier,
        "mandatory": row.source_id in _MANDATORY,
        "status": status,
        "gap_reason": reason,
        "rights_status": row.rights_status,
        "access_mode": row.access_mode,
        "license_reference": row.license_reference or "(none)",
        "detail": detail,
    }


def _event(  # pragma: no cover
    *,
    run_id: str,
    trace_id: str,
    source_id: str,
    step: str,
    status: str,
    **fields: object,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": run_id,
        "trace_id": trace_id,
        "source_id": source_id,
        "step": step,
        "status": status,
    }
    payload.update(fields)
    return payload


def _fixture_mode(row: SourceRegistryRow, settings: Settings) -> bool:  # pragma: no cover
    """Default replay uses fixtures for every source with fixtures; live routes stay opt-in."""
    return (settings.fixtures_dir / row.source_id).is_dir()
