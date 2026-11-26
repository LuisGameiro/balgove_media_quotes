"""`mq sources` must not claim a successful offline admission probe, and probe/backfill
actions must stay rights-gated.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

import media_quotes.ingestion.backfill as backfill
from conftest import settings_for
from media_quotes.cli import ops
from media_quotes.cli.ops import _do_sources
from media_quotes.config import Settings
from media_quotes.exceptions import ConfigError
from media_quotes.source_configs import SourceConfig

TOPIC = "ukraine-war"


def _settings(tmp_path: Path) -> Settings:
    return settings_for(tmp_path, sources_dir=tmp_path / "sources", report_date=date(2026, 11, 19))


def _source_config(*, enabled: bool = False) -> SourceConfig:
    return SourceConfig(
        source_id="gridnews",
        source_name="Grid News",
        homepage_url="https://gridnews.example",
        feed_url="https://gridnews.example/rss.xml",
        tier=3,
        cadence_hours=24,
        country_region="Global",
        source_type="independent",
        language="en",
        rights_status="license_held" if enabled else "pending_counsel_review",
        access_mode="public_rss_permitted" if enabled else "pending_review",
        license_reference="LIC-GRID" if enabled else "",
        discovery_method="rss" if enabled else "web",
        archive_start_date=date(2026, 1, 1),
        enabled=enabled,
        content_hosts=["gridnews.example"],
    )


def test_sources_add_reports_probe_as_not_run_offline(tmp_path: Path, capsys) -> None:
    settings = _settings(tmp_path)
    assert (
        _do_sources(
            settings,
            tier=None,
            rights_status=None,
            action="add",
            source_id="gridnews",
            source_name="Grid News",
            homepage_url="https://gridnews.example",
            feed_url="https://gridnews.example/rss.xml",
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "not run in offline mode" in out
    assert "Admission probe: robots_txt=OK" not in out
    config = json.loads((tmp_path / "sources" / "gridnews.json").read_text(encoding="utf-8"))
    assert "probe_ready" not in config["notes"]


def test_sources_add_probe_live_scaffolds_then_probes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    settings = _settings(tmp_path)
    calls: list[tuple[str, str]] = []

    def fake_probe(config: SourceConfig, **kwargs: Any) -> dict[str, Any]:
        calls.append((config.source_id, kwargs["topic_id"]))
        return {
            "source_id": config.source_id,
            "rights_approved": False,
            "report_path": "report.json",
        }

    monkeypatch.setattr(ops, "probe_source_live", fake_probe)

    assert (
        _do_sources(
            settings,
            tier=None,
            rights_status=None,
            action="add",
            source_id="gridnews",
            source_name="Grid News",
            homepage_url="https://gridnews.example",
            feed_url="https://gridnews.example/rss.xml",
            topic_id=TOPIC,
            probe=True,
            live=True,
        )
        == 0
    )

    assert calls == [("gridnews", TOPIC)]
    config = json.loads((settings.sources_dir / "gridnews.json").read_text(encoding="utf-8"))
    assert config["enabled"] is False
    assert config["rights_status"] == "pending_counsel_review"
    out = capsys.readouterr().out
    assert '"rights_approved": false' in out
    assert "Admission report: report.json" in out


def test_sources_probe_live_existing_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    settings = _settings(tmp_path)
    settings.sources_dir.mkdir(parents=True, exist_ok=True)
    (settings.sources_dir / "gridnews.json").write_text(
        json.dumps(_source_config().model_dump(mode="json")), encoding="utf-8"
    )
    seen: list[str] = []

    def fake_probe(config: SourceConfig, **kwargs: Any) -> dict[str, Any]:
        seen.append(config.source_id)
        return {"report_path": "admission.json"}

    monkeypatch.setattr(ops, "probe_source_live", fake_probe)

    assert (
        _do_sources(
            settings,
            tier=None,
            rights_status=None,
            action="probe",
            source_id="GridNews",
            live=True,
        )
        == 0
    )
    assert seen == ["gridnews"]
    assert "Admission report: admission.json" in capsys.readouterr().out


def test_sources_probe_without_live_makes_no_network_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    settings = _settings(tmp_path)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("probe without --live must not touch the network")

    monkeypatch.setattr(ops, "probe_source_live", forbidden)
    assert (
        _do_sources(
            settings,
            tier=None,
            rights_status=None,
            action="probe",
            source_id="gridnews",
            live=False,
        )
        == 0
    )
    assert "requires --live" in capsys.readouterr().out


def test_sources_backfill_requires_a_usable_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(
        ops, "_load_registry", lambda _settings: [_source_config().to_registry_row()]
    )

    with pytest.raises(ConfigError, match="not usable"):
        _do_sources(
            settings,
            tier=None,
            rights_status=None,
            action="backfill",
            source_id="gridnews",
            max_cost=10,
        )


def test_sources_backfill_filters_to_requested_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(
        ops, "_load_registry", lambda _settings: [_source_config(enabled=True).to_registry_row()]
    )
    captured: dict[str, Any] = {}

    def fake_backfill(settings_arg: Settings, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"manifest": "manifest.json", "gaps": 0}

    monkeypatch.setattr(backfill, "run_historical_backfill", fake_backfill)

    assert (
        _do_sources(
            settings,
            tier=None,
            rights_status=None,
            action="backfill",
            source_id="gridnews",
            max_cost=10,
            topic_id=TOPIC,
        )
        == 0
    )
    assert captured["source_ids"] == {"gridnews"}
    assert captured["start"] == settings.backfill_start
    assert captured["end"] == settings.report_date
    assert captured["topic_id"] == TOPIC
    assert "Backfill summary" in capsys.readouterr().out
