"""Allow one digest manifest per topic and date.

Migration 0005 added `digests.topic_id` but left the older global UNIQUE on `digest_date`,
so a second topic on the same day silently replaced the first topic's manifest. Replace
that constraint with a composite one.
"""

from __future__ import annotations

from alembic import op

revision = "0006_digest_topic_unique"
down_revision = "0005_topic_packs"
branch_labels = None
depends_on = None

UPGRADE = (
    "ALTER TABLE digests DROP CONSTRAINT IF EXISTS digests_digest_date_key;",
    "ALTER TABLE digests ADD CONSTRAINT digests_topic_date_unique UNIQUE (topic_id, digest_date);",
)

DOWNGRADE = (
    "ALTER TABLE digests DROP CONSTRAINT IF EXISTS digests_topic_date_unique;",
    "ALTER TABLE digests ADD CONSTRAINT digests_digest_date_key UNIQUE (digest_date);",
)


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
