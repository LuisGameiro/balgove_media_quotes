"""Deterministic, offline and rights-gated source adapters package.

Re-exports all public symbols previously exposed by ``media_quotes.adapters`` while
separating blob caching, RSS/Sitemap discovery, HTML/JSON-LD extraction, quote
attribution, base adapters, and the plugin registry into cohesive submodules.
"""

from __future__ import annotations

from .base import (
    ApiAdapter,
    GdeltAdapter,
    LicensedDatabaseAdapter,
    RssAdapter,
    SitemapAdapter,
    SourceAdapter,
    SourceNotPermitted,
    WebAdapter,
)
from .blob_cache import BlobCache, FixtureSource
from .html_extractor import extract_json_ld_metadata, parse_newspaper_html
from .quote_extractor import (
    UKRAINE_WAR_KEYWORDS,
    infer_section,
    is_topic_relevant,
    is_ukraine_war_relevant,
)
from .registry import (
    build_adapter,
    register_adapter,
    register_source_adapter,
    registered_adapters,
    registered_source_adapters,
)
from .rss import parse_rss_feed
from .sitemap import parse_sitemap_xml

__all__ = [
    "UKRAINE_WAR_KEYWORDS",
    "ApiAdapter",
    "BlobCache",
    "FixtureSource",
    "GdeltAdapter",
    "LicensedDatabaseAdapter",
    "RssAdapter",
    "SitemapAdapter",
    "SourceAdapter",
    "SourceNotPermitted",
    "WebAdapter",
    "build_adapter",
    "extract_json_ld_metadata",
    "infer_section",
    "is_topic_relevant",
    "is_ukraine_war_relevant",
    "parse_newspaper_html",
    "parse_rss_feed",
    "parse_sitemap_xml",
    "register_adapter",
    "register_source_adapter",
    "registered_adapters",
    "registered_source_adapters",
]
