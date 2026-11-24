"""Gold-set tables — now defined in db/schema.sql.

This revision stays in the chain only so existing databases keep a valid
`alembic_version`. All DDL lives in the single source of truth, schema.sql,
which 0001_initial executes in full.
"""

from __future__ import annotations

revision = "0002_gold_set"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op: schema.sql creates gold_set_labels and eval_runs."""


def downgrade() -> None:
    """No-op: 0001_initial owns the schema and its downgrade drops the tables."""
