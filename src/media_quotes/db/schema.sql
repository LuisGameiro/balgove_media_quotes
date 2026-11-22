-- Append-oriented audit schema. Alembic is the only supported way to apply it
-- (revision 0001_initial); all timestamps are UTC.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Sources mirror the authoritative registry in data/sources.csv. docs/07-sources.md
-- explains each row. The CHECK constraints are the T0 gate: a source may only be enabled
-- when rights are approved, a licence reference exists, the access mode is permitted,
-- and discovery is enumerable (not vague "web").
CREATE TABLE IF NOT EXISTS sources (
    source_id            TEXT PRIMARY KEY,
    source_name          TEXT NOT NULL,
    homepage_url         TEXT NOT NULL,
    feed_url             TEXT NOT NULL DEFAULT '',
    tier                 INTEGER NOT NULL CHECK (tier BETWEEN 1 AND 4),
    cadence_hours        INTEGER NOT NULL CHECK (cadence_hours >= 0),
    country_region       TEXT NOT NULL,
    source_type          TEXT NOT NULL CHECK (source_type IN ('independent','publicly funded','state-affiliated','state-controlled','aggregator')),
    claim_only           BOOLEAN NOT NULL,
    language             TEXT NOT NULL CHECK (language ~ '^[a-z]{2}$'),
    requires_translation BOOLEAN NOT NULL,
    paywalled            BOOLEAN,  -- NULL represents 'unknown' in the CSV
    rights_status        TEXT NOT NULL CHECK (rights_status IN ('license_held','api_permitted','pending_counsel_review','blocked')),
    access_mode          TEXT NOT NULL CHECK (access_mode IN ('licensed_database','official_api','public_rss_permitted','pending_review','scraped_prohibited')),
    license_reference    TEXT NOT NULL DEFAULT '',
    discovery_method     TEXT NOT NULL CHECK (discovery_method IN ('sitemap','rss','web','api','gdelt','licensed_database')),
    archive_start_date   DATE NOT NULL,
    enabled              BOOLEAN NOT NULL DEFAULT FALSE,
    notes                TEXT NOT NULL DEFAULT '',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT sources_t0_gate CHECK (
        NOT enabled OR (
            rights_status IN ('license_held','api_permitted')
            AND license_reference <> ''
            AND access_mode IN ('licensed_database','official_api','public_rss_permitted')
            AND discovery_method <> 'web'
        )
    ),
    CONSTRAINT sources_state_controlled_claim_only CHECK (
        source_type <> 'state-controlled' OR claim_only
    ),
    CONSTRAINT sources_tier4_disabled CHECK (
        tier <> 4 OR (enabled = FALSE AND cadence_hours = 0)
    )
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pipeline      TEXT NOT NULL,
    partition_date DATE,
    status        TEXT NOT NULL DEFAULT 'running'
                  CHECK (status IN ('running','succeeded','failed','partial')),
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    stats         JSONB NOT NULL DEFAULT '{}'::jsonb,
    topic_id      TEXT NOT NULL DEFAULT 'ukraine-war'
);
CREATE INDEX IF NOT EXISTS pipeline_runs_topic_idx ON pipeline_runs(topic_id);

