"""Shared constants and helpers for the offline CLI commands."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast

from .. import __version__
from ..config import PROJECT_ROOT, Settings
from ..enums import APPROVED_RIGHTS
from ..exceptions import ConfigError
from ..export import content_hash
from ..registry import SourceRegistryRow, load_sources_csv

DATA_DIR = PROJECT_ROOT / "data"

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_GAPS = 2
EXIT_CONFIG = 3

_NOTICE = (
    "BLOCKED/DEGRADED MODE: quotes below come from clearly labelled synthetic fixtures for "
    "blocked mandatory sources. Not real reporting; no live source, database or LLM was used."
)
_MANDATORY = ("reuters", "bbc")
_ARTICLES_PER_SOURCE_DAY = 10.0  # docs/02 §4 planning envelope, replaced by T2 measurements
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


def _write_json(path: Path, payload: dict[str, Any]) -> None:  # pragma: no cover
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _read_json(path: Path) -> dict[str, Any]:  # pragma: no cover
    if not path.is_file():
        return {}
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _version_manifest(settings: Settings) -> dict[str, Any]:  # pragma: no cover
    return {
        "app_version": __version__,
        "cli_version": __version__,
        "schema_path": str(settings.schema_path),
        "schema_sha256": _sha256_file(settings.schema_path),
        "prompt_version": settings.prompt_version,
        "llm_model_snapshot": settings.llm_model_snapshot,
        "inference_provider": "local-template-stand-in",
        "gold_set_version": settings.gold_set_version,
        "registry_sha256": _sha256_file(settings.sources_csv),
        "sections_sha256": _sha256_file(settings.sections_csv),
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


def _run_id(report_date: date, settings: Settings) -> str:  # pragma: no cover
    digest = content_hash(
        [
            "run-v1",
            report_date.isoformat(),
            str(settings.report_window_days),
            _version_manifest(settings)["registry_sha256"],
        ]
    )
    return f"run-{digest[:12]}"


def _trace_id(source_id: str, article_ref: str) -> str:  # pragma: no cover
    return f"trace-{content_hash([source_id, article_ref])[:16]}"


def _load_registry(settings: Settings) -> list[SourceRegistryRow]:  # pragma: no cover
    return load_sources_csv(settings.sources_csv)


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
        "offline": True,
    }
    payload.update(fields)
    return payload


def _fixture_mode(row: SourceRegistryRow, settings: Settings) -> bool:  # pragma: no cover
    """Blocked sources are replayed from fixtures; live gate stays closed."""
    return not row.usable and (settings.fixtures_dir / row.source_id).is_dir()
