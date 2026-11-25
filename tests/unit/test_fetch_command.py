"""Explicit live-fetch command, translation readiness warning and policy settings."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from media_quotes.cli.common import EXIT_CONFIG, EXIT_OK, _translation_readiness_warning
from media_quotes.cli.fetch import _do_fetch
from media_quotes.config import Settings
from media_quotes.fetch import FetchPolicy, fetch_policy_from
from media_quotes.source_configs import load_source_config

ROOT = Path(__file__).resolve().parents[2]
REPORT_DATE = date(2026, 11, 19)


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "sources_csv": ROOT / "data/sources.csv",
        "sources_dir": ROOT / "data/sources",
        "source_topics_csv": ROOT / "data/source_topic_routes.csv",
        "topic_rules_path": ROOT / "data/topic_rules.json",
        "topics_dir": ROOT / "data/topics",
        "sections_csv": ROOT / "data/sections.csv",
        "schema_path": ROOT / "data/quotes.schema.json",
        "audit_dir": tmp_path / "audit",
        "export_dir": tmp_path / "export",
        "blob_dir": tmp_path / "blobs",
        "persist_to_postgres": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


class _NoFetch:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("fetch_feed_documents must not be called in this mode")


def test_fetch_dry_run_makes_no_network_calls(tmp_path: Path, monkeypatch: Any) -> None:
    from media_quotes.cli import fetch as fetch_cli

    no_fetch = _NoFetch()
    monkeypatch.setattr(fetch_cli, "fetch_feed_documents", no_fetch)
    settings = _settings(tmp_path)

    assert (
        _do_fetch(settings, source_id="guardian", report_date=REPORT_DATE, dry_run=True) == EXIT_OK
    )
    assert no_fetch.calls == 0


def test_fetch_refuses_unlicensed_source(tmp_path: Path, monkeypatch: Any) -> None:
    from media_quotes.cli import fetch as fetch_cli

    no_fetch = _NoFetch()
    monkeypatch.setattr(fetch_cli, "fetch_feed_documents", no_fetch)
    settings = _settings(tmp_path)
    # The registry is fully licensed; prove the gate still refuses a synthetic bad row.
    original_load = fetch_cli._load_registry

    def unlicensed_registry(cfg: Any) -> Any:
        return [
            row.model_copy(update={"rights_status": "blocked", "license_reference": ""})
            for row in original_load(cfg)
        ]

    monkeypatch.setattr(fetch_cli, "_load_registry", unlicensed_registry)

    assert (
        _do_fetch(settings, source_id="bbc", report_date=REPORT_DATE, dry_run=False) == EXIT_CONFIG
    )
    assert no_fetch.calls == 0


def test_fetch_live_report_contains_topic_and_articles(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    from media_quotes.cli import fetch as fetch_cli
    from media_quotes.live import FetchedArticle

    article = json.dumps(
        {
            "article_text": (
                'Officials said talks continued. "We remain committed," the minister said.'
            ),
            "publication_date": "2026-11-19",
            "canonical_url": "https://www.theguardian.com/world/2026/nov/19/peace-talks",
        }
    ).encode("utf-8")
    calls: list[str] = []

    def fake_fetch(row: Any, feed_url: str, **kwargs: Any) -> Any:
        calls.append(feed_url)
        url = "https://www.theguardian.com/world/2026/nov/19/peace-talks"
        return (
            [
                {
                    "url": url,
                    "title": "Ukraine peace talks continue",
                    "publication_date": "2026-11-19",
                }
            ],
            [
                FetchedArticle(
                    url=url,
                    title="Ukraine peace talks continue",
                    raw=article,
                    sha256="a" * 64,
                    blob_path="guardian.blob",
                )
            ],
        )

    monkeypatch.setattr(fetch_cli, "fetch_feed_documents", fake_fetch)
    settings = _settings(tmp_path)

    assert (
        _do_fetch(settings, source_id="guardian", report_date=REPORT_DATE, dry_run=False, limit=1)
        == EXIT_OK
    )
    assert calls == ["https://www.theguardian.com/world/ukraine/rss"]
    report = json.loads(capsys.readouterr().out)
    assert report["topic_id"] == "ukraine-war"
    assert report["articles_discovered"] == 1
    assert report["articles_fetched"] == 1
    assert report["articles"][0]["quotes"] >= 1


def test_fetch_live_passes_configured_hosts_as_extra_hosts(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from media_quotes.cli import fetch as fetch_cli

    seen: dict[str, Any] = {}

    def fake_fetch(row: Any, feed_url: str, **kwargs: Any) -> Any:
        seen["extra_hosts"] = kwargs.get("extra_hosts")
        return [], []

    config = load_source_config("guardian").model_copy(
        update={
            "content_hosts": ["cdn.guardian.test"],
            "redirect_hosts": ["redirect.guardian.test"],
        }
    )
    monkeypatch.setattr(fetch_cli, "fetch_feed_documents", fake_fetch)
    monkeypatch.setattr(fetch_cli, "_load_source_configs", lambda settings: {"guardian": config})

    assert (
        _do_fetch(_settings(tmp_path), source_id="guardian", report_date=REPORT_DATE, dry_run=False)
        == EXIT_OK
    )
    assert seen["extra_hosts"] == frozenset({"cdn.guardian.test", "redirect.guardian.test"})


def test_translation_warning_only_when_translate_is_on(tmp_path: Path) -> None:
    assert _translation_readiness_warning(_settings(tmp_path)) is None
    configured = _settings(
        tmp_path,
        translation_mode="translate",
        llm_provider="openrouter",
        llm_model_snapshot="openai/gpt-4o-mini-2024-07-18",
        openrouter_api_key=SecretStr("test-key"),
    )
    warning = _translation_readiness_warning(configured)
    assert warning is not None
    assert "translation_rights_confirmed" in warning


def test_fetch_policy_from_settings_uses_overrides(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        fetch_timeout_seconds=20.0,
        fetch_requests_per_second=0.5,
        fetch_max_bytes=1024,
        fetch_max_connections=3,
    )
    policy = fetch_policy_from(settings)
    assert isinstance(policy, FetchPolicy)
    assert policy.timeout_seconds == 20.0
    assert policy.requests_per_second == 0.5
    assert policy.max_bytes == 1024
    assert policy.max_connections == 3
