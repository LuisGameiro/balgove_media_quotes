"""Regression tests for bad input detected in the fourth full review."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from conftest import settings_for
from media_quotes.cli.common import _load_source_configs, load_review_flags
from media_quotes.config import Settings
from media_quotes.exceptions import ConfigError
from media_quotes.processing.curation import review_badge


def _settings(base: Path) -> Settings:
    return settings_for(base, sources_dir=base / "sources")


def test_naive_review_timestamp_is_normalised(tmp_path: Path) -> None:
    (tmp_path / "audit").mkdir()
    flag = {
        "evidence": {
            "old_quote_id": "Q-20261119-0001",
            "old_content_hash": "a" * 64,
            "newer_article_id": "n",
            "newer_content_hash": "b" * 64,
            "newer_is_first_hand": True,
            "newer_rights_cleared": True,
            "superseding_span": "newer text",
            "decision_id": "d",
            "rationale": "r",
        },
        "confidence": 0.9,
        "state": "pending",
        "reviewed_at": "2026-11-19T12:00:00",  # no timezone
    }
    (tmp_path / "audit" / "review-queue.json").write_text(
        json.dumps({"flags": [flag]}), encoding="utf-8"
    )
    flags = load_review_flags(_settings(tmp_path))
    assert len(flags) == 1
    assert flags[0].reviewed_at is not None
    assert flags[0].reviewed_at.tzinfo is UTC
    assert review_badge(flags[0], now=datetime(2026, 11, 19, 13, 0, tzinfo=UTC))


def test_naive_review_timestamp_is_normalised_in_live_queue(tmp_path: Path) -> None:
    (tmp_path / "audit").mkdir()
    flag = {
        "evidence": {
            "old_quote_id": "Q-20261119-0002",
            "old_content_hash": "a" * 64,
            "newer_article_id": "n",
            "newer_content_hash": "b" * 64,
            "newer_is_first_hand": True,
            "newer_rights_cleared": True,
            "superseding_span": "newer text",
            "decision_id": "d",
            "rationale": "r",
        },
        "confidence": 0.9,
        "state": "pending",
        "reviewed_at": "2026-11-19T12:00:00",  # no timezone
    }
    (tmp_path / "audit" / "review-queue-live_feed.json").write_text(
        json.dumps({"flags": [flag]}), encoding="utf-8"
    )
    settings = _settings(tmp_path)
    assert load_review_flags(settings) == []  # fixture queue stays isolated
    flags = load_review_flags(settings, "live_feed")
    assert len(flags) == 1
    assert flags[0].reviewed_at is not None
    assert flags[0].reviewed_at.tzinfo is UTC


def test_malformed_source_config_raises_config_error(tmp_path: Path) -> None:
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "bad.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid source config"):
        _load_source_configs(_settings(tmp_path))


def test_offline_event_field_is_gone() -> None:
    from media_quotes.cli.common import _event

    event = _event(run_id="r", trace_id="t", source_id="s", step="fetch_parse", status="ok")
    assert "offline" not in event