CREATE TABLE IF NOT EXISTS articles (
    article_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id        TEXT NOT NULL REFERENCES sources(source_id),
    canonical_url    TEXT NOT NULL UNIQUE,
    title            TEXT,
    publication_date DATE,
    language         TEXT,
    raw_blob_ref     TEXT,
    extracted_text   TEXT,
    extraction_method TEXT,
    extractor_version TEXT,
    content_hash     TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'discovered'
                     CHECK (status IN ('discovered','fetched','extracted','verified','failed','skipped')),
    article_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    discovered_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    fetched_at       TIMESTAMPTZ,
    processed_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS articles_source_date_idx ON articles(source_id, publication_date);
CREATE INDEX IF NOT EXISTS articles_content_hash_idx ON articles(content_hash);
CREATE INDEX IF NOT EXISTS articles_text_fts_idx
    ON articles USING gin (to_tsvector('english', coalesce(extracted_text, '')));

-- Quotes are append-only. Once written, verification_status may move from
-- 'unverified' to 'verified'/'rejected', but text and offsets are immutable.
CREATE TABLE IF NOT EXISTS quotes (
    quote_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_quote_id  TEXT UNIQUE,  -- Q-YYYYMMDD-NNNN, unique when present
    article_id          UUID NOT NULL REFERENCES articles(article_id),
    quote_text_raw      TEXT NOT NULL,
    quote_text_normalised TEXT NOT NULL,
    char_start          INTEGER NOT NULL CHECK (char_start >= 0),
    char_end            INTEGER NOT NULL CHECK (char_end > char_start),
    dom_path            TEXT,
    speaker             TEXT,
    attribution_span    TEXT,
    extraction_method   TEXT NOT NULL,
    extractor_version   TEXT NOT NULL,
    verification_status TEXT NOT NULL DEFAULT 'unverified'
                        CHECK (verification_status IN ('unverified','verified','rejected')),
    verification_details JSONB NOT NULL DEFAULT '{}'::jsonb,
    quote_metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Topic/language/translation provenance (docs/16). Lists are stored as JSONB.
    topic_id            TEXT NOT NULL DEFAULT 'ukraine-war',
    topic_status        TEXT NOT NULL DEFAULT 'accepted'
                        CHECK (topic_status IN ('accepted','uncertain','rejected')),
    topic_rule_version  TEXT NOT NULL DEFAULT '',
    topic_score         DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    matched_terms       JSONB NOT NULL DEFAULT '[]'::jsonb,
    quote_language      TEXT NOT NULL DEFAULT '',
    language_confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0
                        CHECK (language_confidence BETWEEN 0.0 AND 1.0),
    quote_text_en       TEXT NOT NULL DEFAULT '',
    translation_model_snapshot TEXT NOT NULL DEFAULT '',
    translation_prompt_version TEXT NOT NULL DEFAULT '',
    translation_hash    TEXT NOT NULL DEFAULT '',
    translation_status  TEXT NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (article_id, quote_text_normalised, char_start, char_end),
    CONSTRAINT quotes_topic_accepted_only CHECK (topic_status = 'accepted')
);
CREATE INDEX IF NOT EXISTS quotes_article_idx ON quotes(article_id);
CREATE INDEX IF NOT EXISTS quotes_status_idx ON quotes(verification_status);
CREATE INDEX IF NOT EXISTS quotes_topic_status_idx ON quotes(topic_status);
CREATE INDEX IF NOT EXISTS quotes_topic_id_idx ON quotes(topic_id);
CREATE INDEX IF NOT EXISTS quotes_language_idx ON quotes(quote_language);
CREATE INDEX IF NOT EXISTS quotes_text_fts_idx
    ON quotes USING gin (to_tsvector('english', quote_text_raw));

-- Content-addressed decision cache. Key includes content + extractor + prompt +
-- schema + model snapshot + decoding params. Never rely on temperature=0 alone.
CREATE TABLE IF NOT EXISTS llm_decisions (
    decision_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    decision_type       TEXT NOT NULL,
    article_id          UUID REFERENCES articles(article_id),
    quote_id            UUID REFERENCES quotes(quote_id),
    cache_key           TEXT NOT NULL UNIQUE,
    input_hash          TEXT NOT NULL,
    model_snapshot      TEXT NOT NULL,
    prompt_version      TEXT NOT NULL,
    schema_hash         TEXT NOT NULL,
    decoding_params     JSONB NOT NULL DEFAULT '{}'::jsonb,
    input_payload       JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_payload      JSONB,
    provider_response_id TEXT,
    refusal             BOOLEAN NOT NULL DEFAULT FALSE,
    incomplete          BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS llm_decisions_article_idx ON llm_decisions(article_id);

CREATE TABLE IF NOT EXISTS quote_classifications (
    classification_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    quote_id          UUID NOT NULL REFERENCES quotes(quote_id),
    section           TEXT NOT NULL,
    rationale         TEXT,
    decision_id       UUID REFERENCES llm_decisions(decision_id),
    assigned_by       TEXT NOT NULL DEFAULT 'llm' CHECK (assigned_by IN ('llm','human','rule')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS quote_classifications_quote_idx ON quote_classifications(quote_id);

CREATE TABLE IF NOT EXISTS obsolescence_flags (
    flag_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    quote_id      UUID NOT NULL REFERENCES quotes(quote_id),
    is_obsolete   BOOLEAN NOT NULL,
    reason        TEXT,
    superseded_by UUID[] NOT NULL DEFAULT '{}',
    decision_id   UUID REFERENCES llm_decisions(decision_id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS obsolescence_flags_quote_idx ON obsolescence_flags(quote_id);

CREATE TABLE IF NOT EXISTS digests (
    digest_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    digest_date DATE NOT NULL,
    window_start DATE NOT NULL,
    window_end  DATE NOT NULL,
    md_path     TEXT NOT NULL,
    manifest    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    topic_id    TEXT NOT NULL DEFAULT 'ukraine-war'
);
-- The (topic_id, digest_date) unique constraint is added by migration 0006, so a fresh
-- install and an upgraded database end up with the same single constraint and index.

-- Append-only audit log; surrogate key, no updates.
CREATE TABLE IF NOT EXISTS audit_events (
    event_id    BIGSERIAL PRIMARY KEY,
    run_id      UUID REFERENCES pipeline_runs(run_id),
    trace_id    TEXT,
    step        TEXT NOT NULL,
    entity_type TEXT,
    entity_id   TEXT,
    status      TEXT NOT NULL,
    details     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_events_run_idx ON audit_events(run_id, created_at);
CREATE INDEX IF NOT EXISTS audit_events_trace_idx ON audit_events(trace_id);
