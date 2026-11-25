"""Allow tier-4 sources to be enabled while keeping them claim-only.

Downgrade restores the previous policy and disables tier-4 rows before reinstating the old
constraint, so the rollback remains executable.
"""

from __future__ import annotations

from alembic import op

revision = "0008_enable_tier4"
down_revision = "0007_audit_events_monthly_partition"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE sources DROP CONSTRAINT IF EXISTS sources_tier4_disabled")
    op.execute("ALTER TABLE sources DROP CONSTRAINT IF EXISTS sources_tier4_claim_only")
    op.execute(
        "CREATE TABLE IF NOT EXISTS sources_tier4_rollback_state ("
        "source_id TEXT PRIMARY KEY, enabled BOOLEAN NOT NULL, cadence_hours INTEGER NOT NULL)"
    )
    op.execute(
        "UPDATE sources AS s SET enabled = b.enabled, cadence_hours = b.cadence_hours "
        "FROM sources_tier4_rollback_state AS b "
        "WHERE s.source_id = b.source_id AND s.tier = 4"
    )
    op.execute("DROP TABLE IF EXISTS sources_tier4_rollback_state")
    op.execute(
        "ALTER TABLE sources ADD CONSTRAINT sources_tier4_claim_only "
        "CHECK (tier <> 4 OR claim_only)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE sources DROP CONSTRAINT IF EXISTS sources_tier4_claim_only")
    op.execute(
        "CREATE TABLE IF NOT EXISTS sources_tier4_rollback_state ("
        "source_id TEXT PRIMARY KEY, enabled BOOLEAN NOT NULL, cadence_hours INTEGER NOT NULL)"
    )
    op.execute("DELETE FROM sources_tier4_rollback_state")
    op.execute(
        "INSERT INTO sources_tier4_rollback_state (source_id, enabled, cadence_hours) "
        "SELECT source_id, enabled, cadence_hours FROM sources WHERE tier = 4"
    )
    op.execute("UPDATE sources SET enabled = FALSE, cadence_hours = 0 WHERE tier = 4")
    op.execute(
        "ALTER TABLE sources ADD CONSTRAINT sources_tier4_disabled "
        "CHECK (tier <> 4 OR (enabled = FALSE AND cadence_hours = 0))"
    )
