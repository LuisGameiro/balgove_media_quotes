"""Stable façade for the database repository aggregates.

Implementations live in :mod:`.sources`, :mod:`.runs`, :mod:`.content`, :mod:`.review`
and :mod:`.evaluation`; existing ``from media_quotes.db.repository import ...`` callers
and monkeypatch targets keep resolving here.
"""

from .common import _inserted_count, _jsonable, _run_uuid
from .content import (
    persist_articles_and_quotes,
    persist_digest_manifest,
    persist_quote_classifications,
    read_export_records,
)
from .evaluation import (
    DecisionCacheEntry,
    eval_recent_failures,
    get_decision_cache,
    record_eval_run,
    sync_gold_set_labels,
    upsert_decision_cache,
)
from .review import persist_obsolescence_flags
from .runs import (
    _event_details,
    append_audit_events,
    finish_pipeline_run,
    read_audit_events,
    start_pipeline_run,
    sync_storage_partitions,
)
from .sources import sync_sources

__all__ = [
    "DecisionCacheEntry",
    "_event_details",
    "_inserted_count",
    "_jsonable",
    "_run_uuid",
    "append_audit_events",
    "eval_recent_failures",
    "finish_pipeline_run",
    "get_decision_cache",
    "persist_articles_and_quotes",
    "persist_digest_manifest",
    "persist_obsolescence_flags",
    "persist_quote_classifications",
    "read_audit_events",
    "read_export_records",
    "record_eval_run",
    "start_pipeline_run",
    "sync_gold_set_labels",
    "sync_sources",
    "sync_storage_partitions",
    "upsert_decision_cache",
]
