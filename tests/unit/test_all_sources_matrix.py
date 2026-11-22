"""Offline source x section matrix diagnostics for all 28 registered sources.

Every test here is hermetic: ``fetch_bytes`` is replaced with a guard that refuses any
real request. The only permitted call is the deliberate unapproved probe against
``example.invalid`` used to verify the T0 boundary.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from scripts import test_scraper as scraper

from media_quotes.enums import SOURCE_TYPES
from media_quotes.exceptions import SourceNotPermitted
from media_quotes.pipeline import evaluate_topic
from media_quotes.registry import SourceRegistryRow, load_sources_csv

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCES_CSV = REPO_ROOT / "data" / "sources.csv"
SECTIONS_CSV = REPO_ROOT / "data" / "sections.csv"
GOLD_SET_V2 = REPO_ROOT / "data" / "gold_set" / "synthetic-v2.json"
WINDOW_START = date(2020, 1, 1)
WINDOW_END = date(2030, 12, 31)

ROWS: dict[str, SourceRegistryRow] = {row.source_id: row for row in load_sources_csv(SOURCES_CSV)}
SOURCE_IDS = sorted(ROWS)
SECTION_IDS = scraper.discover_sections(SECTIONS_CSV)
CONTEXT = scraper.load_topic_configuration()
SOURCE_SECTION_CASES = [(source_id, section) for source_id in SOURCE_IDS for section in SECTION_IDS]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if any code path tries to reach a real host."""

    async def blocked_fetch(url: str, **kwargs: Any) -> tuple[bytes, str]:
        if url == "https://example.invalid/probe" and kwargs.get("track_a_approved") is False:
            raise SourceNotPermitted("offline test: unapproved probe refused")
        pytest.fail(f"Unexpected network fetch in a unit test: {url}")

    monkeypatch.setattr(scraper, "fetch_bytes", blocked_fetch)


def _fixture_path(source_id: str, section_id: str) -> Path:
    return (
        REPO_ROOT
        / "data"
        / "fixtures"
        / source_id
        / f"synthetic-{source_id}-topic-{section_id}.html"
    )


def _off_topic_html(text: str, slug: str) -> str:
    return (
        '<!doctype html><html lang="en"><head>'
        f"<title>{slug}</title>"
        f'<link rel="canonical" href="https://fixture.invalid/offtopic/{slug}.html">'
        "</head><body><article>"
        f"<p>Synthetic off-topic sample: {text}</p>"
        "</article></body></html>"
    )


def _gold_negative_articles() -> list[dict[str, Any]]:
    if not GOLD_SET_V2.exists():
        return []
    payload = json.loads(GOLD_SET_V2.read_text(encoding="utf-8"))
    articles = payload.get("articles", []) if isinstance(payload, dict) else payload
    return [article for article in articles if article.get("relevant") is False]


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_registry_row_is_valid(source_id: str) -> None:
    row = ROWS[source_id]
    assert row.source_id == source_id
    assert row.source_name
    assert row.model_validate(row.model_dump()) == row
    assert 1 <= row.tier <= 4
    assert row.source_type in SOURCE_TYPES
    assert isinstance(row.claim_only, bool)
    assert len(row.language) == 2
    expected_usable = (
        row.enabled
        and row.rights_status in {"license_held", "api_permitted"}
        and bool(row.license_reference)
        and row.access_mode in {"licensed_database", "official_api", "public_rss_permitted"}
        and row.discovery_method != "web"
    )
    assert row.usable is expected_usable
    assert source_id in CONTEXT.routes or not CONTEXT.configured


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_topic_gate_refuses_off_topics_from_gold_set_v2(source_id: str) -> None:
    negatives = _gold_negative_articles()
    assert negatives, "gold-set v2 must contain labelled non-relevant articles"
    rule_id = str(
        next(
            (
                scraper._route_attr(route, "topic_rule_id")
                for route in CONTEXT.routes.get(source_id, [])
            ),
            "ukraine-war-v1",
        )
    )
    rule = CONTEXT.rules.get(rule_id)
    assert rule, f"missing topic rule {rule_id!r}"

    for index, article in enumerate(negatives):
        quote_text = next(
            (
                str(quote["quote_text"])
                for quote in article.get("quotes", [])
                if quote.get("quote_text")
            ),
            "",
        )
        direct = evaluate_topic(
            rule,
            {
                "article_text": quote_text,
                "title": article.get("article_ref", f"off-topic-{index}"),
                "tags": [],
                "url": "https://fixture.invalid/offtopic",
            },
        )
        assert direct.status != "accepted", f"{source_id}: accepted off-topic {quote_text!r}"

        wrapped = scraper.evaluate_source_topic(
            source_id,
            {
                "title": "Off-topic article",
                "text": quote_text,
                "url": "https://fixture.invalid/offtopic",
            },
            CONTEXT,
        )
        assert wrapped["status"] != "accepted"

        pipeline_report = scraper.inspect_html_pipeline(
            _off_topic_html(quote_text, f"off-topic-{index}"),
            source_row=ROWS[source_id],
            source_url=f"https://fixture.invalid/{source_id}/off-topic-{index}",
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            context=CONTEXT,
        )
        assert pipeline_report["topic_status"].startswith("rejected")
        assert pipeline_report["quotes_detected"] == 0
        assert pipeline_report["quotes"] == []


