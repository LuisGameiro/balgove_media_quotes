"""Cohesive CLI package for local development, CI and blocked/degraded replay.

Commands are offline by default: fixture replay, local JSONL/Markdown stores and deterministic
policy functions. The explicit ``fetch`` command opens a rights-gated socket only when run
without ``--dry-run``.

All names previously available on ``media_quotes.cli`` are re-exported here so that
``mq = media_quotes.cli:main`` and existing imports keep working.
"""

from .app import (
    _add_shared,
    _bootstrap,
    _build_parser,
    _do_doctor,
    _shared_parser,
    _validate,
    cli_entry,
    main,
)
from .audit import _audit_markdown, _audit_report, _do_audit, _write_audit
from .common import (
    _ARTICLES_PER_HOUR,
    _ARTICLES_PER_SOURCE_DAY,
    _CALLS_PER_ARTICLE,
    _MANDATORY,
    _MAX_BACKFILL_DAYS,
    _NOTICE,
    _PRICE_PER_CALL,
    DATA_DIR,
    EXIT_CONFIG,
    EXIT_FAILURE,
    EXIT_GAPS,
    EXIT_OK,
    _digest_path,
    _event,
    _fixture_mode,
    _gap_for,
    _gap_record,
    _load_registry,
    _load_source_configs,
    _load_topic_pack,
    _parse_report_date,
    _read_json,
    _run_id,
    _sha256_file,
    _trace_id,
    _translation_readiness_warning,
    _version_manifest,
    _window,
    _write_json,
    render_errors,
)
from .eval import _do_eval
from .fetch import _do_fetch
from .logs import _do_logs
from .maintenance import _do_restore_drill, _do_retention, _do_review
from .ops import _do_backfill, _do_dashboard, _do_digest, _do_export, _do_sources, _do_topics
from .orchestration_info import _do_dagster
from .run import _collect, _do_run, _process_source

__all__ = [
    "DATA_DIR",
    "EXIT_CONFIG",
    "EXIT_FAILURE",
    "EXIT_GAPS",
    "EXIT_OK",
    "_ARTICLES_PER_HOUR",
    "_ARTICLES_PER_SOURCE_DAY",
    "_CALLS_PER_ARTICLE",
    "_MANDATORY",
    "_MAX_BACKFILL_DAYS",
    "_NOTICE",
    "_PRICE_PER_CALL",
    "_add_shared",
    "_audit_markdown",
    "_audit_report",
    "_bootstrap",
    "_build_parser",
    "_collect",
    "_digest_path",
    "_do_audit",
    "_do_backfill",
    "_do_dagster",
    "_do_dashboard",
    "_do_digest",
    "_do_doctor",
    "_do_eval",
    "_do_export",
    "_do_fetch",
    "_do_logs",
    "_do_restore_drill",
    "_do_retention",
    "_do_review",
    "_do_run",
    "_do_sources",
    "_do_topics",
    "_event",
    "_fixture_mode",
    "_gap_for",
    "_gap_record",
    "_load_registry",
    "_load_source_configs",
    "_load_topic_pack",
    "_parse_report_date",
    "_process_source",
    "_read_json",
    "_run_id",
    "_sha256_file",
    "_shared_parser",
    "_trace_id",
    "_translation_readiness_warning",
    "_validate",
    "_version_manifest",
    "_window",
    "_write_audit",
    "_write_json",
    "cli_entry",
    "main",
    "render_errors",
]
