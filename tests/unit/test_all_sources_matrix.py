"""Offline source x section matrix diagnostics for all 28 registered sources.

Every test here is hermetic: ``fetch_bytes`` is replaced with a guard that refuses any
real request. The only permitted call is the deliberate unapproved probe against
``example.invalid`` used to verify the T0 boundary.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
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

    monkeypatch.setattr(scraper.diagnostics, "fetch_bytes", blocked_fetch)


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


def _guarded_fetch(
    payload: bytes, calls: list[str] | None = None
) -> Callable[..., Awaitable[tuple[bytes, str]]]:
    """Serve payload for approved fetches; refuse the deliberate unapproved probe."""

    async def fetch(url: str, **kwargs: Any) -> tuple[bytes, str]:
        if url == "https://example.invalid/probe" and kwargs.get("track_a_approved") is False:
            raise SourceNotPermitted("offline test: unapproved probe refused")
        if calls is not None:
            calls.append(url)
        return payload, "sha256"

    return fetch


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
    assert path.exists(), f"no synthetic topic fixture for {source_id}/{section_id}"

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

    monkeypatch.setattr(scraper.diagnostics, "fetch_bytes", recording_fetch)
    exit_code = scraper.main(["--validate-all-sources"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["live_routes_enabled"] is False
    assert not payload["failures"]
    assert all(
        item["live_routes"]["status"] in {"not_probed", "blocked_not_probed"}
        for item in payload["sources"]
    )
    assert all(
        item["live_routes"]["status"] == "blocked_not_probed"
        for item in payload["sources"]
        if item["tier_block"]
    )
    assert calls == []


def test_cli_validation_failure_returns_exit_code_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(scraper.diagnostics, "_unapproved_probe_refused", lambda row: False)
    exit_code = scraper.main(["--validate-source", "reuters"])
    capsys.readouterr()
    assert exit_code == 1


def test_cli_live_routes_probes_only_approved_sources(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []

    async def recording_fetch(url: str, **kwargs: Any) -> tuple[bytes, str]:
        if url == "https://example.invalid/probe":
            raise SourceNotPermitted("offline test: unapproved probe refused")
        assert kwargs.get("track_a_approved") is True
        calls.append(url)
        return scraper.DEMO_RSS_XML.encode("utf-8"), "sha256"

    monkeypatch.setattr(scraper.diagnostics, "fetch_bytes", recording_fetch)
    exit_code = scraper.main(["--validate-all-sources", "--live-routes"])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert not payload["failures"]
    approved_ids = {
        source_id
        for source_id in SOURCE_IDS
        if scraper.is_source_route_approved(source_id, CONTEXT, ROWS[source_id])
        and not ROWS[source_id].tier_block
    }
    probed_ids = {
        item["source_id"] for item in payload["sources"] if item["live_routes"].get("url")
    }
    assert probed_ids <= approved_ids
    assert all(
        item["live_routes"]["status"] == "blocked_not_probed"
        for item in payload["sources"]
        if item["source_id"] not in approved_ids
    )
    for item in payload["sources"]:
        if item["feed"]["url"] != "(none registered)" and item["live_routes"].get("url"):
            assert item["live_routes"]["url"] == item["feed"]["url"]
    assert calls and all(url.startswith("https://") for url in calls)


def test_live_validation_prefers_registered_feed_over_homepage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        scraper.diagnostics,
        "fetch_bytes",
        _guarded_fetch(scraper.DEMO_RSS_XML.encode("utf-8"), calls),
    )

    report = scraper.validate_single_source("ap", context=CONTEXT, live_routes=True)

    assert calls == ["https://apnews.com/index.rss"]
    assert report["live_routes"]["url"] == "https://apnews.com/index.rss"
    assert report["live_routes"]["status"] == "reachable"


def test_live_validation_reports_html_as_wrong_content_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        scraper.diagnostics,
        "fetch_bytes",
        _guarded_fetch(b"<html><body>Not a feed</body></html>"),
    )

    report = scraper.validate_single_source("bbc", context=CONTEXT, live_routes=True)

    assert report["live_routes"]["status"] == "wrong_content_type"
    assert any(failure["code"] == "live_route_wrong_content_type" for failure in report["failures"])


def test_live_validation_reports_empty_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        scraper.diagnostics,
        "fetch_bytes",
        _guarded_fetch(b"<rss><channel></channel></rss>"),
    )

    report = scraper.validate_single_source("bbc", context=CONTEXT, live_routes=True)

    assert report["live_routes"]["status"] == "empty_feed"
    assert report["live_routes"]["articles_discovered"] == 0
    assert "live_route_empty" in report["warnings"]


def test_live_validation_accepts_valid_json_api_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        scraper.diagnostics,
        "fetch_bytes",
        _guarded_fetch(b'{"status":"ok","results":[]}'),
    )
    api_row = ROWS["guardian_api"].model_copy(update={"tier_block": False, "blocker_reason": ""})
    monkeypatch.setattr(scraper.diagnostics, "_load_source_row", lambda source_id: api_row)

    report = scraper.validate_single_source("guardian_api", context=CONTEXT, live_routes=True)

    assert report["live_routes"]["status"] == "reachable_json"
    assert report["live_routes"]["json_valid"] is True


def test_live_validation_reports_html_api_docs_as_wrong_content_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        scraper.diagnostics,
        "fetch_bytes",
        _guarded_fetch(b"<!doctype html><html><body>API documentation</body></html>"),
    )
    api_row = ROWS["guardian_api"].model_copy(update={"tier_block": False, "blocker_reason": ""})
    monkeypatch.setattr(scraper.diagnostics, "_load_source_row", lambda source_id: api_row)

    report = scraper.validate_single_source("guardian_api", context=CONTEXT, live_routes=True)

    assert report["live_routes"]["status"] == "wrong_content_type"


def test_live_feed_cli_returns_failure_for_blocked_source(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    blocked_row = ROWS["rt"].model_copy(
        update={"rights_status": "blocked", "license_reference": "", "enabled": False}
    )
    monkeypatch.setattr(scraper, "_load_source_row", lambda source_id: blocked_row)

    exit_code = scraper.main(["--source-id", "rt", "--live-feed"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["status"] == "blocked_by_t0_gate"


def test_inspect_html_pipeline_require_topic_false_keeps_all_spans() -> None:
    html = (
        '<!doctype html><html lang="en"><head><title>Gardening</title></head><body>'
        "<p>Tomato growing and soil preparation advice for home gardeners.</p>"
        "</body></html>"
    )

    report = scraper.inspect_html_pipeline(
        html,
        source_row=ROWS["bbc"],
        source_url="https://fixture.invalid/bbc/gardening",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        context=CONTEXT,
        require_topic=False,
    )

    assert report["topic_status"] == "off_topic"
    assert report["quotes_detected"] == 0
    assert report["quotes"] == []


def test_validate_single_source_offline_replay_is_hermetic() -> None:
    report = scraper.validate_single_source("reuters", context=CONTEXT)

    assert report["fixtures"]["count"] >= 1
    assert report["fixtures"]["replay_status"].startswith("ok (")
    assert report["fixtures"]["representative_status"].startswith("accepted")
    assert report["categories"]["coverage_complete"] is True
    assert report["categories"]["status"] == "full"
    assert report["categories"]["missing"] == []
    assert report["route"]["approved"] is True
    assert report["live_routes"]["status"] in {"not_probed", "blocked_not_probed"}
    assert report["gate_enforcement"]["network_calls"] == 0
    assert report["warnings"] == []
    assert report["failures"] == []


def test_validate_single_source_reports_missing_fixture_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scraper.diagnostics.sources, "fixture_html_files", lambda source_id: [])

    report = scraper.validate_single_source("guardian", context=CONTEXT)

    assert report["fixtures"]["count"] == 0
    assert report["fixtures"]["replay_status"] == "no_fixtures"
    assert report["fixtures"]["representative_status"] == "no_fixtures"
    assert report["categories"]["status"] == "missing_fixture"
    assert report["language_detection"]["source"] == "source_registry_hint"
    assert "missing_fixture" in report["warnings"]
    assert any(item["code"] == "missing_fixture" for item in report["failures"])


def test_validate_all_sources_offline_summary_consistency() -> None:
    summary = scraper.validate_all_sources(context=CONTEXT)
    coverage = summary["category_coverage"]

    assert summary["live_routes_enabled"] is False
    assert summary["t0_permitted_sources"] + summary["t0_blocked_sources"] == len(SOURCE_IDS)
    assert summary["operationally_enabled_sources"] + summary[
        "operationally_blocked_sources"
    ] == len(SOURCE_IDS)
    assert summary["sources_with_fixtures"] == len(SOURCE_IDS)
    assert coverage["total_cells"] == len(SOURCE_IDS) * len(coverage["sections"])
    assert coverage["covered_cells"] == sum(
        len(item["categories"]["covered"]) for item in summary["sources"]
    )
    assert set(coverage["by_section"]) == set(coverage["sections"])
    assert all(
        item["live_routes"]["status"] in {"not_probed", "blocked_not_probed"}
        for item in summary["sources"]
    )


def test_relay_and_no_quote_status_survive_validation() -> None:
    relay = scraper.validate_single_source("gdelt", context=CONTEXT)
    assert relay["source_type"] == "aggregator"
    assert relay["availability_status"] == "discovery_only"
    assert relay["claim_only"] is False
    assert relay["feed"]["live_supported"] is False
    assert relay["live_routes"]["status"] == "blocked_not_probed"

    blocked = scraper.validate_single_source("reuters", context=CONTEXT)
    assert blocked["availability_status"] == "blocked"
    assert blocked["tier_block"] is True


def test_approved_probe_without_url_is_reported() -> None:
    probe = scraper.diagnostics._probe_approved_route("", ROWS["bbc"])
    assert probe == {
        "status": "no_route_url",
        "message": "Approved route has no discovery URL.",
    }
