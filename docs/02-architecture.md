# 02 — Architecture

## Stack

Python 3.12+, Pydantic v2 strict models, SQLAlchemy 2 Core with psycopg, httpx and trafilatura for
live HTML, structlog with OpenTelemetry for logs and traces, Dagster for daily partitions and
Streamlit for the read-only dashboard. `uv` and `uv.lock` pin the environment. Neo4j is an optional
derived projection.

## Main flow

1. `cli/run.py` loads JSON source configs, the CSV mirror, a topic pack and registry rows, then
   calls `pipeline.run_daily_pipeline`.
2. The pipeline processes fixture references, cached blobs or articles collected in the same run.
   `mq run --live` and `mq backfill ... --execute --live` use official feeds and sitemaps first;
   Bing News is an opt-in discovery fallback. `mq fetch --live` is a separate download step.
3. Verification stages apply a deterministic pre-filter, then check exact span, metadata,
   attribution, first-hand evidence and policy flags.
4. Curation assigns sections and removes duplicates; the rights guard applies word and attribution
   limits; digest and export write the artifacts. Module locations are in
   [04 — Repository structure](04-repository-structure.md).
5. `_audit_report` builds the audit JSON and Markdown; `db/repository.py` persists rows when
   persistence is enabled.
6. `cli/app.py` dispatches commands; `orchestration.py` exposes the same pipeline as a Dagster asset
   with checks, schedules and a review sensor.

The topic gate and optional topic judge are built once per collection and reused for every article.
Malformed feed XML and per-article failures are disclosed as gaps without aborting the run.

## Boundaries

- **Registry and rights.** `data/sources/*.json` is authoritative; `sources.csv` is a generated
  mirror. `registry.py` defines the T0 gate, and `src/media_quotes/ingestion/fetch.py` enforces it at the HTTP boundary.
- **HTTP.** `src/media_quotes/ingestion/fetch.py` uses timeouts, connection limits, a 5 MB byte cap, HTTPS-only host allowlists
  and DNS-validated IP pinning with the original Host and TLS SNI preserved. Redirects are rejected
  unless safe following is enabled. A shared token bucket spaces requests.
- **LLM.** `src/media_quotes/ai/llm_live.py` checks pinned snapshots, consults the decision cache, blocks obvious
  instruction overrides and rejects refusal or incomplete output. Live OpenRouter use requires
  Postgres. A failed section inference is disclosed in the digest with a fixed marker and an audit
  event; quotes and logs are kept.
- **Database.** `db/engine.py` builds the engine; `db/repository.py` owns writes. Business rows use
  idempotent upserts and audit events are append-only. Quote text and offsets are immutable.
- **Files.** `src/media_quotes/processing/export.py` uses atomic replace for JSONL, audit and digest artifacts.
- **Artifact scoping.** Canonical daily paths belong to a complete `live_feed` run with no filter.
  Scoped runs write under `<artifact-root>/runs/<scope>/` and create no canonical digest manifest.
  Fixture replay keeps its own `fixtures/` tree.
- **Provenance.** `fixture`, `live_feed`, `live_bing` and `manual_sample` travel with articles,
  quotes, exports, digests and review queues. Fixtures are never promoted to live evidence.
- **Graph.** `graph/` projects verified quotes into Neo4j with idempotent MERGE statements. With
  `SYNC_TO_NEO4J=true` a projection failure raises and fails the Dagster asset; with sync disabled
  no projection is attempted. Postgres remains authoritative.

## Data model

`migrations/schema.sql` is the single authoritative DDL file. It defines sources, pipeline_runs,
articles, quotes, quote_classifications, obsolescence_flags, digests, audit_events, gold_set_labels,
eval_runs, decision_cache and storage_partitions with their final columns and constraints.
`mq bootstrap` applies it idempotently and converts a pre-existing plain `audit_events` table into
monthly range partitions. There is no migration chain; rollback is backup and restore. Alembic still
appears in the generated `requirements.txt` as a transitive dependency of Dagster; bootstrap does
not use it.

Character offsets, DOM path, content hashes, extractor versions, verification details, policy flags
and obsolescence evidence provide the audit trail. The strict JSONL export is optimised for LLM
context packs; it is not a second database.

## Config and secrets

`config.py` loads `.env` through pydantic-settings. Secrets are `SecretStr`, paths resolve against
the project root, and validators reject unpinned model snapshots and unknown modes.
`observability._redact` keeps sensitive keys out of logs; `license_reference` is audit evidence.
Observability is configured once at the shared pipeline entry point.

## Background work and dashboard

Dagster materialises `daily_quotes_digest` from 2026-10-01. Schedules and the review sensor are
stopped by default; enabling them takes both the matching `ENABLE_*` flag and the explicit
`-EnableSchedules` / `-EnableSensor` switch on `scripts/ops/enable_automation.ps1`. When enabled,
scheduled runs request live sources; manual materialisations still use fixtures. `cadence_hours` is
registry metadata, not scheduler configuration. See
[05 — Command reference](05-command-reference.md#dagster-automation).

The dashboard caches file reads, loads the latest audit once per render and executes only the
selected tab. It shows run metrics, digests, logs, coverage, review flags, registry rows, a read-only
JSONL quote browser and read-only Postgres counts. The Quotes tab loads all governed records (the
table paginates them) and never shows article bodies. It uses an 8-hour session TTL and a
per-session lockout after five failed attempts for 15 minutes.

## Quality gate

`make check` (or `scripts/ci/check.ps1`) runs data validation, format and lint checks,
`mypy --strict src/`, pytest with an 85% branch-coverage floor, a package build and CLI smoke tests.
The pytest run uses the default selection: the `integration` marker is deselected via `addopts` in
`pyproject.toml`, so no live Postgres/feed is contacted. CI adds gitleaks and `uv lock --check`.
Tests inject HTTP transports and fake LLM clients; unit tests need no network.

`mq audit-quotes` is a paid shadow audit over `live_feed` blobs by default. `mq audit`, `mq digest`,
`mq export` and `mq restore-drill` accept `--provenance`; a reconstructed audit is labelled
`reconstructed=true`. Gold sets carry provenance and support `blob://` references, and `mq eval
--full` fails closed on fixture-only or unlabelled evidence.

## Known limits

- No completed production backfill from 2026-10-01.
- 28 sources are enabled for validation, but route approval is not runtime health. Reuters returned
  HTTP 401 on 2026-11-24; several other routes are stale or partial.
- Obsolescence proposals need held-out human labels and a proven review SLA.
- Licence territory is metadata only. PDF extraction is not validated across production routes.

Open items are in [06 — What is left](06-whats-left.md); rationale is in
[07 — Decision log](07-decision-log.md).

## Load assumed

One operator at a CLI, one Dagster daemon, one shared Postgres and a few dashboard viewers. This is
not a multi-user web service.
