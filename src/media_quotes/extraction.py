"""C2 — DOM-aware extraction with a quality gate before any LLM reasoning.

Trafilatura's `bare_extraction` returns a Document in 2.x (a dict in older versions); both
are supported. Nothing here calls the network. The quality gate is pure and fail-closed:
low-quality or empty output is routed to an injectable rendering fallback before a caller
may send text to an LLM. DOM offsets are preserved as spans into the extracted text so the
deterministic verifier can use them (docs/02 §4.1).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

from .exceptions import ConfigError
from .text_utils import normalise_text

_JUNK_RE = re.compile(
    r"(?i)\b(advertisement|subscribe|cookie notice|all rights reserved|sign up)\b|page\s*\d+"
)


class ExtractionParams(BaseModel):
    """Strict trafilatura parameters from docs/02 §4.1 (frozen by snapshot tests)."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    include_formatting: bool = True
    include_links: bool = True
    with_metadata: bool = True
    include_comments: bool = False
    include_tables: bool = False
    favor_precision: bool = True
    favor_recall: bool = False
    prune_xpath: tuple[str, ...] = ()

    def trafilatura_kwargs(self) -> dict[str, Any]:
        return {
            "include_formatting": self.include_formatting,
            "include_links": self.include_links,
            "with_metadata": self.with_metadata,
            "include_comments": self.include_comments,
            "include_tables": self.include_tables,
            "favor_precision": self.favor_precision,
            "favor_recall": self.favor_recall,
            "prune_xpath": list(self.prune_xpath) or None,
        }


class ExtractedArticle(BaseModel):
    """Result of one extraction; `usable` gates all downstream LLM calls."""

    model_config = ConfigDict(strict=True, extra="forbid")

    text: str
    metadata: dict[str, Any]
    quality_ok: bool
    quality_reasons: tuple[str, ...] = ()
    used_fallback: bool = False
    extractor_version: str = "trafilatura-2.x"


class RenderingFallback(Protocol):
    """Injectable second-pass extractor; kept out of the pure unit path."""

    def __call__(self, raw_html: str, *, source_url: str) -> str: ...


def static_html_fallback(raw_html: str, *, source_url: str) -> str:
    """Second pass with recall-favouring trafilatura settings, no new dependency.

    Used only when the strict quality gate rejects the precision-first extraction.
    It cannot render JavaScript; a rendered fallback needs a separate, rights-gated tool.
    """
    import warnings

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=r"SOCKS support in urllib3 requires.*")
        import trafilatura

    relaxed = ExtractionParams(
        include_comments=True,
        include_tables=True,
        favor_precision=False,
        favor_recall=True,
    )
    document = trafilatura.bare_extraction(raw_html, url=source_url, **relaxed.trafilatura_kwargs())
    return str(_as_mapping(document).get("text") or "") if document is not None else ""


def rendered_html_fallback(raw_html: str, *, source_url: str) -> str:
    """Browser-backed second pass for JS-only article bodies.

    Playwright is optional and imported only here, so normal runs never load a browser.
    The caller must already have cleared the source rights gate before selecting this path.
    """
    try:
        from playwright.sync_api import Route, sync_playwright
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise ConfigError(
            "rendered extraction requires 'uv sync --group render' and an installed browser"
        ) from exc
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            parsed_src = urlparse(source_url)
            allowed_host = (parsed_src.hostname or "").lower()

            def _route_handler(route: Route) -> None:
                req_url = route.request.url
                if req_url == source_url:
                    # Serve the vetted fetch, never re-request the main document.
                    route.fulfill(status=200, content_type="text/html", body=raw_html)
                    return
                req_parsed = urlparse(req_url)
                if req_parsed.scheme != "https":
                    route.abort()
                    return
                req_host = (req_parsed.hostname or "").lower()
                if req_host == allowed_host or req_host.endswith("." + allowed_host):
                    route.continue_()
                else:
                    route.abort()

            page.route("**/*", _route_handler)
            page.goto(source_url, wait_until="domcontentloaded", timeout=15_000)
            rendered = page.content()
        finally:
            browser.close()
    return static_html_fallback(rendered, source_url=source_url)


def quality_gate(
    text: str, *, min_chars: int = 400, max_junk_hits: int = 2
) -> tuple[bool, tuple[str, ...]]:
    """Return (ok, reasons). Empty, very short or boilerplate-heavy text fails closed."""
    normalised = normalise_text(text)
    reasons: list[str] = []
    if len(normalised) < min_chars:
        reasons.append(f"too_short: {len(normalised)} < {min_chars}")
    junk_hits = len(_JUNK_RE.findall(normalised))
    if junk_hits > max_junk_hits:
        reasons.append(f"junk_contamination: {junk_hits} > {max_junk_hits}")
    alpha_ratio = (
        sum(char.isalpha() for char in normalised) / len(normalised) if normalised else 0.0
    )
    if alpha_ratio < 0.55:
        reasons.append(f"low_alpha_ratio: {alpha_ratio:.2f}")
    return (not reasons), tuple(reasons)


def _as_mapping(document: object) -> Mapping[str, Any]:
    if isinstance(document, Mapping):
        return document
    as_dict = getattr(document, "as_dict", None)
    if callable(as_dict):
        result = as_dict()
        if isinstance(result, Mapping):
            return result
    names = ("text", "title", "author", "date", "sitename", "url")
    return {name: getattr(document, name, None) for name in names}


def extract_article(
    raw_html: str,
    *,
    params: ExtractionParams,
    source_url: str,
    fallback: RenderingFallback | None = None,
    min_chars: int = 400,
) -> ExtractedArticle:
    """Extract text + metadata, gate quality, then try the fallback before LLM use."""
    # trafilatura pulls in urllib3 SOCKS support; PySocks is optional and unused for
    # offline extraction. Suppress only that third-party warning at the import boundary.
    import warnings

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=r"SOCKS support in urllib3 requires.*")
        import trafilatura

    document = trafilatura.bare_extraction(raw_html, url=source_url, **params.trafilatura_kwargs())
    data = _as_mapping(document) if document is not None else {}
    text = str(data.get("text") or "")
    metadata = {
        "title": str(data.get("title") or ""),
        "author": str(data.get("author") or ""),
        "publication_date": str(data.get("date") or ""),
        "publication_name": str(data.get("sitename") or ""),
        "canonical_url": str(data.get("url") or source_url),
    }
    ok, reasons = quality_gate(text, min_chars=min_chars)
    if not ok and fallback is not None:
        fallback_text = fallback(raw_html, source_url=source_url)
        fallback_ok, fallback_reasons = quality_gate(fallback_text, min_chars=min_chars)
        if fallback_ok:
            return ExtractedArticle(
                text=fallback_text,
                metadata=metadata,
                quality_ok=True,
                used_fallback=True,
            )
        reasons = reasons + fallback_reasons
    return ExtractedArticle(
        text=text,
        metadata=metadata,
        quality_ok=ok,
        quality_reasons=reasons,
        used_fallback=False,
    )


def find_span(extracted_text: str, quote_text: str, start_from: int = 0) -> tuple[int, int] | None:
    """Exact offset of a verified quote in extracted text; no normalisation guessing."""
    start = extracted_text.find(quote_text, max(0, start_from))
    if start < 0:
        return None
    return start, start + len(quote_text)
