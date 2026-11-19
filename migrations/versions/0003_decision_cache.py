"""Decision cache and monthly partition registry (B1/B2, docs/12).

`decision_cache` is append-only in intent: the unique cache key is never updated in place;
re-computation with a different version manifest produces a new key/row.
`storage_partitions` records which monthly partitions should be detached by the RSK-11
retention job. Repartitioning existing 0001 tables is a maintenance-window follow-up; this
migration establishes the contract and the new table without rewriting live data.
"""

from __future__ import annotations

from alembic import op

revision = "0003_decision_cache"
down_revision = "0002_gold_set"
branch_labels = None
depends_on = None

UPGRADE = (
    """
    CREATE TABLE IF NOT EXISTS decision_cache (
        cache_key TEXT PRIMARY KEY,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        model_snapshot TEXT NOT NULL,
        prompt_version TEXT NOT NULL,
        schema_hash TEXT NOT NULL,
        input_hash TEXT NOT NULL,
        output JSONB NOT NULL,
        trace_id TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS decision_cache_input_idx ON decision_cache (input_hash);",
    """
    CREATE TABLE IF NOT EXISTS storage_partitions (
        table_name TEXT NOT NULL,
        month DATE NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('hot', 'cold', 'detached')),
        PRIMARY KEY (table_name, month)
    )
    """,
)

DOWNGRADE = (
    "DROP TABLE IF EXISTS storage_partitions;",
    "DROP TABLE IF EXISTS decision_cache;",
)


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
