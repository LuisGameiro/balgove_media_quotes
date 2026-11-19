"""A2 — migration 0002 declares append-only gold-set/eval tables (no raw DML)."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[2] / "migrations/versions/0002_gold_set.py"


def _load():
    spec = spec_from_file_location("m0002", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_0003_decision_cache():
    path = Path(__file__).resolve().parents[2] / "migrations/versions/0003_decision_cache.py"
    spec = spec_from_file_location("m0003", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0003_decision_cache"
    assert module.down_revision == "0002_gold_set"
    sql = " ".join(module.UPGRADE).lower()
    assert "decision_cache" in sql
    assert "storage_partitions" in sql
    assert "cache_key text primary key" in sql


def test_migration_metadata_and_tables():
    m = _load()
    assert m.revision == "0002_gold_set"
    assert m.down_revision == "0001_initial"
    sql = " ".join(m.UPGRADE).lower()
    assert "gold_set_labels" in sql
    assert "eval_runs" in sql
    assert "split in ('train', 'dev', 'test')" in sql
    assert "jsonb" in sql
    assert all(stmt.strip().upper().startswith(("CREATE", "--")) for stmt in m.UPGRADE)
    assert all("DROP" in stmt.upper() for stmt in m.DOWNGRADE)
