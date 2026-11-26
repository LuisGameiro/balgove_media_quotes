-- Single authoritative PostgreSQL schema for Media Quotes.
-- Bootstrap applies this file once (idempotently); there is no migration chain.
-- All timestamps are UTC. Re-running is safe and preserves existing rows.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Sources mirror the authoritative registry in data/sources.csv. docs/03-sources.md
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
    tier_block           BOOLEAN NOT NULL DEFAULT FALSE,
    blocker_reason       TEXT NOT NULL DEFAULT '',
    availability_status  TEXT NOT NULL DEFAULT 'live'
                          CHECK (availability_status IN ('live','partial_body','accessible_no_quotes','blocked','discovery_only','fixture_only')),
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
    CONSTRAINT sources_tier4_claim_only CHECK (
        tier <> 4 OR claim_only
    ),
    CONSTRAINT sources_tier_block_reason CHECK (
        NOT tier_block OR blocker_reason <> ''
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
    -- Acquisition provenance; fixture rows may be upgraded to live, never downgraded.
    provenance       TEXT NOT NULL DEFAULT 'fixture'
                     CHECK (provenance IN ('live_feed','live_bing','manual_sample','fixture')),
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
    canonical_quote_id  TEXT NOT NULL UNIQUE,  -- Q-YYYYMMDD-NNNN
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
    -- Acquisition provenance is stored per quote so an article upgrade cannot
    -- retroactively turn pre-existing fixture quote rows into live ones.
    provenance          TEXT NOT NULL DEFAULT 'fixture'
                        CHECK (provenance IN ('live_feed','live_bing','manual_sample','fixture')),
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
CREATE TABLE IF NOT EXISTS quote_classifications (
    classification_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    quote_id          UUID NOT NULL REFERENCES quotes(quote_id),
    section           TEXT NOT NULL,
    rationale         TEXT,
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
    topic_id    TEXT NOT NULL DEFAULT 'ukraine-war',
    provenance  TEXT NOT NULL DEFAULT 'fixture'
                CHECK (provenance IN ('live_feed','live_bing','manual_sample','fixture')),
    CONSTRAINT digests_topic_date_unique UNIQUE (topic_id, digest_date, provenance)
);

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

-- Gold-set labels and Postgres-backed evaluation runs.
CREATE TABLE IF NOT EXISTS gold_set_labels (
    label_id UUID PRIMARY KEY,
    gold_set_version TEXT NOT NULL,
    split TEXT NOT NULL CHECK (split IN ('train', 'dev', 'test')),
    article_ref TEXT NOT NULL,
    source_id TEXT NOT NULL,
    publication_date DATE NOT NULL,
    relevant BOOLEAN NOT NULL,
    quote_text TEXT,
    section_id TEXT,
    human_label BOOLEAN NOT NULL,
    is_negative_candidate BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_gold_set_labels_version_split
    ON gold_set_labels (gold_set_version, split);

CREATE TABLE IF NOT EXISTS eval_runs (
    eval_id UUID PRIMARY KEY,
    gold_set_version TEXT NOT NULL,
    run_id TEXT NOT NULL,
    report_date DATE NOT NULL,
    metrics JSONB NOT NULL,
    thresholds JSONB NOT NULL,
    passed BOOLEAN NOT NULL,
    trace_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_eval_runs_version
    ON eval_runs (gold_set_version, created_at DESC);

-- Content-addressed LLM decision cache and the monthly partition registry.
CREATE TABLE IF NOT EXISTS decision_cache (
    cache_key TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    model_snapshot TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    schema_hash TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    output JSONB NOT NULL,
    trace_id TEXT
);
CREATE INDEX IF NOT EXISTS decision_cache_input_idx ON decision_cache (input_hash);

CREATE TABLE IF NOT EXISTS storage_partitions (
    table_name TEXT NOT NULL,
    month DATE NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('hot', 'cold', 'detached')),
    PRIMARY KEY (table_name, month)
);

-- Convert a pre-existing plain audit_events table to monthly range partitioning.
-- Fresh databases already have the plain table created above; this block is idempotent.
DO $schema$
DECLARE
    is_partitioned boolean;
    month_start date;
BEGIN
    SELECT EXISTS (
        SELECT 1
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_partitioned_table p ON p.partrelid = c.oid
        WHERE c.relname = 'audit_events' AND n.nspname = current_schema()
    ) INTO is_partitioned;

    IF NOT is_partitioned THEN
        ALTER TABLE audit_events RENAME TO audit_events_legacy;
        ALTER SEQUENCE audit_events_event_id_seq OWNED BY NONE;
        DROP INDEX IF EXISTS audit_events_run_idx;
        DROP INDEX IF EXISTS audit_events_trace_idx;
        ALTER TABLE audit_events_legacy DROP CONSTRAINT IF EXISTS audit_events_pkey;
        ALTER TABLE audit_events_legacy DROP CONSTRAINT IF EXISTS audit_events_run_id_fkey;

        CREATE TABLE audit_events (
            event_id    BIGINT NOT NULL DEFAULT nextval('audit_events_event_id_seq'),
            run_id      UUID REFERENCES pipeline_runs(run_id),
            trace_id    TEXT,
            step        TEXT NOT NULL,
            entity_type TEXT,
            entity_id   TEXT,
            status      TEXT NOT NULL,
            details     JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (event_id, created_at)
        ) PARTITION BY RANGE (created_at);

        FOR month_start IN
            SELECT DISTINCT date_trunc('month', created_at AT TIME ZONE 'UTC')::date
            FROM audit_events_legacy
            ORDER BY 1
        LOOP
            EXECUTE format(
                'CREATE TABLE audit_events_%s PARTITION OF audit_events '
                'FOR VALUES FROM (%L) TO (%L)',
                to_char(month_start, 'YYYY_MM'),
                (month_start::timestamp AT TIME ZONE 'UTC'),
                ((month_start + INTERVAL '1 month')::timestamp AT TIME ZONE 'UTC')
            );
        END LOOP;

        CREATE TABLE audit_events_default PARTITION OF audit_events DEFAULT;
        CREATE INDEX audit_events_run_idx ON audit_events (run_id, created_at);
        CREATE INDEX audit_events_trace_idx ON audit_events (trace_id);

        INSERT INTO audit_events (
            event_id, run_id, trace_id, step, entity_type, entity_id, status, details, created_at
        )
        SELECT event_id, run_id, trace_id, step, entity_type, entity_id, status, details, created_at
        FROM audit_events_legacy;

        IF (SELECT count(*) FROM audit_events_legacy) <> (SELECT count(*) FROM audit_events) THEN
            RAISE EXCEPTION 'audit_events partition copy lost rows';
        END IF;

        PERFORM setval(
            'audit_events_event_id_seq',
            GREATEST(COALESCE((SELECT max(event_id) FROM audit_events), 1), 1)
        );
        ALTER SEQUENCE audit_events_event_id_seq OWNED BY audit_events.event_id;
        DROP TABLE audit_events_legacy;
    END IF;
END
$schema$;
