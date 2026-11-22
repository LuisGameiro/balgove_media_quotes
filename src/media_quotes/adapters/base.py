"""Base T0-gated source adapter and standard discovery-method adapter subclasses."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from ..exceptions import SourceNotPermitted
from ..registry import SourceRegistryRow
from .blob_cache import _FIXTURE_SCHEME, FixtureSource
from .html_extractor import _FixtureHTMLParser, _parse_json_fixture, parse_newspaper_html
from .quote_extractor import _extract_quotes
from .rss import parse_rss_feed
from .sitemap import parse_sitemap_xml

if TYPE_CHECKING:
    from ..source_configs import ExtractorHints, SourceConfig
    from ..topics import TopicPack

__all__ = [
    "ApiAdapter",
    "GdeltAdapter",
    "LicensedDatabaseAdapter",
    "RssAdapter",
    "SitemapAdapter",
    "SourceAdapter",
    "SourceNotPermitted",
    "WebAdapter",
]


class SourceAdapter:
    """Base adapter: T0 gate in live mode, labelled fixture replay in degraded mode."""

    discovery_method = "web"

    def __init__(  # pragma: no cover
        self,
        row: SourceRegistryRow,
        fixture_source: FixtureSource | None = None,
        *,
        replay: bool = False,
        topic_pack: TopicPack | None = None,
        source_config: SourceConfig | None = None,
    ) -> None:
        self.row = row
        self.fixture_source = fixture_source or FixtureSource()
        self.replay = replay
        self.topic_pack = topic_pack
        self.source_config = source_config

    @property
    def extractor_hints(self) -> ExtractorHints | None:
        return self.source_config.extractor_hints if self.source_config is not None else None

    @property
    def permitted(self) -> bool:  # pragma: no cover
        return self.row.usable

    @property
    def mode(self) -> str:  # pragma: no cover
        return "fixture_replay" if self.replay else "live"

    def _require_permission(self) -> None:  # pragma: no cover
        if not self.replay and not self.permitted:
            raise SourceNotPermitted(
                f"source '{self.row.source_id}' is not permitted: "
                f"enabled={self.row.enabled}, rights={self.row.rights_status}, "
                f"access={self.row.access_mode}, discovery={self.row.discovery_method}"
            )

    def discover(self, start: date, end: date) -> list[str]:  # pragma: no cover
        self._require_permission()
        refs: list[str] = []
        for ref in self.fixture_source.list_refs(self.row.source_id):
            raw = self.fixture_source.read(ref)
            metadata = self._parse(
                raw,
                topic_pack=self.topic_pack,
                extractor_hints=self.extractor_hints,
            )
            published = metadata.get("publication_date", "")
            if not published:
                continue
            day = date.fromisoformat(str(published)[:10])
            if start <= day <= end:
                refs.append(ref)
        return refs

    def fetch(self, article_ref: str) -> bytes:  # pragma: no cover
        self._require_permission()
        if not article_ref.startswith(_FIXTURE_SCHEME):
            raise NotImplementedError(
                f"live {self.discovery_method} fetch is unavailable offline; "
                "pass a fixture:// ref for degraded-mode replay"
            )
        return self.fixture_source.read(article_ref)

    def parse_metadata(
        self, raw: bytes, *, use_trafilatura: bool = False, source_url: str = ""
    ) -> dict[str, Any]:  # pragma: no cover
        self._require_permission()
        return self._parse(
            raw,
            use_trafilatura=use_trafilatura,
            source_url=source_url,
            topic_pack=self.topic_pack,
            extractor_hints=self.extractor_hints,
        )

    def extract_quotes(self, text: str) -> list[dict[str, Any]]:  # pragma: no cover
        self._require_permission()
        return _extract_quotes(text, topic_pack=self.topic_pack)

    def policy_flags(self) -> dict[str, Any]:  # pragma: no cover
        return self.row.policy_flags

    @staticmethod
    def _parse(
        raw: bytes,
        *,
        use_trafilatura: bool = False,
        source_url: str = "",
        topic_pack: TopicPack | None = None,
        extractor_hints: ExtractorHints | None = None,
    ) -> dict[str, Any]:  # pragma: no cover
        decoded = raw.decode("utf-8", errors="replace")
        if decoded.lstrip().startswith(("{", "[")):
            return _parse_json_fixture(decoded)
        # If the HTML has no synthetic <span class="quote"> tags, use the real parser.
        if 'class="quote"' not in decoded and (use_trafilatura or "<article" in decoded.lower()):
            from ..config import get_settings
            from ..extraction import static_html_fallback

            fallback = (
                static_html_fallback if get_settings().extraction_fallback == "static" else None
            )
            return parse_newspaper_html(
                decoded,
                source_url=source_url,
                topic_pack=topic_pack,
                extractor_hints=extractor_hints,
                fallback=fallback,
            )
        parser = _FixtureHTMLParser(topic_pack=topic_pack)
        parser.feed(decoded)
        parser.close()
        return parser.metadata()


class SitemapAdapter(SourceAdapter):
    discovery_method = "sitemap"

    def discover_from_xml(
        self, raw_xml: bytes | str, start: date, end: date
    ) -> list[dict[str, str]]:
        """Discover article URLs from an XML Sitemap within ``[start, end]``."""
        self._require_permission()
        return parse_sitemap_xml(raw_xml, start=start, end=end, topic_pack=self.topic_pack)


class RssAdapter(SourceAdapter):
    discovery_method = "rss"

    def discover_from_xml(
        self, raw_xml: bytes | str, start: date, end: date
    ) -> list[dict[str, str]]:
        """Discover article URLs from an RSS 2.0 or Atom XML feed within ``[start, end]``."""
        self._require_permission()
        return parse_rss_feed(raw_xml, start=start, end=end, topic_pack=self.topic_pack)


class ApiAdapter(SourceAdapter):
    discovery_method = "api"


class GdeltAdapter(SourceAdapter):
    discovery_method = "gdelt"


class LicensedDatabaseAdapter(SourceAdapter):
    discovery_method = "licensed_database"


class WebAdapter(SourceAdapter):
    discovery_method = "web"
