"""Deterministic, offline source adapters.

No adapter opens a socket or talks to a live service. In live mode every method first
applies the T0 gate; in ``replay=True`` mode the adapter reads clearly labelled synthetic
fixtures from ``data/fixtures`` and the caller must log that degraded mode explicitly.
"""

from __future__ import annotations

import json
import re
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .config import get_settings
from .enums import ENUMERABLE_DISCOVERY
from .exceptions import SourceNotPermitted
from .registry import SourceRegistryRow
from .text_utils import normalise_text

__all__ = [
    "ApiAdapter",
    "FixtureSource",
    "GdeltAdapter",
    "LicensedDatabaseAdapter",
    "RssAdapter",
    "SitemapAdapter",
    "SourceAdapter",
    "SourceNotPermitted",
    "WebAdapter",
    "build_adapter",
]

_FIXTURE_SCHEME = "fixture://"
_SAFE_ID = re.compile(r"^[a-z0-9_-]+$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_QUOTE_RE = re.compile(r"[\u201c\"]([^\u201d\"]{20,4000})[\u201d\"]")
_ATTRIBUTION_RE = re.compile(
    r"(?:said|says|added|told|according to|reported|—|-)\s+"
    r"([A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+){0,3})"
)
_SECTION_KEYWORDS = {
    "war_intensity": ("offensive", "shelling", "fighting", "front line", "casualt"),
    "international_support": ("support", "air defence", "partners", "aid", "assistance"),
    "peace_initiatives": ("ceasefire", "negotiation", "talks", "peace", "halt in strikes"),
    "sanctions": ("sanction", "restriction", "enforcement", "dual-use"),
    "resources": ("ammunition", "production", "personnel", "supplies", "budget"),
}


class FixtureSource:
    """Injectable, path-confined source of local fixture bytes.

    Refs have the shape ``fixture://<source_id>/<filename>``. Only regular files directly
    inside ``data/fixtures/<source_id>/`` can be read, so a fixture cannot escape its root.
    """

    def __init__(self, root: Path | None = None) -> None:  # pragma: no cover
        self.root = (root or get_settings().fixtures_dir).resolve()

    def list_refs(self, source_id: str) -> list[str]:  # pragma: no cover
        folder = self._folder(source_id)
        if not folder.is_dir():
            return []
        names = sorted(
            path.name
            for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() in {".html", ".json", ".xml"}
        )
        return [f"{_FIXTURE_SCHEME}{source_id}/{name}" for name in names]

    def read(self, ref: str) -> bytes:  # pragma: no cover
        source_id, name = self._split(ref)
        path = (self._folder(source_id) / name).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("fixture ref escapes the fixtures directory")
        if not path.is_file():
            raise FileNotFoundError(f"fixture not found: {ref}")
        return path.read_bytes()

    def _folder(self, source_id: str) -> Path:  # pragma: no cover
        if not _SAFE_ID.fullmatch(source_id):
            raise ValueError(f"invalid fixture source id: {source_id!r}")
        return self.root / source_id

    @staticmethod
    def _split(ref: str) -> tuple[str, str]:  # pragma: no cover
        if not ref.startswith(_FIXTURE_SCHEME):
            raise ValueError(f"not a fixture ref: {ref}")
        source_id, _, name = ref[len(_FIXTURE_SCHEME) :].partition("/")
        if not _SAFE_ID.fullmatch(source_id) or not _SAFE_NAME.fullmatch(name):
            raise ValueError(f"invalid fixture ref: {ref}")
        return source_id, name


class _QuoteBuffer:
    def __init__(
        self, start: int, dom_path: str, attrs: dict[str, str]
    ) -> None:  # pragma: no cover
        self.start = start
        self.dom_path = dom_path
        self.parts: list[str] = []
        self.speaker = attrs.get("data-speaker", "")
        self.role = attrs.get("data-role", "")
        self.section_id = attrs.get("data-section", "")


class _FixtureHTMLParser(HTMLParser):
    """Small stdlib parser that keeps text offsets and quoted DOM spans."""

    def __init__(self) -> None:  # pragma: no cover
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.length = 0
        self.meta: dict[str, str] = {}
        self.canonical_url = ""
        self.title = ""
        self.byline = ""
        self.publication_name = ""
        self.quotes: list[dict[str, Any]] = []
        self._buffers: list[_QuoteBuffer] = []
        self._capture: str | None = None
        self._captured: list[str] = []
        self._paragraphs = 0

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:  # pragma: no cover
        values = {key: value or "" for key, value in attrs}
        if tag == "link" and "canonical" in values.get("rel", ""):
            self.canonical_url = values.get("href", "")
        elif tag == "meta":
            key = values.get("name") or values.get("property") or ""
            if key:
                self.meta[key.lower()] = values.get("content", "")
        if tag == "title":
            self._capture = "title"
            self._captured = []
        elif tag == "h1":
            self._capture = "h1"
            self._captured = []
            self._newline()
        elif tag == "p":
            self._paragraphs += 1
            self._newline()
        elif tag == "div":
            self._newline()
        if tag == "span" and "quote" in values.get("class", "").split():
            path = f"html/body/article/p[{self._paragraphs}]/span[{len(self.quotes) + 1}]"
            self._buffers.append(_QuoteBuffer(self.length, path, values))

    def handle_data(self, data: str) -> None:  # pragma: no cover
        self._append(data)
        for buffer in self._buffers:
            buffer.parts.append(data)
        if self._capture:
            self._captured.append(data)

    def handle_endtag(self, tag: str) -> None:  # pragma: no cover
        if (self._capture == "title" and tag == "title") or (self._capture == "h1" and tag == "h1"):
            self.title = "".join(self._captured).strip()
            self._capture = None
        if tag in {"p", "h1", "div"}:
            self._newline()
        if tag == "span" and self._buffers:
            self._finish_quote(self._buffers.pop())

    def _append(self, data: str) -> None:  # pragma: no cover
        self.parts.append(data)
        self.length += len(data)

    def _newline(self) -> None:  # pragma: no cover
        if self.parts and not self.parts[-1].endswith("\n"):
            self._append("\n")

    def _finish_quote(self, buffer: _QuoteBuffer) -> None:  # pragma: no cover
        span_text = "".join(buffer.parts)
        for match in _QUOTE_RE.finditer(span_text):
            suffix = span_text[match.end() : match.end() + 160]
            prefix = span_text[max(0, match.start() - 160) : match.start()]
            attribution = _ATTRIBUTION_RE.search(suffix) or _ATTRIBUTION_RE.search(prefix)
            speaker = attribution.group(1) if attribution else buffer.speaker
            self.quotes.append(
                {
                    "quote_text": match.group(1),
                    "char_start": buffer.start + match.start(1),
                    "char_end": buffer.start + match.end(1),
                    "dom_path": buffer.dom_path,
                    "speaker": speaker,
                    "role": buffer.role,
                    "attribution_span": (attribution.group(0) if attribution else buffer.role),
                    "section_id": buffer.section_id or infer_section(match.group(1)),
                }
            )

    def metadata(self) -> dict[str, Any]:  # pragma: no cover
        published = (
            self.meta.get("dc.date")
            or self.meta.get("article:published_time", "")[:10]
            or self.meta.get("date")
            or self.meta.get("pubdate")
            or ""
        )
        byline = (
            self.meta.get("author")
            or self.meta.get("article:author")
            or self.meta.get("byline")
            or ""
        )
        return {
            "canonical_url": self.canonical_url,
            "publication_date": published,
            "title": self.title,
            "byline": byline,
            "publication_name": self.meta.get("publicationname")
            or self.meta.get("og:site_name")
            or "",
            "article_text": "".join(self.parts),
            "quote_spans": self.quotes,
        }


def infer_section(text: str) -> str:  # pragma: no cover
    """Deterministic fallback section assignment for unlabelled fixture quotes."""
    lowered = text.lower()
    for section_id, keywords in _SECTION_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return section_id
    return "other"


class SourceAdapter:
    """Base adapter: T0 gate in live mode, labelled fixture replay in degraded mode."""

    discovery_method = "web"

    def __init__(  # pragma: no cover
        self,
        row: SourceRegistryRow,
        fixture_source: FixtureSource | None = None,
        *,
        replay: bool = False,
    ) -> None:
        self.row = row
        self.fixture_source = fixture_source or FixtureSource()
        self.replay = replay

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
            metadata = self._parse(raw)
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

    def parse_metadata(self, raw: bytes) -> dict[str, Any]:  # pragma: no cover
        self._require_permission()
        return self._parse(raw)

    def extract_quotes(self, text: str) -> list[dict[str, Any]]:  # pragma: no cover
        self._require_permission()
        return _extract_quotes(text)

    def policy_flags(self) -> dict[str, Any]:  # pragma: no cover
        return self.row.policy_flags

    @staticmethod
    def _parse(raw: bytes) -> dict[str, Any]:  # pragma: no cover
        decoded = raw.decode("utf-8")
        if decoded.lstrip().startswith(("{", "[")):
            return _parse_json_fixture(decoded)
        parser = _FixtureHTMLParser()
        parser.feed(decoded)
        parser.close()
        return parser.metadata()


def _parse_json_fixture(decoded: str) -> dict[str, Any]:  # pragma: no cover
    payload = json.loads(decoded)
    if not isinstance(payload, dict):
        raise ValueError("JSON fixture must be an object")
    text = str(payload.get("article_text", ""))
    raw_spans = payload.get("quote_spans", [])
    if not isinstance(raw_spans, list):
        raise ValueError("JSON fixture quote_spans must be a list")
    spans: list[dict[str, Any]] = [span for span in raw_spans if isinstance(span, dict)]
    return {
        "canonical_url": str(payload.get("canonical_url", "")),
        "publication_date": str(payload.get("publication_date", "")),
        "title": str(payload.get("title", "")),
        "byline": str(payload.get("byline", "")),
        "publication_name": str(payload.get("publication_name", "")),
        "article_text": text,
        "quote_spans": spans,
    }


def _extract_quotes(text: str) -> list[dict[str, Any]]:  # pragma: no cover
    """Find attributed, quoted spans in article text with offsets into that text."""
    found: list[dict[str, Any]] = []
    for index, match in enumerate(_QUOTE_RE.finditer(text), start=1):
        quote_text = match.group(1)
        if not normalise_text(quote_text):
            continue
        suffix = text[match.end() : match.end() + 160]
        prefix = text[max(0, match.start() - 160) : match.start()]
        attribution = _ATTRIBUTION_RE.search(suffix) or _ATTRIBUTION_RE.search(prefix)
        found.append(
            {
                "quote_text": quote_text,
                "char_start": match.start(1),
                "char_end": match.end(1),
                "dom_path": f"text://span[{index}]",
                "speaker": attribution.group(1) if attribution else None,
                "attribution_span": attribution.group(0) if attribution else "",
                "section_id": infer_section(quote_text),
            }
        )
    return found


class SitemapAdapter(SourceAdapter):
    discovery_method = "sitemap"


class RssAdapter(SourceAdapter):
    discovery_method = "rss"


class ApiAdapter(SourceAdapter):
    discovery_method = "api"


class GdeltAdapter(SourceAdapter):
    discovery_method = "gdelt"


class LicensedDatabaseAdapter(SourceAdapter):
    discovery_method = "licensed_database"


class WebAdapter(SourceAdapter):
    discovery_method = "web"


_ADAPTERS = {
    "sitemap": SitemapAdapter,
    "rss": RssAdapter,
    "api": ApiAdapter,
    "gdelt": GdeltAdapter,
    "licensed_database": LicensedDatabaseAdapter,
    "web": WebAdapter,
}


def build_adapter(  # pragma: no cover
    row: SourceRegistryRow,
    fixture_source: FixtureSource | None = None,
    *,
    replay: bool = False,
) -> SourceAdapter:
    if row.discovery_method not in ENUMERABLE_DISCOVERY:
        raise ValueError(f"non-enumerable discovery method: {row.discovery_method}")
    adapter_class = _ADAPTERS.get(row.discovery_method, SourceAdapter)
    return adapter_class(row, fixture_source, replay=replay)
