# 05 — Command reference

The entry point is `mq` (or `python -m media_quotes`). Run `mq <command> --help` for exact flags.

## Commands

| Command | Purpose |
|---|---|
| `doctor`, `validate` | Check config, registry, schema, versions and derived data |
| `bootstrap` | Apply Alembic migrations and sync the source registry |
| `run` | Daily pipeline plus audit and logs (`--live` to collect, `--from-blobs` for cached HTML, `--tier 1` for Tier-1 cadence) |
| `fetch` | Rights-gated live feed fetch into `BLOB_DIR`; dry-run by default (`--live` to download) |
| `audit` | Generate or read the T0 and coverage audit report |
| `audit-quotes` | Paid shadow audit for missed quotes; defaults to `live_feed` blobs, accepts `--provenance` |
| `logs` | Inspect structured logs by run id and trace id |
| `eval` | Gold-set metrics with optional split filter; `--full` fails closed without live human-labelled evidence |
| `sources` | List registry rows and rights state; `add`, `sync-csv`, `probe` and `backfill` actions |
| `topics` | List, show or scaffold topic packs |
| `version` | Print the version manifest |
| `backfill` | Bounded historical backfill with cost estimate (`--live`, `--source-id`, `--execute`) |
| `digest` | Regenerate the Markdown digest from local records |
| `export` | Write JSONL from local or Postgres records (`--from-db`), or bounded `--context` packs |
| `dashboard` | Print dashboard launch guidance |
| `dagster` | Print Dagster asset, partition and schedule guidance |
| `review` | List or decide obsolescence review flags |
| `blob-prune` | Plan or execute (`--execute`) tombstone-gated pruning of aged raw blobs |
| `retention` | Print retention and lock readiness plan; with persistence on and no `--dry-run`, register observed partitions |
| `restore-drill` | Compare two exports by quote id and content hash |
| `batch` | OpenRouter batch dry-run, submit, status and apply |
| `graph` | Manage derived Neo4j projection (`init`, `status`, `sync`, `lineage`, `speaker`, `divergence`, `context`) |

## Command boundaries

- `run` replays fixtures by default. `--from-blobs` ingests HTML already downloaded by `fetch`;
  it does not contact a source. `--live` collects from official feeds first (Bing News is an opt-in
  discovery fallback) and never falls back to fixtures.
- `fetch --live` performs rights-gated current feed/sitemap discovery into blobs. It is not a
  historical archive backfill.
- `backfill` is capped at 365 days and supports `--source-id` filtering. Execution requires
  `--max-cost`; partitions use fixtures unless `--live` (bounded archive discovery) or
  `--from-blobs` is supplied, and paid historical inference fails closed. `--max-cost` checks a
  flat estimate, not provider spend.
- `sources --action add` scaffolds a disabled, pending-review JSON config. `add --probe --live` and
  `probe --source-id <id> --live` run bounded admission-only probes; they never grant rights or
  enable a source. `sources --action backfill --source-id <id>` needs a usable source and a positive
  `--max-cost`.
- `audit-quotes` audits `live_feed` blobs by default; `--provenance` selects another acquisition
  provenance and `--execute` makes paid calls. `eval --full` fails closed on fixture-only evidence,
  missing human/obsolete labels, or uncalibrated judge agreement.
- Dagster schedules and the review sensor are stopped by default. When enabled, the daily and
  Tier-1 schedules request `source_mode=live`; a manual asset run still defaults to fixtures.
- OpenRouter inference requires `PERSIST_TO_POSTGRES=true`, a pinned model and an API key.
- `scripts/test_scraper.py --validate-source <id> --live-routes` performs a memory-only health
  probe. It uses the registered feed URL and reports `reachable`, `empty_feed`,
  `wrong_content_type`, `invalid_xml`, `invalid_json`, `unsupported_route_type` or
  `blocked_not_probed`; it writes no blobs or database rows.

## Shared flags

`--date YYYY-MM-DD`, `--topic ID`, `--run-id ID`, `--out PATH`, `--verbose` and `--dry-run` are
accepted where relevant. `--offline` is a deprecated compatibility flag; every command is
offline unless it is an explicit live command.

Exit codes: `0` pass, `1` validation or threshold failure, `2` partial success with disclosed
coverage gaps, `3` configuration or T0-gate error. Re-runs are idempotent.

## Common sequences

