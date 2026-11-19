"""Gold-set labels and Postgres-backed evaluation runs (A2/A3, docs/12).

Append-only per plan: a new gold-set version or eval run is a new row, never an UPDATE.
The 60/20/20 split is stored per article label so eval membership is reproducible.
"""

from __future__ import annotations

from alembic import op

revision = "0002_gold_set"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

UPGRADE = (
    """
    CREATE TABLE IF NOT EXISTS gold_set_labels (
        label_id UUID PRIMARY KEY,
        gold_set_version TEXT NOT NULL,
        split TEXT NOT NULL CHECK (split IN ('train', 'dev', 'test')),
        article_ref TEXT NOT NULL,
        source_id TEXT NOT NULL,
        publication_date DATE NOT NULL,
        relevant BOOLEAN NOT NULL,
        quote_text TEXT,
        section_id TEXT,
        human_label BOOLEAN NOT NULL,
        is_negative_candidate BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_gold_set_labels_version_split "
    "ON gold_set_labels (gold_set_version, split);",
    """
    CREATE TABLE IF NOT EXISTS eval_runs (
        eval_id UUID PRIMARY KEY,
        gold_set_version TEXT NOT NULL,
        run_id TEXT NOT NULL,
        report_date DATE NOT NULL,
        metrics JSONB NOT NULL,
        thresholds JSONB NOT NULL,
        passed BOOLEAN NOT NULL,
        trace_id TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_eval_runs_version "
    "ON eval_runs (gold_set_version, created_at DESC);",
)

DOWNGRADE = (
    "DROP TABLE IF EXISTS eval_runs;",
    "DROP TABLE IF EXISTS gold_set_labels;",
)


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
