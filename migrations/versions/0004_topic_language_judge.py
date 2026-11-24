"""Topic/language/translation columns — now defined in db/schema.sql.

This revision stays in the chain only so existing databases keep a valid
`alembic_version`. All DDL lives in the single source of truth, schema.sql,
which 0001_initial executes in full.
"""

from __future__ import annotations

revision = "0004_topic_language_judge"
down_revision = "0003_decision_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op: schema.sql defines the provenance columns and their constraint."""


def downgrade() -> None:
    """No-op: 0001_initial owns the schema and its downgrade drops the tables."""
