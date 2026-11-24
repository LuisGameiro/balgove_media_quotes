# 02 — Architecture

## Stack

Python 3.12+, Pydantic v2 strict models, SQLAlchemy 2 Core with Alembic and psycopg, httpx and
trafilatura for live HTML, structlog plus OpenTelemetry for logs and traces, Dagster for daily
partitions, and Streamlit for the read-only dashboard. `uv` and `uv.lock` pin the environment.
Derived graph projections optionally run against Neo4j with official Python driver protocols.

## Main flow

1. `cli/run.py` loads the registry and topic pack, then calls `pipeline.run_daily_pipeline`.
2. `SourceAdapter` discovers fixture refs, cached live blobs (`--from-blobs`), or live feeds,
   fetches bytes and parses them through `adapters/html_extractor.py` and `extraction.py`.
3. `verification_stages.py` runs a cheap pre-filter, then `verification.py` checks the exact
   span, metadata, attribution, first-hand evidence and policy flags.
4. `curation.py` assigns sections and removes cross-outlet duplicates; `rights_guard.py` applies
   word and attribution limits again; `digest.py` and `export.py` write the artifacts.
5. `_audit_report` produces the audit JSON/Markdown; `db/repository.py` persists rows when
   persistence is enabled.
6. `cli/app.py` dispatches all commands; `orchestration.py` exposes the same pipeline as a Dagster
   daily asset with checks, daily and 6-hourly Tier-1 schedules, and a review SLA sensor.

## Boundaries

- **Registry and rights.** `registry.py` validates `sources.csv` into strict rows; `usable` is the
  T0 gate. `assert_fetch_permitted` and `assert_url_permitted` enforce it at the HTTP boundary.
- **HTTP.** `fetch.py` has strict timeouts, connection limits, a byte cap, HTTPS-only host
  allowlisting and a DNS check that live fetches enable. A shared token bucket spaces requests;
  a retry loop waits for a token with the real clock and fails fast with an injected clock.
- **LLM.** `llm_live.py` is the pinned boundary. It checks the model snapshot, consults the cache
  before the provider, blocks instruction overrides in untrusted text, and rejects refusal or
  incomplete results. `llm_openrouter.py` implements the chat-completions provider; judge and
  translation use the same boundary.
- **Database.** `db/engine.py` builds the engine; `pipeline._default_session_context` reuses one
  engine per URL. `db/repository.py` contains the only writes: most use idempotent upserts, while
  audit events are append-only inserts.
- **Files.** `export.py` uses atomic replace for JSONL and audit/digest artifacts.
- **Graph.** `src/media_quotes/graph/` projects verified quotes into Neo4j via idempotent MERGE
  statements. Postgres remains the single authoritative source of record.

## Data model

`src/media_quotes/db/schema.sql` is the primary DDL source. It defines sources, pipeline_runs,
articles, quotes, quote_classifications, obsolescence_flags, digests, audit_events,
gold_set_labels, eval_runs, decision_cache and storage_partitions. Migration `0001_initial`
executes the file in full; revisions `0002`–`0006` are no-op chain placeholders; revision
`0007_audit_events_monthly_partition` converts `audit_events` to physical monthly range partitions.

## Graph projection (Neo4j)

When opt-in `SYNC_TO_NEO4J=true` is enabled, verified quotes, sources, articles, speakers,
sections, and topics are deterministically projected into Neo4j (`src/media_quotes/graph/`).
The graph schema maintains uniqueness constraints on canonical keys and indexes on temporal
and classification properties.

The graph projection captures relational topologies that are difficult in relational tables:
- **`SUPERSEDES` lineage chains**: Direct graph traversals track how claims evolve or reverse.
- **`SAID` speaker attribution**: Enables chronological speaker timelines and temporal tracking.
- **Cross-tier & regional divergence**: Correlates claims across tiers and jurisdictions.
- **Bounded GraphRAG context packs**: Subgraph extraction (`fetch_graphrag_subgraph`) retrieves
  active, non-obsolete quotes with speaker and supersession depth, bounded by token budget.

## Config and secrets

`config.py` loads `.env` with pydantic-settings. Secrets (`database_url`, API keys, dashboard
password, Neo4j password) are `SecretStr`. Paths resolve against the project root. Validators
reject unpinned model snapshots and unknown modes. `observability._redact` keeps sensitive keys
out of logs; `license_reference` is audit evidence and is deliberately not redacted.
`configure_observability` is invoked once at the pipeline entry point shared by CLI and Dagster.

## Background work

Dagster materialises `daily_quotes_digest` from 2026-10-01 with two stopped-by-default UTC
schedules: `daily_digest_schedule` (06:00 UTC for all tiers) and `tier1_six_hourly_schedule`
(every 6 hours for Tier-1 sources). Asset checks verify the export, audit gates, gap disclosure,
rights boundary, renderer limits and gold-set thresholds. `review_queue_sla_sensor` is also
stopped by default; when started, it checks the obsolescence queue every five minutes. The
dashboard uses `st.cache_data` only for read artifacts and renders both latest and multi-day
per-source coverage.

## Quality gates

`make check` runs docs QA, ruff format and lint, `mypy --strict src/`, pytest with 85% branch
coverage, the package build and CLI smoke tests. CI runs the same gate plus gitleaks and
`uv lock --check`. Tests inject HTTP transports and fake LLM clients; integration tests may use a
real local Postgres, but unit tests never need the network or a live provider.

## Load assumed

One operator at a CLI, one Dagster daemon, one shared Postgres and a few dashboard viewers.
This is not a multi-user web service.

Full commands are in [05-command-reference.md](05-command-reference.md); remaining work is in
[06-whats-left.md](06-whats-left.md).
