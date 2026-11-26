"""Consolidated schema contract: one idempotent SQL file, no migration chain."""

from __future__ import annotations

import re
from pathlib import Path

from media_quotes.db.tables import metadata

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "migrations"
SCHEMA = MIGRATIONS / "schema.sql"


def _sql() -> str:
    return SCHEMA.read_text(encoding="utf-8")


def test_migrations_folder_has_only_the_schema_file() -> None:
    assert sorted(path.name for path in MIGRATIONS.iterdir() if path.is_file()) == ["schema.sql"]


def test_schema_sql_and_sqlalchemy_tables_match() -> None:
    declared = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", _sql()))
    assert declared == set(metadata.tables), declared ^ set(metadata.tables)


def test_schema_declares_final_constraints_once() -> None:
    sql = _sql()
    assert sql.count("digests_topic_date_unique") == 1
    assert "UNIQUE (topic_id, digest_date, provenance)" in sql
    assert "sources_tier4_claim_only" in sql
    assert "availability_status IN ('live','partial_body','accessible_no_quotes'," in sql
    assert sql.count("provenance IN ('live_feed','live_bing','manual_sample','fixture')") == 3
    assert "digests_digest_date_key" not in sql


def test_schema_converts_audit_events_partitioning_idempotently() -> None:
    sql = _sql()
    assert "DO $schema$" in sql
    assert "pg_partitioned_table" in sql
    assert "IF NOT is_partitioned THEN" in sql
    assert "PARTITION BY RANGE (created_at)" in sql
    assert "audit_events_default PARTITION OF audit_events DEFAULT" in sql
    assert "DROP TABLE audit_events_legacy" in sql


def test_schema_has_no_revision_or_alembic_markers() -> None:
    sql = _sql()
    assert "alembic" not in sql.lower()
    assert not re.search(r"^\s*revision\s*=", sql, re.MULTILINE)
