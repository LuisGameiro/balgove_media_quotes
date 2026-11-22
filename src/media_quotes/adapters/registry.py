"""Plugin registry for discovery-method and per-source adapter overrides."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

from ..enums import ENUMERABLE_DISCOVERY
from ..registry import SourceRegistryRow
from .base import (
    ApiAdapter,
    GdeltAdapter,
    LicensedDatabaseAdapter,
    RssAdapter,
    SitemapAdapter,
    SourceAdapter,
    WebAdapter,
)
from .blob_cache import FixtureSource

if TYPE_CHECKING:
    from ..source_configs import SourceConfig
    from ..topics import TopicPack

__all__ = [
    "build_adapter",
    "register_adapter",
    "register_source_adapter",
    "registered_adapters",
    "registered_source_adapters",
]

_TAdapter = TypeVar("_TAdapter", bound=type[SourceAdapter])

_ADAPTERS: dict[str, type[SourceAdapter]] = {
    "sitemap": SitemapAdapter,
    "rss": RssAdapter,
    "api": ApiAdapter,
    "gdelt": GdeltAdapter,
    "licensed_database": LicensedDatabaseAdapter,
    "web": WebAdapter,
}

_SOURCE_ADAPTERS: dict[str, type[SourceAdapter]] = {}


def register_adapter(discovery_method: str) -> Callable[[_TAdapter], _TAdapter]:
    """Register an adapter class for a discovery method (e.g. ``rss``, ``api``)."""
    key = discovery_method.strip().lower()
    if not key:
        raise ValueError("discovery_method must be a non-empty string")

    def decorator(adapter_cls: _TAdapter) -> _TAdapter:
        _ADAPTERS[key] = adapter_cls
        return adapter_cls

    return decorator


def register_source_adapter(name: str) -> Callable[[_TAdapter], _TAdapter]:
    """Register a custom per-source or named adapter plugin.

    Matches either ``source_config.extractor_hints.adapter`` or ``row.source_id``.
    """
    key = name.strip().lower()
    if not key:
        raise ValueError("adapter plugin name must be a non-empty string")

    def decorator(adapter_cls: _TAdapter) -> _TAdapter:
        _SOURCE_ADAPTERS[key] = adapter_cls
        return adapter_cls

    return decorator


def registered_adapters() -> dict[str, type[SourceAdapter]]:
    """Return a copy of registered discovery-method adapters."""
    return dict(_ADAPTERS)


def registered_source_adapters() -> dict[str, type[SourceAdapter]]:
    """Return a copy of registered named / per-source adapter overrides."""
    return dict(_SOURCE_ADAPTERS)


def build_adapter(
    row: SourceRegistryRow,
    fixture_source: FixtureSource | None = None,
    *,
    replay: bool = False,
    topic_pack: TopicPack | None = None,
    source_config: SourceConfig | None = None,
) -> SourceAdapter:
    """Instantiate the best adapter for ``row``.

    Resolution order:
    1. Explicit ``source_config.extractor_hints.adapter`` plugin name (if registered)
    2. Per-source override registered via ``@register_source_adapter(row.source_id)``
    3. Discovery-method adapter registered via ``@register_adapter(row.discovery_method)``
    """
    if row.discovery_method not in ENUMERABLE_DISCOVERY:
        raise ValueError(f"non-enumerable discovery method: {row.discovery_method}")
    adapter_class: type[SourceAdapter] | None = None
    if source_config is not None and source_config.extractor_hints.adapter:
        hint_name = source_config.extractor_hints.adapter.strip().lower()
        adapter_class = _SOURCE_ADAPTERS.get(hint_name) or _ADAPTERS.get(hint_name)
        if adapter_class is None:
            raise ValueError(
                f"source {row.source_id!r}: unknown adapter plugin {hint_name!r} "
                f"(known: {sorted(set(_SOURCE_ADAPTERS) | set(_ADAPTERS))})"
            )
    if adapter_class is None:
        adapter_class = _SOURCE_ADAPTERS.get(row.source_id.lower())
    if adapter_class is None:
        adapter_class = _ADAPTERS.get(row.discovery_method, SourceAdapter)
    return adapter_class(
        row,
        fixture_source,
        replay=replay,
        topic_pack=topic_pack,
        source_config=source_config,
    )
