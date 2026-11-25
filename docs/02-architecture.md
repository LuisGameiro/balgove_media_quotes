# 02 — Architecture

## Stack

Python 3.12+, Pydantic v2 strict models, SQLAlchemy 2 Core with Alembic and psycopg, httpx and
trafilatura for live HTML, structlog plus OpenTelemetry for logs and traces, Dagster for daily
partitions, and Streamlit for the read-only dashboard. `uv` and `uv.lock` pin the environment.
Derived graph projections optionally run against Neo4j with the official Python driver.

`collection.py` is the shared acquisition service for CLI, diagnostics and proof runs.
`archive.py` performs bounded historical discovery, `bing_news.py` supplies free URL discovery,
and `scripts/source_proof.py` produces the per-source evidence report.

## Main flow

1. `cli/app.py` dispatches the command; `cli/run.py` delegates daily work to
   `pipeline.run_daily_pipeline`.
2. The pipeline loads the active topic pack and registry. Plain `mq run` prefetches approved
   feeds through `collection.py`, then `_collect` processes the cached live blobs.
   `--from-blobs` skips network access; `--fixtures` explicitly replays synthetic fixtures.
   Scheduled assets explicitly use live collection.
3. The topic gate evaluates versioned anchors, supporting terms, exclusions and field weights.
   Only accepted articles continue to quote verification.
4. `verification_stages.py` runs a deterministic pre-filter, then `verification.py` checks the
   exact span, metadata, attribution, first-hand evidence and policy flags.
5. `curation.py` assigns sections and removes duplicates; `rights_guard.py` applies word and
   attribution limits.
6. The pipeline proposes obsolescence candidates, merges them into the review queue and applies
   existing flags when rendering. A quote under review stays visible with a provisional-hold
   badge during the 24-hour hold and is hidden only after a human `review --action decide`; the
   default offline judge produces no new flags.
7. `digest.py` and `export.py` write Markdown and JSONL artifacts; `_audit_report` writes
   coverage and gate evidence. Fixture runs write under `var/test-output/`; live/blob runs use
   `var/output/`. Configured Postgres persistence is skipped for fixtures unless explicitly requested.
8. `orchestration.py` exposes the same pipeline as a Dagster asset with checks, schedules and a
   review SLA sensor.

## Source proof flow

`scripts/source_proof.py` first tries the configured official feed/sitemap through
`collection.py`. If it finds no verified quotes, it queries Bing News for original publisher
URLs and fetches those pages through the same service. Sources with `tier_block=true` are not
contacted. Results are classified as `live_verified`, `partial_body`, `accessible_no_quotes`,
`discovery_only`, `blocked` or `fixture_only`. Only live, direct-URL or authorised sample quotes
count as collected data. Live proof writes under `var/output/proof/`; offline fixture proof
defaults to `var/test-output/proof/`.

## Historical backfill

`backfill.py` is the shared planning and execution service for `mq backfill` and Dagster. It
validates the date range, estimates article/call/cost volume, applies the budget gate, optionally
discovers a bounded live archive once, and executes one daily pipeline partition per date. Tier-4
sources remain excluded and live/archive modes never fall back to fixtures.

The `daily_quotes_digest` asset declares `BackfillPolicy.single_run()`. A manual Dagster
partition-range backfill therefore runs as one Dagster run, writes normal daily artifacts, and
marks every selected partition as materialised. Range runs require `max_cost`, reject dry-run and
fixture mode, and default to live archive discovery followed by blob replay.

## Boundaries

- **Registry and rights.** `source_configs.py` loads `data/sources/*.json` as authoritative and
  regenerates the CSV mirror. `registry.py` validates strict rows; `usable` is the T0 gate.
  `assert_fetch_permitted` and `assert_url_permitted` enforce it at the HTTP boundary.
- **HTTP.** `fetch.py` has strict timeouts, connection limits, a 5 MB byte cap, HTTPS-only host
  allowlisting and a DNS check for live fetches. A shared token bucket spaces requests.
- **Collection service.** `collection.py` enforces both T0 and `tier_block`, shares rate limiting,
  blob caching, redirect-host policy and structured errors across feed, URL and Bing collection.
- **Archive discovery.** `archive.py` walks approved RSS/sitemap indexes within a bounded date
  range and article budget, then stores article blobs for `backfill --fetch-archive`.
- **Discovery fallback.** `bing_news.py` supplies original publisher URLs only. Quote text still
  comes from the rights-gated publisher fetch; Bing is never treated as the quote source.
- **LLM.** `llm_live.py` checks the model snapshot, consults the cache before the provider,
  blocks instruction overrides and rejects refusal or incomplete output. Live OpenRouter use
  requires Postgres so decisions can be cached and audited.
- **Database.** `db/engine.py` builds the engine; `db/repository.py` owns writes. Business rows
  use idempotent upserts; audit events are append-only. Quote text and offsets are immutable.
