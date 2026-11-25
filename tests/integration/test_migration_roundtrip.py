"""Real Postgres round-trip checks for source-state migrations."""

from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL is required")


def _load_migration(name: str) -> ModuleType:
    path = ROOT / "migrations" / "versions" / name
    spec = importlib.util.spec_from_file_location(f"roundtrip_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def postgres_schema():
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    schema = f"migration_roundtrip_{uuid.uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET search_path TO "{schema}", public'))
    try:
        yield engine, schema
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def _use_schema(connection, schema: str) -> None:
    connection.execute(text(f'SET search_path TO "{schema}", public'))


def _bind_operations(module: ModuleType, connection) -> None:
    module.op = Operations(MigrationContext.configure(connection))


def test_tier4_downgrade_upgrade_restores_source_state(postgres_schema) -> None:
    engine, schema = postgres_schema
    module = _load_migration("0008_enable_tier4.py")
    with engine.begin() as connection:
        _use_schema(connection, schema)
        connection.execute(
            text(
                "CREATE TABLE sources (source_id TEXT PRIMARY KEY, tier INTEGER NOT NULL, "
                "claim_only BOOLEAN NOT NULL, enabled BOOLEAN NOT NULL, "
                "cadence_hours INTEGER NOT NULL)"
            )
        )
        connection.execute(
            text("INSERT INTO sources VALUES ('rt', 4, TRUE, TRUE, 24), ('bbc', 1, FALSE, TRUE, 6)")
        )
        _bind_operations(module, connection)
        module.downgrade()
        disabled = connection.execute(
            text("SELECT enabled, cadence_hours FROM sources WHERE source_id = 'rt'")
        ).one()
        assert tuple(disabled) == (False, 0)
        module.upgrade()
        restored = connection.execute(
            text("SELECT enabled, cadence_hours FROM sources WHERE source_id = 'rt'")
        ).one()
        assert tuple(restored) == (True, 24)


def _audit_events_partitioned(connection, schema: str) -> bool:
    return (
        connection.execute(
            text(
                "SELECT 1 FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "JOIN pg_partitioned_table p ON p.partrelid = c.oid "
                "WHERE c.relname = 'audit_events' AND n.nspname = :schema"
            ),
            {"schema": schema},
        ).first()
        is not None
    )


def _audit_events_indexes(connection, schema: str) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = :schema AND tablename = 'audit_events'"
            ),
            {"schema": schema},
        )
    }


def _sequence_owner(connection, schema: str) -> str:
    return str(
        connection.execute(
            text("SELECT pg_get_serial_sequence(:qualified, 'event_id')"),
            {"qualified": f'"{schema}".audit_events'},
        ).scalar_one()
    )


