"""Add acquisition provenance to articles, quotes and digests.

Existing rows keep the 'fixture' default. Reclassifying legacy blob-backed rows is a
separate, operator-driven decision; this migration deliberately does not guess it.
Quote-level provenance prevents an article upgrade from retroactively turning its
pre-existing fixture quotes into live ones, and digest uniqueness now includes it so
fixture and live manifests for the same topic/date can coexist.
"""

from __future__ import annotations

from alembic import op

revision = "0010_article_provenance"
down_revision = "0009_source_availability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE articles ADD COLUMN IF NOT EXISTS provenance TEXT NOT NULL DEFAULT 'fixture'"
    )
    op.execute("ALTER TABLE articles DROP CONSTRAINT IF EXISTS articles_provenance_check")
    op.execute(
        "ALTER TABLE articles ADD CONSTRAINT articles_provenance_check "
        "CHECK (provenance IN ('live_feed','live_bing','manual_sample','fixture'))"
    )

    op.execute(
        "ALTER TABLE quotes ADD COLUMN IF NOT EXISTS provenance TEXT NOT NULL DEFAULT 'fixture'"
    )
    op.execute("ALTER TABLE quotes DROP CONSTRAINT IF EXISTS quotes_provenance_check")
    op.execute(
        "ALTER TABLE quotes ADD CONSTRAINT quotes_provenance_check "
        "CHECK (provenance IN ('live_feed','live_bing','manual_sample','fixture'))"
    )

    op.execute(
        "ALTER TABLE digests ADD COLUMN IF NOT EXISTS provenance TEXT NOT NULL DEFAULT 'fixture'"
    )
    op.execute("ALTER TABLE digests DROP CONSTRAINT IF EXISTS digests_provenance_check")
    op.execute(
        "ALTER TABLE digests ADD CONSTRAINT digests_provenance_check "
        "CHECK (provenance IN ('live_feed','live_bing','manual_sample','fixture'))"
    )
    op.execute("ALTER TABLE digests DROP CONSTRAINT IF EXISTS digests_topic_date_unique")
    op.execute(
        "ALTER TABLE digests ADD CONSTRAINT digests_topic_date_unique "
        "UNIQUE (topic_id, digest_date, provenance)"
    )


def downgrade() -> None:
    # Fixture manifests are test-only; discard them when a real manifest exists so the
    # legacy two-column uniqueness rule can be restored without destroying live data.
    op.execute(
        "DELETE FROM digests AS fixture USING digests AS live "
        "WHERE fixture.topic_id = live.topic_id "
        "AND fixture.digest_date = live.digest_date "
        "AND fixture.provenance = 'fixture' AND live.provenance <> 'fixture'"
    )
    op.execute("ALTER TABLE digests DROP CONSTRAINT IF EXISTS digests_topic_date_unique")
    op.execute(
        "ALTER TABLE digests ADD CONSTRAINT digests_topic_date_unique "
        "UNIQUE (topic_id, digest_date)"
    )
    op.execute("ALTER TABLE digests DROP CONSTRAINT IF EXISTS digests_provenance_check")
    op.execute("ALTER TABLE digests DROP COLUMN IF EXISTS provenance")
    op.execute("ALTER TABLE quotes DROP CONSTRAINT IF EXISTS quotes_provenance_check")
    op.execute("ALTER TABLE quotes DROP COLUMN IF EXISTS provenance")
    op.execute("ALTER TABLE articles DROP CONSTRAINT IF EXISTS articles_provenance_check")
    op.execute("ALTER TABLE articles DROP COLUMN IF EXISTS provenance")
