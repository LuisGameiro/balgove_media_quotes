"""Admission-only live probe: no rights grant, robots respected, report persisted."""

from __future__ import annotations

import json
import socket
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

import media_quotes.ingestion.fetch as fetch
from conftest import settings_for
from media_quotes.config import Settings
from media_quotes.ingestion.admission import probe_source_live
from media_quotes.source_configs import SourceConfig, probe_source_admission

TOPIC = "ukraine-war"
SID = "probe-news"
HOME = "https://www.example.com"
FEED = "https://www.example.com/feed.xml"
ARTICLE = "https://www.example.com/article"
REPORT_DATE = date(2026, 11, 24)

_FEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item>
<title>Front line update</title>
<link>https://www.example.com/article</link>
<pubDate>2026-10-01 00:00:00 GMT</pubDate>
</item>
</channel></rss>
"""
_ARTICLE_HTML = """<html><body>
<p>Officials said the situation on the front line remained difficult.</p>
<p>"The situation on the ground remains difficult and the front line is unstable today."</p>
</body></html>
"""


@pytest.fixture(autouse=True)
def _global_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep ``check_dns=True`` deterministic and offline for every probe test."""

    def resolve(host: str, port: int, *args: Any, **kwargs: Any) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(fetch.socket, "getaddrinfo", resolve)


def _settings(tmp_path: Path) -> Settings:
    return settings_for(tmp_path, report_date=REPORT_DATE, active_topic=TOPIC)


def _config(*, archive_start: date = date(2026, 10, 1)) -> SourceConfig:
    return SourceConfig(
        source_id=SID,
        source_name="Probe News",
        homepage_url=HOME,
        feed_url=FEED,
        tier=3,
        cadence_hours=24,
        country_region="Global",
        source_type="independent",
        language="en",
        rights_status="pending_counsel_review",
        access_mode="pending_review",
        discovery_method="web",
        archive_start_date=archive_start,
        enabled=False,
        content_hosts=["www.example.com"],
    )


def _transport(
    *, robots: str = "User-agent: *\nDisallow: /private\n", article: str = _ARTICLE_HTML
) -> httpx.MockTransport:
    bodies: dict[str, str] = {
        "/robots.txt": robots,
        "/feed.xml": _FEED_XML,
        "/article": article,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = bodies.get(request.url.path)
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, text=body)

    return httpx.MockTransport(handler)


def test_robots_disallow_skips_sample_article(tmp_path: Path) -> None:
    report = probe_source_live(
        _config(),
        settings=_settings(tmp_path),
        topic_id=TOPIC,
        transport=_transport(robots="User-agent: *\nDisallow: /\n"),
    )

    assert report["robots_txt_ok"] is False
    assert report["network_calls"] == 2  # robots + feed only
    assert report["sample_url"] == ""
    assert report["sample_quotes_found"] == 0
    assert report["rights_approved"] is False
    assert "Human T0 rights review" in report["notes"]


