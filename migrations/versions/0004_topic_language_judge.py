"""Topic gate, language detection and translation provenance (docs/16).

Columns are additive and defaulted so existing append-only quote rows stay valid.
`matched_terms` uses JSONB, matching the normalized list storage in the export model.
The CHECK constraint is a defence-in-depth persistence guard: only topic-accepted
rows may be written as published quotes.
"""

from __future__ import annotations

from alembic import op

revision = "0004_topic_language_judge"
down_revision = "0003_decision_cache"
branch_labels = None
depends_on = None

UPGRADE = (
    """
    ALTER TABLE quotes
        ADD COLUMN IF NOT EXISTS topic_status TEXT NOT NULL DEFAULT 'accepted'
            CHECK (topic_status IN ('accepted','uncertain','rejected')),
        ADD COLUMN IF NOT EXISTS topic_rule_version TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS topic_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
        ADD COLUMN IF NOT EXISTS matched_terms JSONB NOT NULL DEFAULT '[]'::jsonb,
        ADD COLUMN IF NOT EXISTS quote_language TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS language_confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0
            CHECK (language_confidence BETWEEN 0.0 AND 1.0),
        ADD COLUMN IF NOT EXISTS quote_text_en TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS translation_model_snapshot TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS translation_prompt_version TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS translation_hash TEXT NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS translation_status TEXT NOT NULL DEFAULT ''
    """,
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'quotes_topic_accepted_only' AND conrelid = 'quotes'::regclass
        ) THEN
            ALTER TABLE quotes ADD CONSTRAINT quotes_topic_accepted_only
                CHECK (topic_status = 'accepted');
        END IF;
    END $$
    """,
    "CREATE INDEX IF NOT EXISTS quotes_topic_status_idx ON quotes (topic_status);",
    "CREATE INDEX IF NOT EXISTS quotes_language_idx ON quotes (quote_language);",
)

DOWNGRADE = (
    "DROP INDEX IF EXISTS quotes_language_idx;",
    "DROP INDEX IF EXISTS quotes_topic_status_idx;",
    "ALTER TABLE quotes DROP CONSTRAINT IF EXISTS quotes_topic_accepted_only;",
    """
    ALTER TABLE quotes
        DROP COLUMN IF EXISTS translation_status,
        DROP COLUMN IF EXISTS translation_hash,
        DROP COLUMN IF EXISTS translation_prompt_version,
        DROP COLUMN IF EXISTS translation_model_snapshot,
        DROP COLUMN IF EXISTS quote_text_en,
        DROP COLUMN IF EXISTS language_confidence,
        DROP COLUMN IF EXISTS quote_language,
        DROP COLUMN IF EXISTS matched_terms,
        DROP COLUMN IF EXISTS topic_score,
        DROP COLUMN IF EXISTS topic_rule_version,
        DROP COLUMN IF EXISTS topic_status
    """,
)


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
