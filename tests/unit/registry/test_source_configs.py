"""SourceConfig Bing discovery opt-in fields, validation and serialization."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from media_quotes.source_configs import SourceConfig, load_source_config, scaffold_source_config


def test_robots_disallow_feed_url() -> None:
    from media_quotes.source_configs import robots_allows

    robots = "User-agent: *\nDisallow: /private/feed.xml\n"
    url = "https://example.com/private/feed.xml"
    assert robots_allows(robots, url) is False
    assert robots_allows(robots, "https://example.com/public/feed.xml") is True


def test_robots_group_specific_user_agent() -> None:
    from media_quotes.source_configs import probe_source_admission, robots_allows

    robots = (
        "User-agent: *\nDisallow: /\nUser-agent: MediaQuotesBot\nDisallow: /blocked\nAllow: /\n"
    )
    assert robots_allows(robots, "https://example.com/open", "MediaQuotesBot/0.1") is True
    assert robots_allows(robots, "https://example.com/blocked", "MediaQuotesBot/0.1") is False
    report = probe_source_admission(
        "https://example.com",
        feed_url="https://example.com/blocked",
        robots_txt_content=robots,
        user_agent="MediaQuotesBot/0.1",
    )
    assert report["robots_txt_ok"] is False
    assert report["admission_ready"] is False


def _config(**overrides: object) -> SourceConfig:
    payload: dict[str, object] = {
        "source_id": "example",
        "source_name": "Example News",
        "homepage_url": "https://example.com",
        "tier": 3,
        "cadence_hours": 24,
        "country_region": "Global",
        "source_type": "independent",
        "language": "en",
        "rights_status": "license_held",
        "access_mode": "official_api",
        "discovery_method": "rss",
        "archive_start_date": "2026-10-01",
    }
    payload.update(overrides)
    return SourceConfig.model_validate(payload)


def test_bing_discovery_fields_default_off() -> None:
    config = _config()
    assert config.bing_discovery_enabled is False
    assert config.content_hosts == []


def test_content_hosts_accepts_hostnames_only() -> None:
    config = _config(content_hosts=[" WWW.Example.com ", "feeds.example.com"])
    assert config.content_hosts == ["www.example.com", "feeds.example.com"]
    for invalid in ("https://example.com/path", "example.com/news", "user@example.com"):
        with pytest.raises(ValidationError, match="hostnames only"):
            _config(content_hosts=[invalid])


def test_bbc_enables_bing_discovery_as_discovery_only() -> None:
    config = load_source_config("bbc")
    assert config.bing_discovery_enabled is True
    assert config.content_hosts == ["www.bbc.co.uk", "feeds.bbci.co.uk"]


def test_reuters_does_not_enable_bing_discovery() -> None:
    config = load_source_config("reuters")
    assert config.bing_discovery_enabled is False
    assert config.content_hosts == []


def test_original_reporting_flag_defaults_to_first_hand_for_legacy_configs() -> None:
    # Legacy configs predate the field, so the model default keeps them first-hand.
    assert _config().original_reporting_flag == "first-hand"
    assert load_source_config("guardian").original_reporting_flag == "first-hand"


def test_original_reporting_flag_rejects_unknown_values() -> None:
    with pytest.raises(ValidationError):
        _config(original_reporting_flag="misreported")


def test_scaffold_serializes_new_fields(tmp_path: Path) -> None:
    path = scaffold_source_config(
        "newsource",
        source_name="New Source",
        homepage_url="https://newsource.example",
        out_dir=tmp_path,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["bing_discovery_enabled"] is False
    assert payload["content_hosts"] == []
    # A scaffolded source is never assumed to be first-hand; rights review must opt in.
    assert payload["original_reporting_flag"] == "unknown"