def test_audit_events_partition_upgrade_downgrade_upgrade_preserves_rows(postgres_schema) -> None:
    engine, schema = postgres_schema
    module = _load_migration("0007_audit_events_monthly_partition.py")
    run_id = "11111111-1111-4111-8111-111111111111"
    with engine.begin() as connection:
        _use_schema(connection, schema)
        connection.execute(
            text("CREATE TABLE pipeline_runs (run_id UUID PRIMARY KEY, pipeline TEXT NOT NULL)")
        )
        connection.execute(text("CREATE SEQUENCE audit_events_event_id_seq AS BIGINT"))
        connection.execute(
            text(
                "CREATE TABLE audit_events ("
                "event_id BIGINT NOT NULL DEFAULT nextval('audit_events_event_id_seq'), "
                "run_id UUID REFERENCES pipeline_runs(run_id), "
                "trace_id TEXT, step TEXT NOT NULL, entity_type TEXT, entity_id TEXT, "
                "status TEXT NOT NULL, details JSONB NOT NULL DEFAULT '{}'::jsonb, "
                "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
                "CONSTRAINT audit_events_pkey PRIMARY KEY (event_id))"
            )
        )
        connection.execute(
            text("ALTER SEQUENCE audit_events_event_id_seq OWNED BY audit_events.event_id")
        )
        connection.execute(
            text("CREATE INDEX audit_events_run_idx ON audit_events (run_id, created_at)")
        )
        connection.execute(text("CREATE INDEX audit_events_trace_idx ON audit_events (trace_id)"))
        connection.execute(
            text("INSERT INTO pipeline_runs VALUES (:run_id, 'mq-run')"), {"run_id": run_id}
        )
        connection.execute(
            text(
                "INSERT INTO audit_events (event_id, run_id, trace_id, step, entity_type, "
                "entity_id, status, details, created_at) VALUES "
                "(1, :run_id, 'trace-1', 'fetch', 'article', 'a1', 'ok', '{}'::jsonb, "
                "'2026-10-15T12:00:00+00'), "
                "(2, :run_id, 'trace-2', 'verify', 'quote', 'q1', 'ok', '{}'::jsonb, "
                "'2026-11-15T12:00:00+00')"
            ),
            {"run_id": run_id},
        )
        connection.execute(text("SELECT setval('audit_events_event_id_seq', 2)"))

        _bind_operations(module, connection)
        assert not _audit_events_partitioned(connection, schema)
        module.upgrade()
        assert _audit_events_partitioned(connection, schema)
        rows = connection.execute(
            text("SELECT event_id, trace_id FROM audit_events ORDER BY event_id")
        ).all()
        assert [tuple(row) for row in rows] == [(1, "trace-1"), (2, "trace-2")]
        partitions = {
            row[0]
            for row in connection.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = :schema"),
                {"schema": schema},
            )
        }
        assert {
            "audit_events_2026_10",
            "audit_events_2026_11",
            "audit_events_default",
        } <= partitions
        assert {"audit_events_run_idx", "audit_events_trace_idx"} <= _audit_events_indexes(
            connection, schema
        )
        assert _sequence_owner(connection, schema).endswith("audit_events_event_id_seq")

        module.downgrade()
        assert not _audit_events_partitioned(connection, schema)
        rows = connection.execute(
            text("SELECT event_id, trace_id FROM audit_events ORDER BY event_id")
        ).all()
        assert [tuple(row) for row in rows] == [(1, "trace-1"), (2, "trace-2")]
        assert {"audit_events_run_idx", "audit_events_trace_idx"} <= _audit_events_indexes(
            connection, schema
        )
        assert _sequence_owner(connection, schema).endswith("audit_events_event_id_seq")

        module.upgrade()
        assert _audit_events_partitioned(connection, schema)
        rows = connection.execute(
            text("SELECT event_id, trace_id FROM audit_events ORDER BY event_id")
        ).all()
        assert [tuple(row) for row in rows] == [(1, "trace-1"), (2, "trace-2")]
        assert {"audit_events_run_idx", "audit_events_trace_idx"} <= _audit_events_indexes(
            connection, schema
        )
        assert _sequence_owner(connection, schema).endswith("audit_events_event_id_seq")


def test_availability_downgrade_upgrade_restores_source_state(postgres_schema) -> None:
    engine, schema = postgres_schema
    module = _load_migration("0009_source_availability.py")
    with engine.begin() as connection:
        _use_schema(connection, schema)
        connection.execute(
            text(
                "CREATE TABLE sources (source_id TEXT PRIMARY KEY, "
                "tier_block BOOLEAN NOT NULL DEFAULT FALSE, "
                "blocker_reason TEXT NOT NULL DEFAULT '', "
                "availability_status TEXT NOT NULL DEFAULT 'live')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO sources VALUES ('reuters', TRUE, 'HTTP 401', 'blocked'), "
                "('bbc', FALSE, '', 'live')"
            )
        )
        _bind_operations(module, connection)
        module.downgrade()
        columns = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = 'sources'"
                ),
                {"schema": schema},
            )
        }
        assert "tier_block" not in columns
        module.upgrade()
        restored = connection.execute(
            text(
                "SELECT tier_block, blocker_reason, availability_status "
                "FROM sources WHERE source_id = 'reuters'"
            )
        ).one()
        assert tuple(restored) == (True, "HTTP 401", "blocked")
