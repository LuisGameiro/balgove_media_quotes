from datetime import date
from pathlib import Path

import pytest

from media_quotes.adapters import SourceAdapter, SourceNotPermitted, build_adapter
from media_quotes.enums import DISCOVERY_METHODS
from media_quotes.registry import load_sources_csv

SOURCES_CSV = Path(__file__).resolve().parents[2] / "data" / "sources.csv"
ROWS = {row.source_id: row for row in load_sources_csv(SOURCES_CSV)}


def test_factory_builds_adapter_for_every_registered_method() -> None:
    for method in DISCOVERY_METHODS:
        row = ROWS["reuters"].model_copy(update={"discovery_method": method})
        assert isinstance(build_adapter(row), SourceAdapter)


def test_blocked_source_cannot_discover() -> None:
    adapter = build_adapter(ROWS["reuters"])
    assert adapter.permitted is False
    with pytest.raises(SourceNotPermitted):
        adapter.discover(date(2026, 10, 1), date(2026, 10, 2))


def test_web_discovery_is_never_permitted() -> None:
    web_row = ROWS["ap"].model_copy(update={"discovery_method": "web"})
    adapter = SourceAdapter(web_row)
    with pytest.raises(SourceNotPermitted):
        adapter.fetch("https://example.invalid/article")


def test_policy_flags_come_from_registry_without_network() -> None:
    adapter = build_adapter(ROWS["anadolu"])
    assert adapter.policy_flags()["claim_only"] is True
    assert adapter.policy_flags()["source_type"] == "state-controlled"


def test_licensed_row_is_permitted() -> None:
    row = ROWS["reuters"].model_copy(
        update={
            "rights_status": "license_held",
            "access_mode": "licensed_database",
            "license_reference": "LIC-1",
            "enabled": True,
            "discovery_method": "licensed_database",
        }
    )
    adapter = build_adapter(row)
    assert adapter.permitted is True
    with pytest.raises(NotImplementedError):
        adapter.fetch("licensed://article/1")


def test_fixture_parser_ignores_script_and_style_bodies() -> None:
    from media_quotes.adapters.html_extractor import _FixtureHTMLParser

    parser = _FixtureHTMLParser()
    parser.feed(
        "<html><head><title>Real headline</title>"
        '<script type="application/ld+json">'
        '{"headline":"SYNTHETIC FIXTURE - Other / Cross-cutting",'
        '"publisher":{"name":"New York Times APIs (synthetic fixture)"}}'
        '</script><style>.x { content: "css-only quote text here"; }</style></head>'
        '<body><article><p><span class="quote">Minister said "Every synthetic JSON quote must '
        'stay out of the published digest."</span></p></article></body></html>'
    )
    meta = parser.metadata()
    text = str(meta["article_text"])
    assert "SYNTHETIC FIXTURE" not in text
    assert "New York Times APIs" not in text
    assert "css-only quote text here" not in text
    assert str(meta["title"]) == "Real headline"
    assert [span["quote_text"] for span in meta["quote_spans"]] == [
        "Every synthetic JSON quote must stay out of the published digest."
    ]
