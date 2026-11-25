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

# Downgrade must collapse provenance-scoped manifests to the legacy two-column key.
# Survivors are deterministic: live_feed beats other provenances, and the lowest
# digest_id wins any remaining tie. Kept as data so the opt-in Postgres test can run
# the exact statements the downgrade executes.
_DOWNGRADE_DEDUPE_SQL = (
    "DELETE FROM digests AS fixture USING digests AS live "
    "WHERE fixture.topic_id = live.topic_id "
    "AND fixture.digest_date = live.digest_date "
    "AND fixture.provenance = 'fixture' AND live.provenance <> 'fixture'",
    "DELETE FROM digests AS other USING digests AS livefeed "
    "WHERE other.topic_id = livefeed.topic_id "
    "AND other.digest_date = livefeed.digest_date "
    "AND other.provenance <> 'live_feed' AND livefeed.provenance = 'live_feed'",
    "DELETE FROM digests AS loser USING digests AS keeper "
    "WHERE loser.topic_id = keeper.topic_id "
    "AND loser.digest_date = keeper.digest_date "
    "AND loser.digest_id > keeper.digest_id",
)


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
    for statement in _DOWNGRADE_DEDUPE_SQL:
        op.execute(statement)
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
