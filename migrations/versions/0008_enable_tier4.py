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
        "ALTER TABLE sources ADD CONSTRAINT sources_tier4_claim_only "
        "CHECK (tier <> 4 OR claim_only)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE sources DROP CONSTRAINT IF EXISTS sources_tier4_claim_only")
    op.execute("UPDATE sources SET enabled = FALSE, cadence_hours = 0 WHERE tier = 4")
    op.execute(
        "ALTER TABLE sources ADD CONSTRAINT sources_tier4_disabled "
        "CHECK (tier <> 4 OR (enabled = FALSE AND cadence_hours = 0))"
    )
