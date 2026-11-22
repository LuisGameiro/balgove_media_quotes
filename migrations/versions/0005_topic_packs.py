"""Multi-topic pack tracking across quotes, digests and pipeline runs.

Additive, defaulted columns so existing append-only rows remain valid and default
to the canonical 'ukraine-war' topic pack.
"""

from __future__ import annotations

from alembic import op

revision = "0005_topic_packs"
down_revision = "0004_topic_language_judge"
branch_labels = None
depends_on = None

UPGRADE = (
    """
    ALTER TABLE quotes
        ADD COLUMN IF NOT EXISTS topic_id TEXT NOT NULL DEFAULT 'ukraine-war'
    """,
    """
    ALTER TABLE digests
        ADD COLUMN IF NOT EXISTS topic_id TEXT NOT NULL DEFAULT 'ukraine-war'
    """,
    """
    ALTER TABLE pipeline_runs
        ADD COLUMN IF NOT EXISTS topic_id TEXT NOT NULL DEFAULT 'ukraine-war'
    """,
    "CREATE INDEX IF NOT EXISTS quotes_topic_id_idx ON quotes (topic_id);",
    "CREATE INDEX IF NOT EXISTS digests_topic_date_idx ON digests (topic_id, digest_date);",
    "CREATE INDEX IF NOT EXISTS pipeline_runs_topic_idx ON pipeline_runs (topic_id);",
)

DOWNGRADE = (
    "DROP INDEX IF EXISTS pipeline_runs_topic_idx;",
    "DROP INDEX IF EXISTS digests_topic_date_idx;",
    "DROP INDEX IF EXISTS quotes_topic_id_idx;",
    "ALTER TABLE pipeline_runs DROP COLUMN IF EXISTS topic_id;",
    "ALTER TABLE digests DROP COLUMN IF EXISTS topic_id;",
    "ALTER TABLE quotes DROP COLUMN IF EXISTS topic_id;",
)


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
