"""C4 — Dagster definitions and opt-in persistence flag."""

from __future__ import annotations

import dagster as dg

from media_quotes.config import Settings
from media_quotes.orchestration import CODE_VERSION, PARTITION_START, build_definitions


def test_settings_default_to_offline() -> None:
    assert Settings(_env_file=None).persist_to_postgres is False


def test_build_definitions_has_daily_partitioned_asset() -> None:
    defs = build_definitions()
    assets = list(defs.assets or [])
    assert len(assets) == 1
    asset = assets[0]
    assert isinstance(asset, dg.AssetsDefinition)
    assert asset.key.to_user_string() == "daily_quotes_digest"
    assert asset.code_versions_by_key[asset.key] == CODE_VERSION
    assert isinstance(asset.partitions_def, dg.DailyPartitionsDefinition)
    assert asset.partitions_def.get_partition_keys()[0] == PARTITION_START