- **Files.** `export.py` uses atomic replace for JSONL, audit and digest artifacts.
- **Graph.** `src/media_quotes/graph/` projects verified quotes into Neo4j with idempotent MERGE
  statements. The `mq graph` CLI bypasses `SYNC_TO_NEO4J` and connects directly, failing loudly
  (non-zero exit) on error; the Dagster `neo4j_quote_projection` asset raises on an empty export or
  projection errors. Only the post-range-backfill sync is warning-only. Postgres remains
  authoritative.

## Data model

`src/media_quotes/db/schema.sql` is the primary DDL source. It defines sources, pipeline_runs,
articles, quotes, quote_classifications, obsolescence_flags, digests, audit_events,
gold_set_labels, eval_runs, decision_cache and storage_partitions. Migration `0001_initial`
executes the schema; revisions `0002`–`0006` are no-op chain revisions. The
`0007_widen_alembic_version.py` file defines revision `0007_widen_version` and widens the Alembic
version column; `0007_audit_events_monthly_partition` partitions audit events by month;
`0008_enable_tier4` permits enabled claim-only tier-4 rows; `0009_source_availability` adds
`tier_block`, `blocker_reason` and `availability_status`.

The brief’s quote metadata maps to article, source and quote rows. Character offsets, DOM path,
content hashes, extractor versions, verification details, policy flags and obsolescence evidence
provide the audit trail. The strict JSONL export is derived from verified records and optimized
for LLM context packs; it is rebuilt from Postgres only by `mq export --from-db` and is not a
second authoritative database.

## Graph projection

When `SYNC_TO_NEO4J=true`, verified quotes, sources, articles, speakers, sections and topics are
projected into Neo4j. Dagster exposes this as the partitioned `neo4j_quote_projection` asset,
downstream of `daily_quotes_digest`, with its own retries and asset checks. The asset raises when
the export is missing or empty or projection reports errors. Its three checks
(`neo4j_projection_no_errors`, `neo4j_export_quotes_present`,
`neo4j_projection_metadata_current`) verify projection metadata, export membership by content
hash and sync freshness; they return a green skip for fixture or disabled partitions instead of
failing. Fixture replays and dry runs never write to the live graph. Historical range backfills
project the complete topic once after the range finishes. The graph supports supersession lineage,
speaker timelines, cross-tier comparison and bounded GraphRAG context packs. It is optional and
derived from Postgres; the Streamlit dashboard shows graph counts, while richer queries remain
CLI operations.

## Config and secrets

`config.py` loads `.env` with pydantic-settings. Secrets are `SecretStr`. Paths resolve against
the project root. Validators reject unpinned model snapshots and unknown modes.
`observability._redact` keeps sensitive keys out of logs; `license_reference` remains audit
evidence. Observability is configured once at the shared pipeline entry point.

## Background work

Dagster materialises `daily_quotes_digest` from 2026-10-01. The daily 06:00 UTC schedule, the
six-hour Tier-1 schedule and the review SLA sensor are all stopped by default. Both schedules
pass `source_mode=live` explicitly; fixture replay requires an explicit materialization config.
Declared `cadence_hours` values are registry metadata, not scheduler configuration.

The dashboard has four tabs: Overview, Coverage, Digests and logs, and Data. It renders review
flags, per-source coverage, latest digests, logs, registry rows, schema summaries and read-only
Postgres counts. With Neo4j enabled, the Data tab shows node and relationship counts. Artifact
reads are not cached and all tab bodies execute on each Streamlit render.

## Quality gates

`make check` runs docs QA, ruff format and lint, `mypy --strict src/`, pytest with 85% branch
coverage, the package build and CLI smoke tests. CI adds gitleaks and `uv lock --check`. Tests
inject HTTP transports and fake LLM clients; unit tests do not require network access.

## Known limits

- Bounded live archive discovery exists and the initial 2026-10-01-to-current production backfill
  has been executed; its audit is still pending.
- Obsolescence proposals are wired into the daily pipeline; live-judge accuracy, held-out human
  labels and review-SLA evidence remain open.
- The 2026-11-24 proof run covered 27 sources: 15 collected data and 76 live quotes were verified
  after `tier_block` enforcement, one source was `accessible_no_quotes`, 11 were blocked and none
  were fixture-only; Anadolu is absent from the report. Counts are point-in-time; read the
  generated report for current results.
- Route approval is not runtime health. Reuters remains blocked by HTTP 401, and non-blocked
  sources can still have transient route or quote-yield failures.
- Digest rendering normalises quote whitespace; strict formatting preservation is incomplete.
- Licence territory is recorded in config but not enforced by the renderer.
- PDF extraction exists but is not yet validated across production source routes; live responses
  are capped at 5 MB.
- The dashboard exposes row counts, not arbitrary database browsing.

These are tracked in [06-whats-left.md](06-whats-left.md). Decision rationale is in
[07-decision-log.md](07-decision-log.md).

## Load assumed

One operator at a CLI, one Dagster daemon, one shared Postgres and a few dashboard viewers.
This is not a multi-user web service.
