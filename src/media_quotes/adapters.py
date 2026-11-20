"""Deterministic, offline and rights-gated source adapters.

Supports RSS/Sitemap discovery and Trafilatura extraction.

No adapter opens a live socket by default. In live mode every method first applies the T0
gate; in ``replay=True`` mode the adapter reads clearly labelled synthetic fixtures from
``data/fixtures`` and the caller must log that degraded mode explicitly.

In addition to synthetic fixture replay, this module provides:
- ``parse_rss_feed`` / ``RssAdapter.discover_from_xml``: RSS 2.0 & Atom XML feed discovery.
- ``parse_sitemap_xml`` / ``SitemapAdapter.discover_from_xml``: XML Sitemap and
  Google News Sitemap discovery.
- ``parse_newspaper_html``: Real-world newspaper HTML parser combining JSON-LD ``NewsArticle``
  metadata, OpenGraph/DC meta tags, and Trafilatura 2.x body extraction with quality gating.
- Multi-format quote & speaker extraction supporting straight quotes (``"..."``), curly quotes
  (``“...”``), guillemets (``«...»``, ``„...“``), verb-first attribution (``"said Speaker"``),
  speaker-first attribution (``"Speaker said"``), and multilingual (Ukrainian/English) verbs.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .config import get_settings
from .enums import ENUMERABLE_DISCOVERY
from .exceptions import SourceNotPermitted
from .extraction import ExtractionParams, RenderingFallback, extract_article
from .registry import SourceRegistryRow
from .text_utils import normalise_text

__all__ = [
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
    "parse_newspaper_html",
    "parse_rss_feed",
    "parse_sitemap_xml",
]

_FIXTURE_SCHEME = "fixture://"
_SAFE_ID = re.compile(r"^[a-z0-9_-]+$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")

# Match curly quotes (“...”), straight quotes ("..."), or guillemets («...» / „...“)
_QUOTE_RE = re.compile(
    r"(?:[\u201c\"]([^\u201d\"]{20,4000})[\u201d\"]"
    r"|[\u00ab]([^\u00bb]{20,4000})[\u00bb]"
    r"|[\u201e]([^\u201c\u201d]{20,4000})[\u201c\u201d])"
)

_ATTRIBUTION_VERBS = (
    r"said|says|added|told|stated|noted|warned|announced|stressed|explained|declared"
    r"|according to|reported|заявив|заявила|сказав|сказала|зазначив|зазначила|повідомив|наголосив"
)
_NAME_TOKEN = (
    r"[A-Z\u0410-\u042f\u0404\u0406\u0407\u0490]"  # noqa: S105 - name regex
    r"[A-Za-z\u0410-\u044f\u0404\u0454\u0406\u0456\u0407\u0457\u0490\u0491'-]+"
)
_SPEAKER_NAME = rf"({_NAME_TOKEN}(?:\s+{_NAME_TOKEN}){{0,3}})"

# 1) Verb-first: "said Volodymyr Zelenskyy", "according to Minister Denys Shmyhal",
# "— Spokesperson".
_ATTRIBUTION_VERB_FIRST_RE = re.compile(rf"(?:{_ATTRIBUTION_VERBS}|—|-)\s+{_SPEAKER_NAME}")

# 2) Speaker-first: "Volodymyr Zelenskyy said", "President Biden told", "Dmytro Kuleba stated"
_ATTRIBUTION_SPEAKER_FIRST_RE = re.compile(rf"{_SPEAKER_NAME}\s+(?:{_ATTRIBUTION_VERBS})\b")

_JSON_LD_RE = re.compile(
    r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)

_SECTION_KEYWORDS = {
    "war_intensity": (
        "offensive",
        "shelling",
        "fighting",
        "front line",
        "casualt",
        "strike",
        "drone",
        "missile",
    ),
    "international_support": (
        "support",
        "air defence",
        "partners",
        "aid",
        "assistance",
        "coalition",
        "package",
    ),
    "peace_initiatives": (
        "ceasefire",
        "negotiation",
        "talks",
        "peace",
        "halt in strikes",
        "diploma",
        "summit",
    ),
    "sanctions": ("sanction", "restriction", "enforcement", "dual-use", "embargo", "asset freeze"),
    "resources": (
        "ammunition",
        "production",
        "personnel",
        "supplies",
        "budget",
        "mobilisation",
        "defense industry",
    ),
}


class BlobCache:
    """Content-addressed local disk cache for scraped articles.

    Stores files under ``var/blobs/<source_id>/<sha256>.html``.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or get_settings().blob_dir).resolve()

    def store(self, source_id: str, raw: bytes, *, suffix: str = ".html") -> tuple[str, Path]:
        if not _SAFE_ID.fullmatch(source_id):
            raise ValueError(f"invalid source id for blob cache: {source_id!r}")
        digest = hashlib.sha256(raw).hexdigest()
        ext = suffix if suffix.startswith(".") else f".{suffix}"
        folder = (self.root / source_id).resolve()
        if not folder.is_relative_to(self.root):
            raise ValueError("blob folder escapes root")
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{digest}{ext}"
        if not target.exists():
            target.write_bytes(raw)
        return digest, target

    def read(self, source_id: str, digest: str, *, suffix: str = ".html") -> bytes:
        if not _SAFE_ID.fullmatch(source_id) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid source_id or sha256 digest")
        ext = suffix if suffix.startswith(".") else f".{suffix}"
        target = (self.root / source_id / f"{digest}{ext}").resolve()
        if not target.is_relative_to(self.root) or not target.is_file():
            raise FileNotFoundError(f"blob not found: {source_id}/{digest}{ext}")
        return target.read_bytes()


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


