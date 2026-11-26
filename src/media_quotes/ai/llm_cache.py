"""Postgres-backed decision cache for the pinned LLM boundary.

Only successful decisions are written. The cache row keeps the full version
manifest (model, prompt, schema, input hash) so a version change produces a new
cache key instead of reusing a stale decision.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .llm_live import LLMDecision

if TYPE_CHECKING:
    from ..config import Settings
    from ..db.resources import SessionContextFactory
    from .llm_live import LLMRequest

_ENVELOPE = "__mq_db_cache_v1__"


class DbDecisionCache:
    """Cache hooks that read and write the `decision_cache` table."""

    def __init__(
        self,
        settings: Settings,
        session_context: SessionContextFactory | None = None,
    ) -> None:
        self.settings = settings
        self.session_context = session_context

    def _context(self) -> Any:
        """Caller-injected session context, falling back to the process default."""
        if self.session_context is not None:
            return self.session_context
        from ..pipeline import default_session_context

        return default_session_context

    def lookup(self, request: LLMRequest) -> LLMDecision | None:
        from ..db.repository import get_decision_cache

        with self._context()(self.settings) as session:
            row = get_decision_cache(session, request.cache_key)
        if not row:
            return None
        # A cache key collision or a stale row must never return a decision produced
        # under a different model/prompt/schema/input version.
        for field in ("model_snapshot", "prompt_version", "schema_hash", "input_hash"):
            if row.get(field) != getattr(request, field):
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
        # Cache writes commit on their own so a later pipeline rollback cannot erase them.
        from ..db.repository import DecisionCacheEntry, upsert_decision_cache

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
        with self._context()(self.settings) as session:
            return upsert_decision_cache(session, entry)
