"""schema.sql is the single source of truth; migrations only keep the revision chain."""

from __future__ import annotations

import inspect
import re
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from media_quotes.db.tables import metadata

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "src/media_quotes/db/schema.sql"
MIGRATIONS = ROOT / "migrations/versions"


def _load(name: str):
    path = MIGRATIONS / f"{name}.py"
    spec = spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_schema_sql_and_sqlalchemy_tables_match() -> None:
    sql = SCHEMA.read_text(encoding="utf-8")
    declared = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql))
    assert declared == set(metadata.tables), declared ^ set(metadata.tables)


def test_digest_uniqueness_declared_once_in_schema() -> None:
    sql = SCHEMA.read_text(encoding="utf-8")
    assert sql.count("digests_topic_date_unique") == 1
    assert "UNIQUE (topic_id, digest_date)" in sql


def test_later_migrations_are_noops() -> None:
    chain = (
        ("0002_gold_set", "0001_initial"),
        ("0003_decision_cache", "0002_gold_set"),
        ("0004_topic_language_judge", "0003_decision_cache"),
        ("0005_topic_packs", "0004_topic_language_judge"),
        ("0006_digest_topic_unique", "0005_topic_packs"),
    )
    for name, parent in chain:
        module = _load(name)
        assert module.revision == name
        assert module.down_revision == parent
        for action in (module.upgrade, module.downgrade):
            assert "execute" not in inspect.getsource(action)
            action()


def test_monthly_partition_migration_carries_all_ddl() -> None:
    module = _load("0007_audit_events_monthly_partition")
    assert module.revision == "0007_audit_events_monthly_partition"
    assert module.down_revision == "0006_digest_topic_unique"
    source = inspect.getsource(module)
    assert "PARTITION BY RANGE (created_at)" in source
    assert "audit_events_{month:%Y_%m}" in source
    assert "DEFAULT" in source
    assert "def downgrade" in source
    assert "pg_dump" in source  # maintenance window documented with the DDL


def test_initial_migration_executes_schema_sql() -> None:
    module = _load("0001_initial")
    sql = module._load_schema_sql()
    assert "CREATE TABLE IF NOT EXISTS quotes" in sql
    assert "CREATE TABLE IF NOT EXISTS decision_cache" in sql
    assert "digests_topic_date_unique" in sql


def test_initial_migration_drops_all_schema_tables() -> None:
    module = _load("0001_initial")
    sql = SCHEMA.read_text(encoding="utf-8")
    declared_tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql))
    dropped_tables = {
        stmt.split()[4].rstrip(";")
        for stmt in module._DROP_STATEMENTS
        if stmt.startswith("DROP TABLE IF EXISTS ")
    }
    assert declared_tables == dropped_tables