@pytest.mark.parametrize(("source_id", "section_id"), SOURCE_SECTION_CASES)
def test_fixture_assigns_exact_section_and_accepts_topic(source_id: str, section_id: str) -> None:
    path = _fixture_path(source_id, section_id)
    if not path.exists():
        pytest.skip(f"no synthetic topic fixture for {source_id}/{section_id}")

    declared = scraper.expected_sections_for_fixture(path)
    assert section_id in declared, f"{path.name} does not declare section {section_id}"

    report = scraper.inspect_html_pipeline(
        path.read_text(encoding="utf-8"),
        source_row=ROWS[source_id],
        source_url=f"https://fixture.invalid/{source_id}/{path.name}",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        context=CONTEXT,
    )
    assert report["topic_status"].startswith("accepted")
    assert report["topic_gate"]["status"] != "rejected"
    assert report["quotes_detected"] >= 1
    assert {quote["section_id"] for quote in report["quotes"]} == {section_id}
    assert all(quote["exact_offset_slice_match"] for quote in report["quotes"])
    assert section_id in report["categories"]["covered"]
    assert report["source_type"] == str(ROWS[source_id].source_type)
    assert report["claim_only"] is ROWS[source_id].claim_only


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_blocked_and_unapproved_sources_never_probe_network(source_id: str) -> None:
    row = ROWS[source_id]
    approved = scraper.is_source_route_approved(source_id, CONTEXT, row)
    report = scraper.validate_single_source(source_id, context=CONTEXT, live_routes=not approved)
    if not approved:
        assert report["live_routes"]["status"] == "blocked_not_probed"
        assert report["feed"]["live_supported"] is False
    assert report["gate_enforcement"]["unapproved_probe_refused"] is True
    assert report["t0_security_boundary_verified"] is True


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_source_type_and_claim_only_survive_in_reports(source_id: str) -> None:
    row = ROWS[source_id]
    report = scraper.validate_single_source(source_id, context=CONTEXT)
    assert report["source_id"] == source_id
    assert report["source_type"] == str(row.source_type)
    assert report["claim_only"] is row.claim_only
    assert report["rights_status"] == str(row.rights_status)
    assert "topic_gate" in report
    assert "language_detection" in report
    assert "categories" in report
    assert "translation_readiness" in report
    assert "gate_enforcement" in report


def test_all_sources_summary_has_required_matrix_fields() -> None:
    summary = scraper.validate_all_sources(context=CONTEXT)
    assert summary["total_sources"] == len(SOURCE_IDS) == 28
    assert summary["approved_routes"] + summary["blocked_routes"] == 28
    assert summary["approved_routes"] == sum(
        scraper.is_source_route_approved(source_id, CONTEXT, ROWS[source_id])
        for source_id in SOURCE_IDS
    )
    assert summary["sources_with_fixtures"] == sum(
        bool(scraper.fixture_html_files(source_id)) for source_id in SOURCE_IDS
    )
    assert set(summary["category_coverage"]["sections"]) == set(SECTION_IDS)
    assert summary["category_coverage"]["total_cells"] == 28 * len(SECTION_IDS)
    assert isinstance(summary["failures"], list)
    assert len(summary["sources"]) == 28


def test_cli_default_audit_stays_offline_and_live_routes_is_opt_in(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []

    async def recording_fetch(url: str, **kwargs: Any) -> tuple[bytes, str]:
        if url == "https://example.invalid/probe":
            raise SourceNotPermitted("offline test: unapproved probe refused")
        calls.append(url)
        return b"<rss><channel></channel></rss>", "sha256"

    monkeypatch.setattr(scraper, "fetch_bytes", recording_fetch)
    exit_code = scraper.main(["--validate-all-sources"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["live_routes_enabled"] is False
    assert not payload["failures"]
    assert all(item["live_routes"]["status"] == "not_probed" for item in payload["sources"])
    assert calls == []


def test_cli_validation_failure_returns_exit_code_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(scraper, "_unapproved_probe_refused", lambda row: False)
    exit_code = scraper.main(["--validate-source", "reuters"])
    capsys.readouterr()
    assert exit_code == 1


def test_cli_live_routes_probes_only_approved_sources(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []

    async def recording_fetch(url: str, **kwargs: Any) -> tuple[bytes, str]:
        if url == "https://example.invalid/probe":
            assert kwargs.get("track_a_approved") is False
            raise SourceNotPermitted("offline test: unapproved probe refused")
        calls.append(url)
        return b"<rss><channel></channel></rss>", "sha256"

    monkeypatch.setattr(scraper, "fetch_bytes", recording_fetch)
    exit_code = scraper.main(["--validate-all-sources", "--live-routes"])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    approved_ids = {
        source_id
        for source_id in SOURCE_IDS
        if scraper.is_source_route_approved(source_id, CONTEXT, ROWS[source_id])
    }
    probed_ids = {
        item["source_id"]
        for item in payload["sources"]
        if item["live_routes"]["status"] == "reachable"
    }
    assert probed_ids == approved_ids
    assert all(
        item["live_routes"]["status"] == "blocked_not_probed"
        for item in payload["sources"]
        if item["source_id"] not in approved_ids
    )
    assert calls and all(url.startswith("https://") for url in calls)
