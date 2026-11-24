"""Digest (topic_id, digest_date) uniqueness — now defined in db/schema.sql.

This revision stays in the chain only so existing databases keep a valid
`alembic_version`. All DDL lives in the single source of truth, schema.sql,
which 0001_initial executes in full with a single named constraint.
"""

from __future__ import annotations

revision = "0006_digest_topic_unique"
down_revision = "0005_topic_packs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op: schema.sql declares digests_topic_date_unique once."""


def downgrade() -> None:
    """No-op: 0001_initial owns the schema and its downgrade drops the tables."""
