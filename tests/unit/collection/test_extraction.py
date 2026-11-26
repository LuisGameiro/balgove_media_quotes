"""C2 — trafilatura extraction, quality gate and fallback routing (offline)."""

import sys
import types
from pathlib import Path

import pytest
from pydantic import ValidationError

import media_quotes.processing.extraction as extraction
from media_quotes.exceptions import ConfigError
from media_quotes.processing.extraction import (
    ExtractionParams,
    extract_article,
    find_span,
    quality_gate,
    rendered_html_fallback,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "data/fixtures/reuters/synthetic-reuters-2026-11-18.html"


def test_params_match_plan_and_are_frozen() -> None:
    p = ExtractionParams()
    kwargs = p.trafilatura_kwargs()
    assert kwargs["include_comments"] is False
    assert kwargs["include_tables"] is False
    assert kwargs["favor_precision"] is True
    assert kwargs["favor_recall"] is False
    assert kwargs["prune_xpath"] is None
    with pytest.raises(ValidationError):
        p.include_comments = True  # type: ignore[misc]


def test_quality_gate_rejects_short_and_junk() -> None:
    ok, reasons = quality_gate("word " * 200)
    assert ok and reasons == ()
    ok, reasons = quality_gate("short text")
    assert not ok and any("too_short" in r for r in reasons)
    junk = ("advertisement subscribe cookie notice " * 5) + ("word " * 100)
    ok, reasons = quality_gate(junk)
    assert not ok and any("junk_contamination" in r for r in reasons)


def test_extract_real_fixture() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    article = extract_article(html, params=ExtractionParams(), source_url="https://www.reuters.com")
    assert article.quality_ok
    assert not article.used_fallback
    assert len(article.text) > 400
    assert article.metadata["title"]


def test_extract_article_requires_trafilatura(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "trafilatura", None)
    with pytest.raises(ConfigError, match="trafilatura") as exc_info:
        extract_article(
            "<html><body>text</body></html>",
            params=ExtractionParams(),
            source_url="https://example.invalid/a",
        )
    assert "uv sync --extra extraction" in str(exc_info.value)


def test_low_quality_routes_to_fallback_before_llm() -> None:
    fallback_calls: list[str] = []

    def fallback(raw_html: str, *, source_url: str) -> str:
        fallback_calls.append(source_url)
        return "This is a complete rendered article. " * 30

    article = extract_article(
        "<html><body>short</body></html>",
        params=ExtractionParams(),
        source_url="https://example.invalid/a",
        fallback=fallback,
    )
    assert fallback_calls == ["https://example.invalid/a"]
    assert article.quality_ok
    assert article.used_fallback


def test_low_quality_without_fallback_fails_closed() -> None:
    article = extract_article(
        "<html><body>short</body></html>",
        params=ExtractionParams(),
        source_url="https://example.invalid/a",
    )
    assert not article.quality_ok
    assert article.quality_reasons


def test_find_span_exact() -> None:
    text = "alpha beta gamma"
    assert find_span(text, "beta") == (6, 10)
    assert find_span(text, "missing") is None


def test_rendered_fallback_lazily_uses_playwright(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extraction, "static_html_fallback", lambda raw_html, **kw: "rendered-ok")

    class FakePage:
        def __init__(self) -> None:
            self.body = ""

        def route(self, pattern: str, handler: object) -> None:
            return None

        def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
            self.body = f"<body>rendered from {url}</body>"

        def content(self) -> str:
            return self.body

    class FakeBrowser:
        def __init__(self) -> None:
            self.page = FakePage()
            self.closed = False

        def new_page(self) -> FakePage:
            return self.page

        def close(self) -> None:
            self.closed = True

    class FakeChromium:
        def __init__(self) -> None:
            self.browser = FakeBrowser()

        def launch(self, *, headless: bool) -> FakeBrowser:
            return self.browser

    class FakePlaywright:
        def __init__(self) -> None:
            self.chromium = FakeChromium()

        def __enter__(self) -> "FakePlaywright":
            return self

        def __exit__(self, *args: object) -> bool:
            return False

    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = FakePlaywright  # type: ignore[attr-defined]
    sync_api.Route = object  # type: ignore[attr-defined]
    playwright = types.ModuleType("playwright")
    monkeypatch.setitem(sys.modules, "playwright", playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)

    assert (
        rendered_html_fallback("<p>js body</p>", source_url="https://example.invalid/a")
        == "rendered-ok"
    )
    assert sync_api.sync_playwright is not None  # type: ignore[attr-defined]
