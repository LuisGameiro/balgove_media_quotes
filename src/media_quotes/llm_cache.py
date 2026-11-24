"""Postgres-backed decision cache for the pinned LLM boundary.

Only successful decisions are written. The cache row keeps the full version
manifest (model, prompt, schema, input hash) so a version change produces a new
cache key instead of reusing a stale decision.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .llm_live import LLMDecision

if TYPE_CHECKING:
    from .config import Settings
    from .llm_live import LLMRequest

_ENVELOPE = "__mq_db_cache_v1__"


class DbDecisionCache:
    """Cache hooks that read and write the `decision_cache` table."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def lookup(self, request: LLMRequest) -> LLMDecision | None:
        from .db.repository import get_decision_cache
        from .pipeline import _default_session_context

        with _default_session_context(self.settings) as session:
            row = get_decision_cache(session, request.cache_key)
        if not row:
            return None
        if (
            row.get("model_snapshot") != request.model_snapshot
            or row.get("prompt_version") != request.prompt_version
            or row.get("schema_hash") != request.schema_hash
            or row.get("input_hash") != request.input_hash
        ):
            return None
        output = row.get("output") or {}
        if isinstance(output, dict) and output.get(_ENVELOPE):
            inner = output[_ENVELOPE]
            return LLMDecision(
                output=dict(inner.get("output") or {}),
                refusal=bool(inner.get("refusal", False)),
                incomplete=bool(inner.get("incomplete", False)),
                provider_response_id=inner.get("provider_response_id"),
                cached=True,
            )
        return LLMDecision(
            output=output if isinstance(output, dict) else {"text": str(output)}, cached=True
        )

    def write(self, request: LLMRequest, decision: LLMDecision) -> Any:
        from .db.repository import DecisionCacheEntry, upsert_decision_cache
        from .pipeline import _default_session_context

        if decision.refusal or decision.incomplete:
            return None
        envelope = {
            _ENVELOPE: {
                "output": decision.output,
                "refusal": decision.refusal,
                "incomplete": decision.incomplete,
                "provider_response_id": decision.provider_response_id,
            }
        }
        entry = DecisionCacheEntry(
            cache_key=request.cache_key,
            model_snapshot=request.model_snapshot,
            prompt_version=request.prompt_version,
            schema_hash=request.schema_hash,
            input_hash=request.input_hash,
            output=envelope,
            trace_id=None,
        )
        with _default_session_context(self.settings) as session:
            return upsert_decision_cache(session, entry)
