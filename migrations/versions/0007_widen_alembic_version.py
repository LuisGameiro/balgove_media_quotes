"""Widen Alembic's version column before the long 0007 revision identifier.

Alembic creates ``alembic_version.version_num`` as VARCHAR(32) by default. The descriptive
0007 revision id is longer, so this short prerequisite migration makes the chain upgradeable.
"""

from __future__ import annotations

from alembic import op

revision = "0007_widen_version"
down_revision = "0006_digest_topic_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(64)")


def downgrade() -> None:
    op.execute("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(32)")
