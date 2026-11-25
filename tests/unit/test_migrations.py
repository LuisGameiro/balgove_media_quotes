"""schema.sql is the single source of truth; migrations only keep the revision chain."""

from __future__ import annotations

import inspect
import re
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from sqlalchemy import UniqueConstraint

from media_quotes.db.tables import metadata, quotes_table

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


def test_quotes_span_unique_declared_in_schema_and_tables() -> None:
    sql = SCHEMA.read_text(encoding="utf-8")
    assert "UNIQUE (article_id, quote_text_normalised, char_start, char_end)" in sql
    span_constraints = [
        tuple(constraint.columns.keys())
        for constraint in quotes_table.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    assert (
        "article_id",
        "quote_text_normalised",
        "char_start",
        "char_end",
    ) in span_constraints


def test_digest_uniqueness_declared_once_in_schema() -> None:
    sql = SCHEMA.read_text(encoding="utf-8")
    assert sql.count("digests_topic_date_unique") == 1
    assert "UNIQUE (topic_id, digest_date, provenance)" in sql


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


def test_alembic_version_width_migration_precedes_long_revision() -> None:
    module = _load("0007_widen_alembic_version")
    assert module.revision == "0007_widen_version"
    assert module.down_revision == "0006_digest_topic_unique"
    assert "VARCHAR(64)" in inspect.getsource(module.upgrade)

    partition = _load("0007_audit_events_monthly_partition")
    assert partition.down_revision == module.revision


def test_monthly_partition_migration_carries_all_ddl() -> None:
    module = _load("0007_audit_events_monthly_partition")
    assert module.revision == "0007_audit_events_monthly_partition"
    assert module.down_revision == "0007_widen_version"
    source = inspect.getsource(module)
    assert "PARTITION BY RANGE (created_at)" in source
    assert "audit_events_{month:%Y_%m}" in source
    assert "DEFAULT" in source
    assert "def downgrade" in source
    assert "pg_dump" in source  # maintenance window documented with the DDL


def test_tier4_migration_preserves_claim_only_gate() -> None:
    module = _load("0008_enable_tier4")
    assert module.revision == "0008_enable_tier4"
    assert module.down_revision == "0007_audit_events_monthly_partition"
    source = inspect.getsource(module)
    assert "sources_tier4_claim_only" in source
    assert "CHECK (tier <> 4 OR claim_only)" in source
    assert "sources_tier4_disabled" in source


def test_provenance_migration_adds_columns_and_scopes_digest_unique() -> None:
    module = _load("0010_article_provenance")
    assert module.revision == "0010_article_provenance"
    assert module.down_revision == "0009_source_availability"
    source = inspect.getsource(module)
    assert "articles" in source and "provenance" in source
    assert "TEXT NOT NULL DEFAULT 'fixture'" in source
    assert "operator" in source
    assert "DROP COLUMN IF EXISTS provenance" in source
    assert "UNIQUE (topic_id, digest_date, provenance)" in source
    assert "UNIQUE (topic_id, digest_date)" in source  # symmetric downgrade
    schema = SCHEMA.read_text(encoding="utf-8")
    assert "provenance       TEXT NOT NULL DEFAULT 'fixture'" in schema
    assert "provenance          TEXT NOT NULL DEFAULT 'fixture'" in schema
    assert "UNIQUE (topic_id, digest_date, provenance)" in schema


def test_provenance_downgrade_collapses_duplicates_deterministically() -> None:
    module = _load("0010_article_provenance")
    statements = module._DOWNGRADE_DEDUPE_SQL
    assert len(statements) == 3
    assert "fixture.provenance = 'fixture'" in statements[0]
    assert "live.provenance <> 'fixture'" in statements[0]
    assert "other.provenance <> 'live_feed'" in statements[1]
    assert "livefeed.provenance = 'live_feed'" in statements[1]
    assert "loser.digest_id > keeper.digest_id" in statements[2]
    downgrade = inspect.getsource(module.downgrade)
    assert "for statement in _DOWNGRADE_DEDUPE_SQL" in downgrade
    assert downgrade.index("_DOWNGRADE_DEDUPE_SQL") < downgrade.index(
        "DROP CONSTRAINT IF EXISTS digests_topic_date_unique"
    )


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
