"""Newspaper HTML + JSON-LD + Trafilatura 2.x body extractor and fixture parser."""

from __future__ import annotations

import contextlib
import json
import re
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any

from ..extraction import ExtractionParams, RenderingFallback, extract_article, find_span
from .quote_extractor import (
    _QUOTE_RE,
    _extract_quotes,
    _find_attribution,
    _match_quote_group,
    infer_section,
    is_topic_relevant,
)

if TYPE_CHECKING:
    from ..source_configs import ExtractorHints
    from ..topics import TopicPack

__all__ = ["extract_json_ld_metadata", "parse_newspaper_html"]

_JSON_LD_RE = re.compile(
    r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)


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

    def __init__(self, *, topic_pack: TopicPack | None = None) -> None:  # pragma: no cover
        super().__init__(convert_charrefs=True)
        self.topic_pack = topic_pack
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
                    "section_id": buffer.section_id
                    or infer_section(quote_text, topic_pack=self.topic_pack),
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
    """Extract NewsArticle/Article metadata from ``<script type="application/ld+json">`` blocks."""
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


def _params_from_hints(hints: ExtractorHints | None) -> ExtractionParams:
    if hints is None:
        return ExtractionParams()
    return ExtractionParams(
        include_comments=hints.include_comments,
        include_tables=hints.include_tables,
        favor_precision=hints.favor_precision,
        favor_recall=hints.favor_recall,
    )


def parse_newspaper_html(
    raw_html: str,
    *,
    source_url: str = "",
    params: ExtractionParams | None = None,
    fallback: RenderingFallback | None = None,
    require_ukraine_topic: bool = False,
    topic_pack: TopicPack | None = None,
    require_topic: bool = False,
    extractor_hints: ExtractorHints | None = None,
) -> dict[str, Any]:
    """Parse real-world newspaper HTML using JSON-LD + meta tags + Trafilatura 2.x body extraction.

    Preserves exact offsets into the extracted clean article body and runs ``quality_gate()``
    to ensure ads, cookie notices, and boilerplate are stripped before quote extraction.
    """
    enforce_topic = require_topic or require_ukraine_topic
    extraction_params = params or _params_from_hints(extractor_hints)
    dom_parser = _FixtureHTMLParser(topic_pack=topic_pack)
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
    topic_relevant = is_topic_relevant(
        article_text, topic_pack=topic_pack, title=title, url=canonical_url
    )
    quote_spans: list[dict[str, Any]] = []
    if not extracted.quality_ok:
        # A low-quality body is reporting only: never publish quotes from it.
        pass
    elif dom_meta.get("quote_spans"):
        for span in dom_meta["quote_spans"]:
            realigned = dict(span)
            found_offsets = find_span(article_text, str(span.get("quote_text") or ""))
            if found_offsets is not None:
                realigned["char_start"], realigned["char_end"] = found_offsets
            if (
                enforce_topic
                and not topic_relevant
                and not is_topic_relevant(str(span.get("quote_text") or ""), topic_pack=topic_pack)
            ):
                continue
            quote_spans.append(realigned)
    else:
        if enforce_topic and not topic_relevant:
            quote_spans = []
        else:
            quote_spans = _extract_quotes(
                article_text,
                require_topic=enforce_topic,
                topic_pack=topic_pack,
            )

    return {
        "canonical_url": canonical_url,
        "publication_date": publication_date[:10] if publication_date else "",
        "title": title,
        "byline": byline,
        "publication_name": publication_name,
        "article_text": article_text,
        "quote_spans": quote_spans,
        "topic_relevant": topic_relevant,
        "quality_ok": extracted.quality_ok,
        "quality_reasons": list(extracted.quality_reasons),
        "used_fallback": extracted.used_fallback,
        "extraction_method": "trafilatura_dom_v2",
        "extractor_version": extracted.extractor_version,
    }


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
