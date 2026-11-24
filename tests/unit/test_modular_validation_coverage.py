"""Coverage tests for modular config validation (check_topic_packs & check_source_configs)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import media_quotes.validation.modular as modular_validation
from media_quotes.source_configs import SourceConfig
from media_quotes.validation.modular import check_source_configs, check_topic_packs


def test_check_topic_packs_directory_not_dir(tmp_path: Path) -> None:
    errors: list[str] = []
    res = check_topic_packs(tmp_path / "nonexistent", errors)
    assert res == {}
    assert errors == []


def test_check_topic_packs_empty_directory(tmp_path: Path) -> None:
    empty_dir = tmp_path / "topics"
    empty_dir.mkdir()
    errors: list[str] = []
    res = check_topic_packs(empty_dir, errors)
    assert res == {}
    assert any("expected at least one topic pack" in err for err in errors)


def test_check_topic_packs_invalid_json_and_duplicates(tmp_path: Path) -> None:
    topics_dir = tmp_path / "topics"
    topics_dir.mkdir()

    # Ignored schema file
    (topics_dir / "topic.schema.json").write_text("{}", encoding="utf-8")

    # Invalid JSON
    (topics_dir / "bad.json").write_text("not json", encoding="utf-8")

    # Valid topic pack from real template
    real_pack_data = json.loads(Path("data/topics/ukraine-war.json").read_text(encoding="utf-8"))
    real_pack_data["topic_id"] = "test-topic"

    (topics_dir / "test-topic.json").write_text(json.dumps(real_pack_data), encoding="utf-8")
    (topics_dir / "test-topic.example.json").write_text(
        json.dumps(real_pack_data), encoding="utf-8"
    )

    errors: list[str] = []
    res = check_topic_packs(topics_dir, errors)
    assert "test-topic" in res
    assert any("bad.json" in err for err in errors)
    assert any("duplicate topic_id 'test-topic'" in err for err in errors)


def test_check_source_configs_not_dir_and_empty(tmp_path: Path) -> None:
    errors: list[str] = []
    assert check_source_configs(tmp_path / "nonexistent", {}, {}, errors) == {}
    assert errors == []

    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    assert check_source_configs(sources_dir, {}, {}, errors) == {}
    assert any("expected at least one source config" in err for err in errors)


def test_check_source_configs_rule_violations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()

    # Schema file ignored
    (sources_dir / "source.schema.json").write_text("{}", encoding="utf-8")

    # 1. Invalid JSON
    (sources_dir / "bad.json").write_text("invalid json", encoding="utf-8")

    guardian_data = json.loads(Path("data/sources/guardian.json").read_text(encoding="utf-8"))

    def make_cfg(source_id: str, **overrides: object) -> dict[str, object]:
        base = dict(guardian_data)
        base["source_id"] = source_id
        base["source_name"] = f"Source {source_id}"
        base["homepage_url"] = "https://example.com"
        base["feed_url"] = "https://example.com/feed.xml"
        base["routes"] = []
        base.update(overrides)
        return base

    # 2. Bad config:
    # - enabled with blocked rights
    # - enabled with web discovery
    # - non-https homepage and feed URL
    # - route issues: duplicate route_id, non-https discovery_url, approved without license_ref
    # - route issues: unknown topic_id
    bad_cfg = make_cfg(
        "bad-src",
        enabled=True,
        rights_status="blocked",
        discovery_method="web",
        license_reference="",
        homepage_url="http://insecure.example.com",
        feed_url="http://insecure.example.com/feed.xml",
        routes=[
            {
                "route_id": "r1",
                "route_type": "rss",
                "discovery_url": "http://insecure.example.com/rss",
                "approved": True,
                "license_reference": "",
                "topic_id": "unknown-topic",
                "topic_rule_id": "rule-1",
            },
            {
                "route_id": "r1",  # duplicate
                "route_type": "rss",
                "discovery_url": "https://example.com/rss",
                "approved": False,
                "topic_id": "",
                "topic_rule_id": "rule-2",
            },
        ],
    )
    (sources_dir / "bad-src.json").write_text(json.dumps(bad_cfg), encoding="utf-8")

    # 3. Tier 4 config invalid because it is not claim-only.
    tier4_cfg = make_cfg(
        "tier4-src",
        tier=4,
        enabled=True,
        cadence_hours=12,
        rights_status="blocked",
        source_type="state-controlled",
        claim_only=False,
    )
    (sources_dir / "tier4-src.json").write_text(json.dumps(tier4_cfg), encoding="utf-8")

    # 4. State controlled not claim_only
    sc_cfg = make_cfg(
        "state-src",
        source_type="state-controlled",
        claim_only=False,
        rights_status="blocked",
        enabled=False,
    )
    (sources_dir / "state-src.json").write_text(json.dumps(sc_cfg), encoding="utf-8")

    # 5. Approved rights but no license reference
    lic_missing_cfg = make_cfg(
        "lic-src",
        rights_status="license_held",
        license_reference="",
        enabled=False,
    )
    (sources_dir / "lic-src.json").write_text(json.dumps(lic_missing_cfg), encoding="utf-8")

    # 6. Duplicate source_id via mock
    dup_cfg = make_cfg("dup-src", enabled=False, rights_status="blocked")
    (sources_dir / "dup-src.json").write_text(json.dumps(dup_cfg), encoding="utf-8")
    (sources_dir / "dup-src-2.json").write_text(json.dumps(dup_cfg), encoding="utf-8")

    orig_load = modular_validation.load_source_config_file

    def mock_load(path: Path) -> SourceConfig:
        if path.name == "dup-src-2.json":
            return SourceConfig.model_validate(dup_cfg)
        return orig_load(path)

    monkeypatch.setattr(modular_validation, "load_source_config_file", mock_load)

    csv_data = {
        "bad-src": {
            "rights_status": "api_permitted",  # mismatch
            "enabled": "false",  # mismatch
            "tier_block": "false",
            "blocker_reason": "",
            "availability_status": "live",
            "tier": "3",  # mismatch
            "access_mode": "official_api",  # mismatch
            "source_type": "publicly funded",  # mismatch
            "claim_only": "true",  # mismatch
        },
        "only-in-csv": {
            "rights_status": "blocked",
            "enabled": "false",
            "tier_block": "false",
            "blocker_reason": "",
            "availability_status": "live",
            "tier": "1",
            "access_mode": "scraped_prohibited",
            "source_type": "independent",
            "claim_only": "false",
        },
    }

    errors: list[str] = []
    topic_packs = {"some-topic": MagicMock()}
    configs = check_source_configs(sources_dir, csv_data, topic_packs, errors)

    assert "bad-src" in configs
    assert any("duplicate source_id 'dup-src'" in err for err in errors)
    assert any("enabled sources need approved rights_status" in err for err in errors)
    assert any("enabled sources cannot use web discovery" in err for err in errors)
    assert any("approved rights need a license_reference" in err for err in errors)
    assert any("tier 4 sources must be claim_only" in err for err in errors)
    assert any("state-controlled sources must be claim_only" in err for err in errors)
    assert any("homepage_url must be an https URL" in err for err in errors)
    assert any("feed_url must be an https URL" in err for err in errors)
    assert any("duplicate route_id 'r1'" in err for err in errors)
    assert any("discovery_url must be an https URL" in err for err in errors)
    assert any("approved route 'r1' needs a license_reference" in err for err in errors)
    assert any("references unknown topic_id 'unknown-topic'" in err for err in errors)
    assert any("missing JSON configs for sources.csv entries" in err for err in errors)
    assert any("JSON configs not present in sources.csv" in err for err in errors)
    assert any("differs from sources.csv" in err for err in errors)
