"""Tests for the per-source proof report runner."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from scripts import source_proof

from media_quotes.config import Settings


def _row(source_id: str, **overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "source_id": source_id,
        "source_name": source_id.upper(),
        "homepage_url": f"https://{source_id}.example",
        "tier": 2,
        "tier_block": False,
        "blocker_reason": "",
        "availability_status": "live",
        "claim_only": False,
        "rights_status": "license_held",
        "access_mode": "licensed_database",
        "archive_start_date": date(2026, 10, 1),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_offline_proof_defaults_to_test_output(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        _env_file=None,
        output_dir=tmp_path / "output",
        report_date=date(2026, 11, 24),
    )
    monkeypatch.setattr(source_proof, "get_settings", lambda: settings)

    assert source_proof.main(["--offline", "--source", "bbc"]) == 0
    assert (tmp_path / "test-output" / "proof" / "source-proof-2026-11-24.json").is_file()
    assert not (tmp_path / "output" / "proof" / "source-proof-2026-11-24.json").exists()


def test_parse_assignments_requires_source_and_value() -> None:
    assert source_proof._parse_assignments(["reuters=article.html"], "--sample") == {
        "reuters": "article.html"
    }
    with pytest.raises(ValueError, match="SOURCE_ID=VALUE"):
        source_proof._parse_assignments(["reuters"], "--sample")


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Client error '401 HTTP Forbidden'", "unauthorized"),
        ("Client error '403 Forbidden'", "forbidden"),
        ("Client error '404 Not Found'", "not_found"),
        ("Redirect response '301 Moved Permanently'", "redirect"),
        ("Response is HTML, not RSS", "wrong_content_type"),
        ("ConnectError: all connection attempts failed", "network"),
        ("ConnectTimeout", "network"),
        ("article URL must be absolute https", "insecure_feed_url"),
        (
            "URL host 'other.invalid' is not registered for source 'example'",
            "unapproved_redirect_or_host",
        ),
        ("No feed URL registered", "no_public_feed"),
    ],
)
def test_blocker_from_text(message: str, expected: str) -> None:
    assert source_proof._blocker_from_text(message) == expected


def test_build_report_excludes_fixture_quotes_from_collected_data() -> None:
    entries = [
        {
            "status": "live_verified",
            "mode": "bing_discovery",
            "collected": True,
            "quotes_verified": 2,
            "blocker": "",
        },
        {
            "status": "fixture_only",
            "mode": "fixture_fallback",
            "collected": False,
            "quotes_verified": 1,
            "blocker": "unauthorized",
        },
        {
            "status": "blocked",
            "mode": "fixture_fallback",
            "collected": False,
            "quotes_verified": 0,
            "blocker": "no_public_feed",
        },
        {
            "status": "discovery_only",
            "mode": "discovery_only",
            "collected": False,
            "quotes_verified": 0,
            "blocker": "",
        },
    ]

    report = source_proof.build_report(
        entries,
        generated_at="2026-11-24T00:00:00Z",
        topic_id="ukraine-war",
        live=True,
    )

    assert report["summary"] == {
        "total_sources": 4,
        "statuses": {
            "live_verified": 1,
            "fixture_only": 1,
            "blocked": 1,
            "discovery_only": 1,
        },
        "sources_with_collected_data": 1,
        "sources_with_verified_quotes": 1,
        "sources_fixture_only": 2,
        "sources_not_collected": 3,
        "sources_with_blockers": 2,
        "live_quotes_verified": 2,
        "sample_quotes_verified": 0,
        "fixture_quotes_verified": 1,
    }


def test_render_markdown_includes_availability_and_blockers() -> None:
    report = {
        "topic_id": "ukraine-war",
        "live_requested": True,
        "summary": {
            "total_sources": 1,
            "sources_with_collected_data": 0,
            "sources_fixture_only": 1,
            "sources_not_collected": 1,
            "sources_with_blockers": 1,
        },
        "sources": [
            {
                "source_name": "Reuters",
                "source_id": "reuters",
                "tier": 1,
                "availability_status": "blocked",
                "mode": "fixture_fallback",
                "status": "fixture_only",
                "articles_fetched": 3,
                "quotes_verified": 0,
                "sections": ["war_intensity"],
                "blocker": "unauthorized",
                "detail": "401",
            }
        ],
    }

    markdown = source_proof.render_markdown(report, "2026-11-24")

    assert "# Source collection proof — 2026-11-24" in markdown
    assert "Reuters (`reuters`)" in markdown
    assert "war_intensity" in markdown
    assert "`unauthorized` — 401" in markdown


def test_no_quote_status_uses_registry_availability() -> None:
    assert (
        source_proof._no_quote_status(_row("ft", availability_status="partial_body"), [])
        == "partial_body"
    )
    assert source_proof._no_quote_status(_row("kyodo"), []) == "accessible_no_quotes"
    assert (
        source_proof._no_quote_status(_row("scmp", availability_status="partial_body"), [])
        == "partial_body"
    )


def test_live_entry_marks_verified_quotes(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _row("bbc", tier=1)
    monkeypatch.setattr(source_proof, "_resolve_feed_url", lambda *a, **k: "https://feed")
    monkeypatch.setattr(
        source_proof,
        "run_live_feed",
        lambda *a, **k: {
            "articles_discovered_in_feed": 1,
            "articles": [
                {
                    "url": "https://article",
                    "quotes_verified_publishable": 1,
                    "categories": {"covered": ["war_intensity"]},
                    "metadata": {
                        "title": "Title",
                        "canonical_url": "https://article",
                        "publication_date": "2026-11-24",
                        "byline": "Reporter",
                        "publication_name": "BBC",
                        "extracted_text_length": 1500,
                    },
                }
            ],
        },
    )

    entry = source_proof._live_entry(
        row,  # type: ignore[arg-type]
        MagicMock(),
        MagicMock(),
        topic_id="ukraine-war",
        limit=1,
    )

    assert entry["status"] == "live_verified"
    assert entry["collected"] is True
    assert entry["quotes_verified"] == 1
    assert entry["metadata_complete"] == 1
    assert entry["max_text_length"] == 1500
    assert entry["blocker"] == ""


def test_bing_entry_passes_rolling_report_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import timedelta

    from media_quotes.collection import CollectionResult

    settings = Settings(_env_file=None, report_date=date(2026, 11, 20))
    monkeypatch.setattr(source_proof, "get_settings", lambda: settings)
    captured: dict[str, object] = {}

    def fake_collect_bing(row: object, domain: str, **kwargs: object) -> CollectionResult:
        captured.update(kwargs)
        return CollectionResult(mode="bing_discovery")

    monkeypatch.setattr(source_proof, "collect_bing", fake_collect_bing)
    source_proof._bing_entry(
        _row("ap"),
        MagicMock(),
        MagicMock(),
        limit=1,  # type: ignore[arg-type]
    )

    assert captured["start"] == settings.report_date - timedelta(
        days=settings.report_window_days - 1
    )
    assert captured["end"] == settings.report_date


def test_live_entry_uses_bing_fallback_when_feed_has_no_quotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _row("ap")
    monkeypatch.setattr(source_proof, "_resolve_feed_url", lambda *a, **k: "https://feed")
    monkeypatch.setattr(
        source_proof,
        "run_live_feed",
        lambda *a, **k: {
            "articles_discovered_in_feed": 1,
            "articles": [{"quotes_verified_publishable": 0, "categories": {"covered": []}}],
        },
    )
    monkeypatch.setattr(
        source_proof,
        "_bing_entry",
        lambda *a, **k: {
            "status": "live_verified",
            "collected": True,
            "articles_fetched": 1,
            "quotes_verified": 2,
            "blocker": "",
        },
    )

    entry = source_proof._live_entry(
        row,  # type: ignore[arg-type]
        MagicMock(),
        MagicMock(),
        topic_id="ukraine-war",
        limit=1,
    )

    assert entry["status"] == "live_verified"
    assert entry["collected"] is True
    assert entry["quotes_verified"] == 2


def test_live_entry_keeps_feed_quotes_when_bing_has_no_quotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _row("ap")
    monkeypatch.setattr(source_proof, "_resolve_feed_url", lambda *a, **k: "https://feed")
    monkeypatch.setattr(
        source_proof,
        "run_live_feed",
        lambda *a, **k: {
            "articles_discovered_in_feed": 2,
            "articles": [
                {
                    "status": "ok",
                    "quotes_verified_publishable": 1,
                    "categories": {"covered": ["other"]},
                    "url": "https://apnews.com/good",
                },
                {"status": "fetch_or_parse_error", "error": "timeout"},
            ],
        },
    )
    monkeypatch.setattr(
        source_proof,
        "_bing_entry",
        lambda *a, **k: {
            "status": "accessible_no_quotes",
            "collected": False,
            "articles_fetched": 1,
            "quotes_verified": 0,
            "blocker": "accessible_no_quotes",
        },
    )

    entry = source_proof._live_entry(
        row,  # type: ignore[arg-type]
        MagicMock(),
        MagicMock(),
        topic_id="ukraine-war",
        limit=1,
    )

    assert entry["status"] == "live_partial"
    assert entry["collected"] is True
    assert entry["quotes_verified"] == 1
    assert entry["blocker"] == "partial_article_failures"


def test_url_entry_fails_closed_when_collect_returns_no_articles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        source_proof,
        "collect_urls",
        lambda *args, **kwargs: SimpleNamespace(articles=[]),
    )
    monkeypatch.setattr(source_proof, "_proof_policy", lambda: object())
    monkeypatch.setattr(source_proof, "_approved_redirect_hosts", lambda row: frozenset())
    monkeypatch.setattr(
        source_proof,
        "_fixture_entry",
        lambda row, context, **kwargs: captured.update(kwargs) or {"mode": kwargs["mode"]},
    )

    entry = source_proof._url_entry(
        _row("guardian"),  # type: ignore[arg-type]
        "https://guardian.example/article",
        MagicMock(),
        MagicMock(),
    )

    assert entry["mode"] == "fixture_fallback"
    assert "no articles" in str(captured["detail"])


def test_discovery_only_source_can_be_blocked_for_quote_acquisition() -> None:
    entry = source_proof._live_entry(
        _row(
            "gdelt",
            availability_status="discovery_only",
            tier_block=True,
            blocker_reason="Metadata only; no article text.",
        ),  # type: ignore[arg-type]
        MagicMock(),
        MagicMock(),
        topic_id="ukraine-war",
        limit=1,
    )

    assert entry["status"] == "blocked"
    assert entry["collected"] is False
    assert entry["blocker"] == "Metadata only; no article text."