def test_failed_robots_fetch_fails_closed_for_sample(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(503)
        body = {"/feed.xml": _FEED_XML, "/article": _ARTICLE_HTML}.get(request.url.path)
        return httpx.Response(200, text=body) if body is not None else httpx.Response(404)

    report = probe_source_live(
        _config(),
        settings=_settings(tmp_path),
        topic_id=TOPIC,
        transport=httpx.MockTransport(handler),
    )

    assert report["robots_txt_ok"] is False
    assert report["network_calls"] == 2  # robots + feed only; no sample article
    assert report["sample_url"] == ""
    assert report["sample_quotes_found"] == 0
    assert report["admission_ready"] is False
    assert any("robots.txt could not be fetched" in error for error in report["errors"])


def test_robots_redirect_fails_closed(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(301, headers={"location": "/robots-final.txt"})
        body = {"/feed.xml": _FEED_XML, "/article": _ARTICLE_HTML}.get(request.url.path)
        return httpx.Response(200, text=body) if body is not None else httpx.Response(404)

    report = probe_source_live(
        _config(),
        settings=_settings(tmp_path),
        topic_id=TOPIC,
        transport=httpx.MockTransport(handler),
    )

    assert report["robots_txt_ok"] is False
    assert report["admission_ready"] is False
    assert report["network_calls"] == 2  # robots + feed; the redirect target was not followed
    assert report["sample_url"] == ""
    assert any("unexpected redirect" in error for error in report["errors"])


def test_failed_feed_fetch_is_not_admission_ready(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/feed.xml":
            return httpx.Response(503)
        body = {"/robots.txt": "User-agent: *\nAllow: /\n", "/article": _ARTICLE_HTML}.get(
            request.url.path
        )
        return httpx.Response(200, text=body) if body is not None else httpx.Response(404)

    report = probe_source_live(
        _config(),
        settings=_settings(tmp_path),
        topic_id=TOPIC,
        transport=httpx.MockTransport(handler),
    )

    assert report["robots_txt_ok"] is True
    assert report["feed_depth_ok"] is False
    assert report["admission_ready"] is False
    assert report["sample_url"] == ""
    assert any("/feed.xml" in error for error in report["errors"])


def test_feed_and_sample_success(tmp_path: Path) -> None:
    report = probe_source_live(
        _config(),
        settings=_settings(tmp_path),
        topic_id=TOPIC,
        transport=_transport(),
    )

    assert report["robots_txt_ok"] is True
    assert report["network_calls"] == 3  # robots + feed + one sample
    assert report["sample_url"] == ARTICLE
    assert report["feed_entries_found"] == 1
    assert report["earliest_feed_date"] == "2026-10-01"
    assert report["sample_quotes_found"] >= 1
    assert report["first_hand_check_ok"] is True
    assert report["archive_depth_ok"] is True
    assert report["admission_ready"] is True


def test_archive_depth_failure(tmp_path: Path) -> None:
    # The feed's earliest entry is newer than the declared archive start.
    report = probe_source_live(
        _config(archive_start=date(2025, 1, 1)),
        settings=_settings(tmp_path),
        topic_id=TOPIC,
        transport=_transport(),
    )

    assert report["earliest_feed_date"] == "2026-10-01"
    assert report["archive_depth_ok"] is False
    assert report["admission_ready"] is False


def test_report_is_persisted_atomically(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    report = probe_source_live(_config(), settings=settings, topic_id=TOPIC, transport=_transport())

    path = settings.audit_dir / "admission" / f"{SID}-2026-11-24.json"
    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8")) == report
    assert not list(path.parent.glob(".*.tmp"))


def test_disabled_source_is_unchanged(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.sources_dir = tmp_path / "sources"
    settings.sources_dir.mkdir(parents=True, exist_ok=True)
    config = _config()
    path = settings.sources_dir / f"{SID}.json"
    path.write_text(json.dumps(config.model_dump(mode="json")), encoding="utf-8")
    before: str = path.read_text(encoding="utf-8")

    report = probe_source_live(config, settings=settings, topic_id=TOPIC, transport=_transport())

    assert path.read_text(encoding="utf-8") == before
    assert json.loads(before)["enabled"] is False
    assert json.loads(before)["rights_status"] == "pending_counsel_review"
    assert report["rights_approved"] is False


def test_offline_probe_keeps_behavior_without_dates() -> None:
    result: dict[str, Any] = probe_source_admission(HOME)

    assert result["feed_depth_ok"] is True
    assert result["archive_depth_ok"] is True
    assert result["admission_ready"] is True
    assert result["archive_start_date"] is None

    result = probe_source_admission(
        HOME,
        feed_sample_content=_FEED_XML,
        archive_start_date=date(2026, 10, 1),
    )
    assert result["archive_depth_ok"] is True
    assert result["earliest_feed_date"] == "2026-10-01"


def test_private_ip_host_is_rejected_before_transport_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def private_dns(host: str, port: int, *args: Any, **kwargs: Any) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", port))]

    monkeypatch.setattr(fetch.socket, "getaddrinfo", private_dns)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text="unreachable")

    report = probe_source_live(
        _config(),
        settings=_settings(tmp_path),
        topic_id=TOPIC,
        transport=httpx.MockTransport(handler),
    )

    assert requests == []  # the transport was never reached
    assert report["network_calls"] == 0
    assert any("non-global IP" in error for error in report["errors"])
    assert report["sample_url"] == ""
