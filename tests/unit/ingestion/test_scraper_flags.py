"""Diagnostic script flags: environment-driven policy and local topic filtering."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts import test_scraper as scraper

from media_quotes.config import Settings

_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item><title>Ukraine peace talks continue</title><link>https://www.bbc.co.uk/news/ukraine</link>
<pubDate>Thu, 19 Nov 2026 12:00:00 GMT</pubDate></item>
<item><title>Chess championship round-up</title><link>https://www.bbc.co.uk/sport/chess</link>
<pubDate>Thu, 19 Nov 2026 12:00:00 GMT</pubDate></item>
</channel></rss>
"""


def test_scraper_policy_uses_settings(monkeypatch: Any) -> None:
    settings = Settings(
        _env_file=None,
        fetch_timeout_seconds=33.0,
        fetch_requests_per_second=0.25,
        fetch_max_bytes=2048,
        fetch_max_connections=4,
    )
    monkeypatch.setattr(scraper, "get_settings", lambda: settings)
    policy = scraper._policy()
    assert policy.timeout_seconds == 33.0
    assert policy.requests_per_second == 0.25
    assert policy.max_bytes == 2048
    assert policy.max_connections == 4


def test_local_rss_honours_all_topics(tmp_path: Path, capsys: Any) -> None:
    feed_path = tmp_path / "feed.xml"
    feed_path.write_text(_RSS, encoding="utf-8")

    assert scraper.main(["--rss-file", str(feed_path)]) == 0
    default_items = json.loads(capsys.readouterr().out)["discovered"]
    assert len(default_items) == 1
    assert "Ukraine" in default_items[0]["title"]

    assert scraper.main(["--rss-file", str(feed_path), "--all-topics"]) == 0
    all_items = json.loads(capsys.readouterr().out)["discovered"]
    assert len(all_items) == 2
