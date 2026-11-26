"""Injectable inference boundary.

Provider selection comes from the environment only. Blocked/degraded mode uses a
deterministic local stand-in by default; ``LLM_PROVIDER=openrouter`` selects the
OpenAI-compatible OpenRouter client. No CLI flag changes the provider.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Protocol

from ..exceptions import ConfigError

if TYPE_CHECKING:
    from ..config import Settings
    from ..db.resources import SessionContextFactory
    from ..topics import TopicPack

_STANDIN = {
    "war_intensity": (
        "The selected quotes describe pressure on the front line and expectations about how "
        "fighting may develop."
    ),
    "international_support": (
        "The selected quotes focus on partner commitments and the practical delivery of support."
    ),
    "peace_initiatives": (
        "The selected quotes frame peace efforts around conditions, guarantees and verification."
    ),
    "sanctions": (
        "The selected quotes concern sanctions pressure, enforcement and its economic effects."
    ),
    "resources": (
        "The selected quotes point to manpower, equipment and industrial capacity on both sides."
    ),
    "other": "The selected quotes raise cross-cutting consequences of the war.",
}


class InferenceProvider(Protocol):
    """One section inference from the quotes actually selected for that section."""

    def infer(self, *, section_id: str, section_name: str, quote_texts: Sequence[str]) -> str: ...


class LocalStandInProvider:
    """Template-based, clearly labelled stand-in for the future pinned LLM provider."""

    def __init__(
        self,
        *,
        topic_pack: TopicPack | None = None,
        standin_map: Mapping[str, str] | None = None,
    ) -> None:
        merged = dict(_STANDIN)
        if topic_pack is not None:
            merged.update(topic_pack.standin_map())
        if standin_map is not None:
            merged.update(standin_map)
        self._standin = merged

    def infer(self, *, section_id: str, section_name: str, quote_texts: Sequence[str]) -> str:
        count = len(quote_texts)
        body = self._standin.get(
            section_id,
            f"The selected quotes concern {section_name.lower()} in this replay snapshot.",
        )
        return (
            "STAND-IN INFERENCE (deterministic; no LLM call): "
            f"{body} It reflects {count} verified quote(s) selected for this section and adds "
            "no facts beyond them."
        )


def make_provider(
    settings: Settings,
    *,
    topic_pack: TopicPack | None = None,
    session_context: SessionContextFactory | None = None,
) -> InferenceProvider:
    """Resolve the provider configured in the environment via ``Settings``."""
    provider = settings.llm_provider.strip().lower()
    if provider in {"local", "stand-in", "none", ""}:
        return LocalStandInProvider(topic_pack=topic_pack)
    if provider == "openrouter":
        from .llm_cache import DbDecisionCache
        from .llm_openrouter import OpenRouterInferenceProvider

        cache_hooks = (
            DbDecisionCache(settings, session_context=session_context)
            if settings.persist_to_postgres
            else None
        )
        return OpenRouterInferenceProvider(settings, cache_hooks=cache_hooks)
    raise ConfigError(
        f"inference provider '{provider}' is unavailable; set LLM_PROVIDER to local or openrouter"
    )


__all__ = ["InferenceProvider", "LocalStandInProvider", "make_provider"]
