"""Add source availability status and tier-block metadata.

Editorial tiers remain 1-4. ``tier_block`` records operational availability separately and
requires a human-readable blocker reason.
"""

from __future__ import annotations

from alembic import op

revision = "0009_source_availability"
down_revision = "0008_enable_tier4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE sources ADD COLUMN IF NOT EXISTS tier_block BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "ALTER TABLE sources ADD COLUMN IF NOT EXISTS blocker_reason TEXT NOT NULL DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE sources ADD COLUMN IF NOT EXISTS availability_status "
        "TEXT NOT NULL DEFAULT 'live'"
    )
    op.execute(
        "CREATE TABLE IF NOT EXISTS sources_availability_rollback_state ("
        "source_id TEXT PRIMARY KEY, tier_block BOOLEAN NOT NULL, "
        "blocker_reason TEXT NOT NULL, availability_status TEXT NOT NULL)"
    )
    op.execute(
        "UPDATE sources AS s SET tier_block = b.tier_block, "
        "blocker_reason = b.blocker_reason, availability_status = b.availability_status "
        "FROM sources_availability_rollback_state AS b WHERE s.source_id = b.source_id"
    )
    op.execute("DROP TABLE IF EXISTS sources_availability_rollback_state")
    op.execute("ALTER TABLE sources DROP CONSTRAINT IF EXISTS sources_availability_status_check")
    op.execute(
        "ALTER TABLE sources ADD CONSTRAINT sources_availability_status_check "
        "CHECK (availability_status IN ('live','partial_body','accessible_no_quotes',"
        "'blocked','discovery_only','fixture_only'))"
    )
    op.execute("ALTER TABLE sources DROP CONSTRAINT IF EXISTS sources_tier_block_reason")
    op.execute(
        "ALTER TABLE sources ADD CONSTRAINT sources_tier_block_reason "
        "CHECK (NOT tier_block OR blocker_reason <> '')"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE sources DROP CONSTRAINT IF EXISTS sources_tier_block_reason")
    op.execute("ALTER TABLE sources DROP CONSTRAINT IF EXISTS sources_availability_status_check")
    op.execute(
        "CREATE TABLE IF NOT EXISTS sources_availability_rollback_state ("
        "source_id TEXT PRIMARY KEY, tier_block BOOLEAN NOT NULL, "
        "blocker_reason TEXT NOT NULL, availability_status TEXT NOT NULL)"
    )
    op.execute("DELETE FROM sources_availability_rollback_state")
    op.execute(
        "INSERT INTO sources_availability_rollback_state "
        "(source_id, tier_block, blocker_reason, availability_status) "
        "SELECT source_id, tier_block, blocker_reason, availability_status FROM sources"
    )
    op.execute("ALTER TABLE sources DROP COLUMN IF EXISTS availability_status")
    op.execute("ALTER TABLE sources DROP COLUMN IF EXISTS blocker_reason")
    op.execute("ALTER TABLE sources DROP COLUMN IF EXISTS tier_block")
