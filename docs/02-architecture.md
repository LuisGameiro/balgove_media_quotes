# 02 — Architecture

## Stack

Python 3.12+, Pydantic v2 strict models, SQLAlchemy 2 Core with Alembic and psycopg, httpx and
trafilatura for live HTML, structlog plus OpenTelemetry for logs and traces, Dagster for daily
partitions, and Streamlit for the read-only dashboard. `uv` and `uv.lock` pin the environment.
Derived graph projections optionally run against Neo4j with the official Python driver.

## Main flow

1. `cli/run.py` loads the JSON source configs, mirrored CSV, topic pack and registry rows, then
   calls `pipeline.run_daily_pipeline`.
2. The pipeline processes fixture references, cached blobs or articles collected in the same run.
   `mq run --live` and `mq backfill --execute --live` collect from official feeds/sitemaps first
   (Bing News is an opt-in discovery fallback) and never fall back to fixtures; `mq fetch --live`
   is the separate plan/download step. Schedules request `source_mode=live` when enabled.
3. `verification_stages.py` runs a deterministic pre-filter, then `verification.py` checks the
   exact span, metadata, attribution, first-hand evidence and policy flags.
4. `curation.py` assigns sections and removes duplicates; `rights_guard.py` applies word and
   attribution limits; `digest.py` and `export.py` write the artifacts.
5. `_audit_report` produces the audit JSON/Markdown; `db/repository.py` persists rows when
   persistence is enabled.
6. `cli/app.py` dispatches commands; `orchestration.py` exposes the same pipeline as a Dagster
   asset with checks, schedules and a review sensor.

## Boundaries

- **Registry and rights.** `source_configs.py` loads `data/sources/*.json` as authoritative and
  regenerates the CSV mirror. `registry.py` validates strict rows; `usable` is the T0 gate.
  `assert_fetch_permitted` and `assert_url_permitted` enforce it at the HTTP boundary.
- **HTTP.** `fetch.py` has strict timeouts, connection limits, a 5 MB byte cap, HTTPS-only host
  allowlisting and a DNS check for live fetches. A shared token bucket spaces requests.
- **LLM.** `llm_live.py` checks the model snapshot, consults the cache before the provider,
  blocks instruction overrides and rejects refusal or incomplete output. Live OpenRouter use
  requires Postgres so decisions can be cached and audited.
- **Database.** `db/engine.py` builds the engine; `db/repository.py` owns writes. Business rows
  use idempotent upserts; audit events are append-only. Quote text and offsets are immutable.
- **Files.** `export.py` uses atomic replace for JSONL, audit and digest artifacts.
- **Provenance.** Acquisition provenance (`fixture`, `live_feed`, `live_bing`, `manual_sample`) is
  stored on articles, quotes, exports, digests and review queues; fixture artifacts are written to
  isolated paths and are never promoted or used as a live fallback.
- **Graph.** `src/media_quotes/graph/` projects verified quotes into Neo4j with idempotent MERGE
  statements. Sync failure is warning-only; Postgres remains authoritative.

## Data model

`src/media_quotes/db/schema.sql` is the primary DDL source. It defines sources, pipeline_runs,
articles, quotes, quote_classifications, obsolescence_flags, digests, audit_events,
gold_set_labels, eval_runs, decision_cache and storage_partitions. Migration `0001_initial`
executes the schema; revisions `0002`–`0006` are placeholders; `0007_widen_version` widens the
Alembic version column; `0007_audit_events_monthly_partition` partitions audit events by month;
`0008` allows enabled tier-4 rows while preserving the claim-only constraint.

The brief’s quote metadata maps to article, source and quote rows. Character offsets, DOM path,
content hashes, extractor versions, verification details, policy flags and obsolescence evidence
provide the audit trail. The strict JSONL export is optimized for LLM context packs; it is not a
second authoritative database.

## Graph projection

When `SYNC_TO_NEO4J=true`, verified quotes, sources, articles, speakers, sections and topics are
projected into Neo4j. The graph supports supersession lineage, speaker timelines, cross-tier
comparison and bounded GraphRAG context packs. It is optional and derived from Postgres.

## Config and secrets

`config.py` loads `.env` with pydantic-settings. Secrets are `SecretStr`. Paths resolve against
the project root. Validators reject unpinned model snapshots and unknown modes.
`observability._redact` keeps sensitive keys out of logs; `license_reference` remains audit
evidence. Observability is configured once at the shared pipeline entry point.

## Background work

Dagster materialises `daily_quotes_digest` from 2026-10-01. The daily 06:00 UTC schedule, the
six-hour Tier-1 schedule and the review SLA sensor are all stopped by default. When enabled, the
daily and Tier-1 schedules issue `RunRequest`s with `source_mode=live`; a manual asset
materialisation still defaults to `fixtures`. Declared `cadence_hours` values are registry metadata,
not scheduler configuration.

The dashboard caches file reads, loads the latest audit once per render, and only executes the
selected tab. It renders run metrics, digests, logs, per-source coverage, review flags, registry
rows, a read-only JSONL quote/article browser and read-only Postgres counts. The Quotes tab loads
the newest 500 governed records for the selected topic; article bodies are not part of the export.
With Neo4j enabled, the Data tab shows projection element counts.

## Quality gates

`make check` runs docs QA, ruff format and lint, `mypy --strict src/`, pytest with 85% branch
coverage, the package build and CLI smoke tests. CI adds gitleaks and `uv lock --check`. Tests
inject HTTP transports and fake LLM clients; unit tests do not require network access.

`mq audit-quotes` is a paid shadow audit that defaults to `live_feed` blobs and accepts
`--provenance`. Gold sets carry provenance and support `blob://` article refs so evaluation can run
on collected text; `mq eval --full` fails closed on fixture-only evidence, missing human or
obsolete labels, or uncalibrated judge agreement.

## Known limits

- Live historical archive discovery is implemented (bounded RSS/sitemap recursion, date filtering,
  resumable manifests, 365-day cap), but an end-to-end 2026-10-01 production backfill has not been
  executed.
- Route approval is not runtime health: all 28 sources are enabled, 11 were live-verified on
  2026-11-24, Reuters returned HTTP 401, and several other approved routes are stale, partial
  or unsupported.
- Daily obsolescence proposals are wired, but held-out human labels and production SLA evidence
  are still pending.
- Digest rendering normalises quote whitespace; strict formatting preservation is incomplete.
- Licence territory is metadata only by the decision log; it is not enforced by the renderer.
- PDF extraction exists but is not yet validated across production source routes; live responses
  are capped at 5 MB.
- The dashboard quote browser is capped at the newest 500 records and shows article metadata and
  links, not article bodies.

These are tracked in [06-whats-left.md](06-whats-left.md). Decision rationale is in
[07-decision-log.md](07-decision-log.md).

## Load assumed

One operator at a CLI, one Dagster daemon, one shared Postgres and a few dashboard viewers.
This is not a multi-user web service.