```bash
# Offline sanity, no secrets or network
uv run python -m media_quotes doctor
uv run python -m media_quotes run --date 2026-11-19
uv run python -m media_quotes audit --date 2026-11-19
uv run python -m media_quotes eval --gold-set synthetic-v1
uv run python -m media_quotes review --action list
uv run python -m media_quotes retention --dry-run
uv run python -m media_quotes restore-drill

# Per-source proof: fixtures only, or live public routes with fixture fallback
uv run python -m scripts.source_proof --offline
uv run python -m scripts.source_proof --live --limit 5

# Live fetch -> ingest live blobs into pipeline
uv run python -m media_quotes fetch --source-id guardian
uv run python -m media_quotes fetch --source-id guardian --live
uv run python -m media_quotes run --date 2026-11-23 --from-blobs

# Live collection (official feed first; Bing opt-in fallback; never fixtures)
uv run python -m media_quotes run --date 2026-11-23 --live

# Bounded historical replay (estimate, then execute; add --from-blobs for fetched HTML)
uv run python -m media_quotes backfill --from 2026-11-18 --to 2026-11-20 --max-cost 50
uv run python -m media_quotes backfill --from 2026-11-18 --to 2026-11-20 --max-cost 50 --execute

# Bounded live archive backfill (estimate, then execute; --source-id restricts the run)
uv run python -m media_quotes backfill --from 2026-11-18 --to 2026-11-20 --max-cost 50 --live --source-id bbc
uv run python -m media_quotes backfill --from 2026-11-18 --to 2026-11-20 --max-cost 50 --execute --live --source-id bbc

# Admission probe (bounded, admission-only; no rights granted, source stays disabled)
uv run python -m media_quotes sources --action probe --source-id bbc --live

# Local Postgres: set PERSIST_TO_POSTGRES=true in .env before the run
docker compose up -d --wait
uv run python -m media_quotes bootstrap
uv run python -m media_quotes run --date 2026-11-19

# Derived Neo4j projection (external Neo4j at NEO4J_URI; not started by Compose)
uv sync --frozen --extra graph --group dev
uv run python -m media_quotes graph init
uv run python -m media_quotes graph sync --from-db
uv run python -m media_quotes graph speaker --name "Andrii Kovalenko"
uv run python -m media_quotes graph lineage --quote-id Q-20261123-0001
uv run python -m media_quotes graph divergence
uv run python -m media_quotes graph context --max-tokens 4000
```

With `SYNC_TO_NEO4J=true`, the dashboard's **Graph insights** tab shows connection status, last
sync time, cross-tier/region divergence, speaker timelines, immediate supersession links and
active quote context. Enabling the setting alone does not populate the graph; run `graph init`
and `graph sync` first.

## Configuration

The operator config lives in `.env`, copied from [`.env.example`](../.env.example). Every setting
is documented there; the table below lists the common ones. Key settings:

| Setting | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://news:news@127.0.0.1:5432/news` | Database connection URL |
| `PERSIST_TO_POSTGRES` | `false` | Enable database writes |
| `SYNC_TO_NEO4J` | `false` | Enable derived Neo4j graph projections |
| `NEO4J_URI` | `bolt://127.0.0.1:7687` | Neo4j Bolt connection URI |
| `NEO4J_USER` | `neo4j` | Neo4j authentication user |
| `NEO4J_PASSWORD` | `password123` | Neo4j authentication password |
| `NEO4J_DATABASE` | `neo4j` | Neo4j target database name |
| `OUTPUT_DIR` | `var/output` | Shared root for digests, quotes, audit, logs, and raw HTML |
| `ACTIVE_TOPIC` | `ukraine-war` | Active topic pack ID |
| `LLM_PROVIDER` | `local` | `local` or `openrouter` |
| `LLM_MODEL_SNAPSHOT` | `unset` | Pinned model id; required for `openrouter` |
| `OPENROUTER_API_KEY` | empty | Required for live inference and batch work |
| `BATCH_MODEL_SNAPSHOT` | empty | Pinned batch-enabled model |
| `TRANSLATION_MODE` | `off` | `off`, `detect_only`, or `translate` |
| `AI_JUDGE_MODE` | `off` | `off`, `shadow`, `advisory`, or `decision` |
| `AI_JUDGE_MODEL_SNAPSHOT` | `unset` | Pinned judge model id when judge is enabled |
| `DASHBOARD_PASSWORD` | empty | Required to view the Streamlit dashboard |
| `OTEL_ENABLED` | `false` | Enable OpenTelemetry tracing |
| `EXTRACTION_FALLBACK` | `off` | `off`, `static`, or `rendered` (optional Playwright, opt-in per source) |
| `FETCH_*` | see `.env.example` | HTTP timeout, rate, byte and connection limits |

`REPORT_DATE` defaults to today; `BACKFILL_START` is 2026-10-01 and the report window is 14 days.
Translation also requires route-level `translation_rights_confirmed=true`; Reuters and BBC do
not currently have that approval. Never put a real key in source control.

## Scripts

`scripts/run_all.ps1` runs the complete consolidated verification sequence, `scripts/check.ps1` runs the full quality gate, `scripts/run_dagster.ps1` starts Dagster,
`scripts/run_dashboard.ps1` starts Streamlit, `scripts/manual_validate.ps1` runs the full
collection/audit verification for a given date without Dagster, `scripts/test_scraper.py`
audits scraper and feed connectivity, `scripts/source_proof.py` writes the per-source JSON/
Markdown proof report, and `scripts/docs_qa.py` checks documentation consistency.

Remaining operational work is in [06-whats-left.md](06-whats-left.md).
