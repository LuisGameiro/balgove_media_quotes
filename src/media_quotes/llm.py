"""Injectable inference boundary.

Blocked/degraded mode uses a deterministic local stand-in; no provider SDK is imported and no
network call is made. A real provider can be supplied later by implementing ``InferenceProvider``
and passing it to the digest renderer.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from .exceptions import ConfigError

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

    def infer(
        self, *, section_id: str, section_name: str, quote_texts: Sequence[str]
    ) -> str: ...  # pragma: no cover


class LocalStandInProvider:
    """Template-based, clearly labelled stand-in for the future pinned LLM provider."""

    def infer(
        self, *, section_id: str, section_name: str, quote_texts: Sequence[str]
    ) -> str:  # pragma: no cover
        count = len(quote_texts)
        body = _STANDIN.get(
            section_id,
            f"The selected quotes concern {section_name.lower()} in this replay snapshot.",
        )
        return (
            "STAND-IN INFERENCE (deterministic; no LLM call): "
            f"{body} It reflects {count} verified quote(s) selected for this section and adds "
            "no facts beyond them."
        )


def make_provider(name: str = "local") -> InferenceProvider:  # pragma: no cover
    """Resolve a provider by name; live providers are disabled while sources are blocked."""
    if name in {"local", "stand-in", "none", ""}:
        return LocalStandInProvider()
    raise ConfigError(
        f"inference provider '{name}' is unavailable in blocked/degraded mode; "
        "implement InferenceProvider and inject it directly"
    )


__all__ = ["InferenceProvider", "LocalStandInProvider", "make_provider"]