def _match_quote_group(match: re.Match[str]) -> tuple[str, int, int]:
    """Return (inner_quote_text, start_offset, end_offset) for whichever quote delimiter matched."""
    for group_idx in (1, 2, 3):
        val = match.group(group_idx)
        if val is not None:
            return val, match.start(group_idx), match.end(group_idx)
    return "", match.start(), match.end()


def _find_attribution(prefix: str, suffix: str) -> tuple[str | None, str]:
    """Search suffix then prefix for verb-first or speaker-first attribution."""
    for window in (suffix, prefix):
        match = _ATTRIBUTION_VERB_FIRST_RE.search(window) or _ATTRIBUTION_SPEAKER_FIRST_RE.search(
            window
        )
        if match:
            return match.group(1), match.group(0)
    return None, ""


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
            quote_text, q_start, q_end = _match_quote_group(match)
            suffix = span_text[match.end() : match.end() + 160]
            prefix = span_text[max(0, match.start() - 160) : match.start()]
            attr_speaker, attr_span = _find_attribution(prefix, suffix)
            speaker = attr_speaker or buffer.speaker
            self.quotes.append(
                {
                    "quote_text": quote_text,
                    "char_start": buffer.start + q_start,
                    "char_end": buffer.start + q_end,
                    "dom_path": buffer.dom_path,
                    "speaker": speaker,
                    "role": buffer.role,
                    "attribution_span": attr_span or buffer.role,
                    "section_id": buffer.section_id or infer_section(quote_text),
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
            "canonical_url": self.canonical_url or self.meta.get("og:url", ""),
            "publication_date": published,
            "title": self.title or self.meta.get("og:title", ""),
            "byline": byline,
            "publication_name": self.meta.get("publicationname")
            or self.meta.get("og:site_name")
            or "",
            "article_text": "".join(self.parts),
            "quote_spans": self.quotes,
            "extraction_method": "fixture_html_v1",
            "extractor_version": "fixture-replay-v1",
        }


def extract_json_ld_metadata(raw_html: str) -> dict[str, str]:
    """Extract NewsArticle/Article metadata from `<script type="application/ld+json">` blocks."""
    result: dict[str, str] = {}
    for match in _JSON_LD_RE.finditer(raw_html):
        block = match.group(1).strip()
        if not block:
            continue
        with contextlib.suppress(Exception):
            parsed = json.loads(block)
            items = parsed if isinstance(parsed, list) else [parsed]
            for item in list(items):
                if isinstance(item, dict) and "@graph" in item and isinstance(item["@graph"], list):
                    items.extend(item["@graph"])
            for item in items:
                if not isinstance(item, dict):
                    continue
                schema_type = item.get("@type", "")
                types = [schema_type] if isinstance(schema_type, str) else list(schema_type)
                if not any("Article" in str(t) or "News" in str(t) for t in types):
                    continue
                if item.get("headline") and not result.get("title"):
                    result["title"] = str(item["headline"]).strip()
                if item.get("datePublished") and not result.get("publication_date"):
                    result["publication_date"] = str(item["datePublished"])[:10]
                if item.get("url") and not result.get("canonical_url"):
                    result["canonical_url"] = str(item["url"]).strip()
                main_entity = item.get("mainEntityOfPage")
                if (
                    isinstance(main_entity, dict)
                    and main_entity.get("@id")
                    and not result.get("canonical_url")
                ):
                    result["canonical_url"] = str(main_entity["@id"]).strip()
                author = item.get("author")
                if author and not result.get("byline"):
                    if isinstance(author, dict) and author.get("name"):
                        result["byline"] = str(author["name"]).strip()
                    elif isinstance(author, list):
                        names = [
                            str(a["name"]).strip()
                            for a in author
                            if isinstance(a, dict) and a.get("name")
                        ]
                        if names:
                            result["byline"] = ", ".join(names)
                publisher = item.get("publisher")
                if (
                    isinstance(publisher, dict)
                    and publisher.get("name")
                    and not result.get("publication_name")
                ):
                    result["publication_name"] = str(publisher["name"]).strip()
    return result


def parse_newspaper_html(
    raw_html: str,
    *,
    source_url: str = "",
    params: ExtractionParams | None = None,
    fallback: RenderingFallback | None = None,
) -> dict[str, Any]:
    """Parse real-world newspaper HTML using JSON-LD + meta tags + Trafilatura 2.x body extraction.

    Preserves exact offsets into the extracted clean article body and runs `quality_gate()`
    to ensure ads, cookie notices, and boilerplate are stripped before quote extraction.
    """
    extraction_params = params or ExtractionParams()
    dom_parser = _FixtureHTMLParser()
    dom_parser.feed(raw_html)
    dom_parser.close()
    dom_meta = dom_parser.metadata()
    ld_meta = extract_json_ld_metadata(raw_html)

    extracted = extract_article(
        raw_html,
        params=extraction_params,
        source_url=source_url
        or ld_meta.get("canonical_url")
        or dom_meta.get("canonical_url")
        or "https://example.invalid",
        fallback=fallback,
    )

    article_text = (
        extracted.text if extracted.text.strip() else str(dom_meta.get("article_text") or "")
    )
    canonical_url = (
        ld_meta.get("canonical_url")
        or dom_meta.get("canonical_url")
        or str(extracted.metadata.get("canonical_url") or "")
        or source_url
    )
    publication_date = (
        ld_meta.get("publication_date")
        or dom_meta.get("publication_date")
        or str(extracted.metadata.get("publication_date") or "")[:10]
    )
    title = (
        ld_meta.get("title")
        or str(extracted.metadata.get("title") or "")
        or dom_meta.get("title")
        or ""
    )
    byline = (
        ld_meta.get("byline")
        or dom_meta.get("byline")
        or str(extracted.metadata.get("author") or "")
    )
    publication_name = (
        ld_meta.get("publication_name")
        or dom_meta.get("publication_name")
        or str(extracted.metadata.get("publication_name") or "")
    )
    quote_spans = (
        dom_meta["quote_spans"] if dom_meta.get("quote_spans") else _extract_quotes(article_text)
    )

    return {
        "canonical_url": canonical_url,
        "publication_date": publication_date[:10] if publication_date else "",
        "title": title,
        "byline": byline,
        "publication_name": publication_name,
        "article_text": article_text,
        "quote_spans": quote_spans,
        "quality_ok": extracted.quality_ok,
        "quality_reasons": list(extracted.quality_reasons),
        "used_fallback": extracted.used_fallback,
        "extraction_method": "trafilatura_dom_v2",
        "extractor_version": extracted.extractor_version,
    }


def _parse_feed_date(raw_date: str) -> date | None:
    """Parse RFC-2822 (RSS `<pubDate>`) or ISO-8601 (Atom/Sitemap) date strings into `date`."""
    cleaned = raw_date.strip()
    if not cleaned:
        return None
    with contextlib.suppress(Exception):
        return date.fromisoformat(cleaned[:10])
    with contextlib.suppress(Exception):
        return parsedate_to_datetime(cleaned).date()
    with contextlib.suppress(Exception):
        return datetime.fromisoformat(cleaned.replace("Z", "+00:00")).date()
    return None


def _local_tag(tag: str) -> str:
    """Strip XML namespace `{http://...}name` -> `name`."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def parse_rss_feed(
    raw_xml: bytes | str,
    *,
    start: date | None = None,
    end: date | None = None,
) -> list[dict[str, str]]:
    """Parse RSS 2.0 (`<item>`) or Atom (`<entry>`) XML and return discovered article entries.

    Each entry contains `url`, `title`, and `publication_date` (ISO `YYYY-MM-DD`).
    When `start` / `end` are provided, only articles within `[start, end]` are returned.
    """
    xml_text = raw_xml.decode("utf-8") if isinstance(raw_xml, bytes) else raw_xml
    root = ET.fromstring(xml_text)  # noqa: S314
    items: list[dict[str, str]] = []

    for elem in root.iter():
        tag = _local_tag(elem.tag).lower()
        if tag not in {"item", "entry"}:
            continue
        url = ""
        title = ""
        pub_raw = ""
        for child in elem:
            ctag = _local_tag(child.tag).lower()
            if ctag == "link":
                href = child.attrib.get("href", "").strip()
                rel = child.attrib.get("rel", "alternate").strip()
                text_url = (child.text or "").strip()
                if href and (rel in {"alternate", ""} or not url):
                    url = href
                elif text_url and not url:
                    url = text_url
            elif ctag == "title" and not title:
                title = (child.text or "").strip()
            elif ctag in {"pubdate", "published", "updated", "date"} and not pub_raw:
                pub_raw = (child.text or "").strip()
        if not url:
            continue
        pub_date = _parse_feed_date(pub_raw)
        if start and pub_date and pub_date < start:
            continue
        if end and pub_date and pub_date > end:
            continue
        items.append(
            {
                "url": url,
                "title": title,
                "publication_date": pub_date.isoformat() if pub_date else "",
            }
        )
    return items


def parse_sitemap_xml(
    raw_xml: bytes | str,
    *,
    start: date | None = None,
    end: date | None = None,
) -> list[dict[str, str]]:
    """Parse standard XML Sitemap (`<urlset>`) and Google News Sitemap (`<news:news>`).

    Extracts `url` (`<loc>`), `publication_date` (`<news:publication_date>` or `<lastmod>`),
    and optional `title` (`<news:title>`). Filters by `[start, end]` when dates are present.
    """
    xml_text = raw_xml.decode("utf-8") if isinstance(raw_xml, bytes) else raw_xml
    root = ET.fromstring(xml_text)  # noqa: S314
    entries: list[dict[str, str]] = []

    for elem in root.iter():
        if _local_tag(elem.tag).lower() != "url":
            continue
        loc = ""
        lastmod = ""
        news_pub = ""
        news_title = ""
        for desc in elem.iter():
            dtag = _local_tag(desc.tag).lower()
            if dtag == "loc" and not loc:
                loc = (desc.text or "").strip()
            elif dtag == "lastmod" and not lastmod:
                lastmod = (desc.text or "").strip()
            elif dtag == "publication_date" and not news_pub:
                news_pub = (desc.text or "").strip()
            elif dtag == "title" and not news_title:
                news_title = (desc.text or "").strip()
        if not loc:
            continue
        pub_date = _parse_feed_date(news_pub or lastmod)
        if start and pub_date and pub_date < start:
            continue
        if end and pub_date and pub_date > end:
            continue
        entries.append(
            {
                "url": loc,
                "title": news_title,
                "publication_date": pub_date.isoformat() if pub_date else "",
            }
        )
    return entries


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

    def parse_metadata(
        self, raw: bytes, *, use_trafilatura: bool = False, source_url: str = ""
    ) -> dict[str, Any]:  # pragma: no cover
        self._require_permission()
        return self._parse(raw, use_trafilatura=use_trafilatura, source_url=source_url)

    def extract_quotes(self, text: str) -> list[dict[str, Any]]:  # pragma: no cover
        self._require_permission()
        return _extract_quotes(text)

    def policy_flags(self) -> dict[str, Any]:  # pragma: no cover
        return self.row.policy_flags

    @staticmethod
    def _parse(
        raw: bytes, *, use_trafilatura: bool = False, source_url: str = ""
    ) -> dict[str, Any]:  # pragma: no cover
        decoded = raw.decode("utf-8")
        if decoded.lstrip().startswith(("{", "[")):
            return _parse_json_fixture(decoded)
        # If the HTML has no synthetic <span class="quote"> tags, use the real parser.
        if 'class="quote"' not in decoded and (use_trafilatura or "<article" in decoded.lower()):
            return parse_newspaper_html(decoded, source_url=source_url)
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
        "extraction_method": "fixture_json_v1",
        "extractor_version": "fixture-replay-v1",
    }


def _extract_quotes(text: str) -> list[dict[str, Any]]:  # pragma: no cover
    """Find attributed, quoted spans in article text with exact offsets into that text."""
    found: list[dict[str, Any]] = []
    for index, match in enumerate(_QUOTE_RE.finditer(text), start=1):
        quote_text, q_start, q_end = _match_quote_group(match)
        if not normalise_text(quote_text):
            continue
        suffix = text[match.end() : match.end() + 160]
        prefix = text[max(0, match.start() - 160) : match.start()]
        speaker, attr_span = _find_attribution(prefix, suffix)
        found.append(
            {
                "quote_text": quote_text,
                "char_start": q_start,
                "char_end": q_end,
                "dom_path": f"text://span[{index}]",
                "speaker": speaker,
                "attribution_span": attr_span,
                "section_id": infer_section(quote_text),
            }
        )
    return found


class SitemapAdapter(SourceAdapter):
    discovery_method = "sitemap"

    def discover_from_xml(
        self, raw_xml: bytes | str, start: date, end: date
    ) -> list[dict[str, str]]:
        """Discover article URLs from an XML Sitemap within ``[start, end]``."""
        self._require_permission()
        return parse_sitemap_xml(raw_xml, start=start, end=end)


class RssAdapter(SourceAdapter):
    discovery_method = "rss"

    def discover_from_xml(
        self, raw_xml: bytes | str, start: date, end: date
    ) -> list[dict[str, str]]:
        """Discover article URLs from an RSS 2.0 or Atom XML feed within `[start, end]`."""
        self._require_permission()
        return parse_rss_feed(raw_xml, start=start, end=end)


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
