# 05 — Command reference

The entry point is `mq` (or `python -m media_quotes`). Run `mq <command> --help` for exact flags.

## Commands

| Command | Purpose |
|---|---|
| `doctor`, `validate` | Check config, registry, schema, versions and derived data |
| `bootstrap` | Apply Alembic migrations and sync the source registry |
| `run` | Live-by-default daily pipeline plus audit and logs (`--from-blobs` for cached HTML, `--fixtures` for synthetic tests) |
| `fetch` | Rights-gated live feed fetch into `BLOB_DIR`; dry-run by default |
| `audit` | Generate or read the T0 and coverage audit report |
| `audit-quotes` | AI shadow audit for missed quotes; plan by default, paid with `--execute` |
| `logs` | Inspect structured logs by run id and trace id |
| `eval` | Gold-set metrics with optional split filter and `--full` mode |
| `sources` | List registry rows and rights state; scaffold or sync configs |
| `topics` | List, show or scaffold topic packs |
| `version` | Print the version manifest |
| `backfill` | Estimate, archive and execute bounded historical partitions |
| `digest` | Regenerate the Markdown digest from local records |
| `export` | Write JSONL or bounded context packs from local or Postgres records |
| `dashboard` | Print dashboard launch guidance |
| `dagster` | Print Dagster asset, partition and schedule guidance |
| `review` | List, seed or decide obsolescence review flags |
| `blob-prune` | Plan or execute tombstone-gated pruning of aged raw blobs |
| `retention` | Print retention and lock readiness plan; optionally register observed partitions |
| `restore-drill` | Compare two exports by quote id and content hash |
| `batch` | OpenRouter batch dry-run, submit, status and apply |
| `graph` | Manage derived Neo4j projection and queries |

## Important flags

| Command | Flags and defaults |
|---|---|
| `run` | `--date`, `--topic`, `--run-id`, `--from-blobs`, `--fixtures`, `--tier`, `--dry-run`; live by default |
| `fetch` | `--source-id` required, `--limit` default 2, `--live`; dry-run without `--live` |
| `backfill` | `--from`, `--to`, `--max-cost`, `--execute`, `--fetch-archive`, `--max-archive-articles` default 200, `--from-blobs` |
| `audit-quotes` | `--limit` default 20, `--max-cost` required with `--execute`, `--execute` |
| `eval` | `--gold-set`, `--split`, `--full` |
| `sources` | `--action list|add|sync-csv`, `--tier`, `--rights-status`; add also accepts `--source-id`, `--name`, `--homepage-url`, `--feed-url`, `--language`, `--country-region` |
| `topics` | `--action list|show|init`, `--name`, `--description` |
| `audit`, `digest`, `export` | `--fixtures` reads replay output under `var/test-output/`; default is operational `var/output/` |
| `export` | `--from-db`, `--context PATH`, `--max-tokens` default 4000 |
| `review` | `--action list|decide|seed` default `list`, `--quote-id`, `--verdict`, `--reason-code` |
| `blob-prune` | `--tombstones PATH` required, `--execute` |
| `retention` | `--dry-run` |
| `restore-drill` | `--reference PATH` |
| `logs` | `--run-id` required, `--trace-id` |
| `batch` | `--requests PATH`, `--max-cost`, `--execute`, `--status ID`, `--apply` |
| `graph status` | connectivity check plus node and relationship counts |
| `graph sync` | `--from-db` |
| `graph lineage` | `--quote-id` required |
| `graph speaker` | `--name` required |
| `graph divergence` | `--section` |
| `graph context` | `--section`, `--max-tokens` default 4000, `--limit` default 25 |

## Command boundaries

- `run` fetches enabled sources live by default and writes `var/output/`. It never falls back to
  fixtures when a live route fails. `--from-blobs` ingests cached HTML without network access;
  `--fixtures` explicitly replays synthetic data under `var/test-output/`. The two mode flags are
  mutually exclusive. `--dry-run` makes no network calls and writes nothing.
- `fetch --live` uses the shared collection service. It refuses `tier_block` sources, applies
  the report-date window and approved redirect hosts, and returns exit code 2 for article
  errors or zero fetched articles. Configuration, T0, tier-block and missing-feed failures return
  exit code 3. It is not a historical archive backfill.
- `backfill` is capped at 365 days and excludes tier-4 rows. Execution requires `--max-cost`.
  Fixture replay without `--fetch-archive` or `--from-blobs` writes under `var/test-output/`;
  `--from-blobs` or `--fetch-archive` uses live blobs and `var/output/`. Archive errors
  are printed but do not directly change the final exit code. `--dry-run` suppresses execution
  even when `--execute` is present.
- `sources --action add` only scaffolds a disabled, pending-review JSON config. Rights,
  archive depth and first-hand admission are manual.
- Dagster schedules and the review sensor are stopped by default. Both schedules pass
  `source_mode=live`; fixture replay needs an explicit materialization config.
- OpenRouter section inference requires `PERSIST_TO_POSTGRES=true`: the OpenRouter client reads
  and writes its content-addressed decision cache through Postgres. A pinned model and an API key
  are also required.
- `audit-quotes` is shadow-only. Plan mode prints JSON to stdout; the
  `missed-quotes-YYYY-MM-DD.json` report is written only with `--execute`. It never changes the
  digest or export. Execution additionally requires `LLM_PROVIDER=openrouter`, a pinned
  `LLM_MODEL_SNAPSHOT`, `OPENROUTER_API_KEY` and `PERSIST_TO_POSTGRES=true` (its client uses the
  same Postgres decision cache). `--max-cost` is required with `--execute` and is a local
  preflight estimate gate: execution is refused when the estimate exceeds it, but it is not a hard
  provider-side spending cap.
- `EXTRACTION_FALLBACK=rendered` needs Playwright from the optional `render` group; install it with
  `uv sync --group render` (add `--group dev` as needed) before enabling that mode.
- `scripts/test_scraper.py --validate-source <id> --live-routes` performs a memory-only health
  probe. Statuses include `reachable`, `reachable_json`, `empty_feed`, `wrong_content_type`,
  `invalid_xml`, `invalid_json`, `unsupported_route_type`, `no_route_url`, `probe_error`,
  `not_probed` and `blocked_not_probed`. It writes no blobs or database rows.
- `scripts/source_proof.py --live` uses the same collection service and skips `tier_block`
  sources before any feed or Bing request.

## Source proof flags

`python -m scripts.source_proof` accepts:

| Flag | Meaning |
|---|---|
| `--live` | Try official feeds/sitemaps and Bing-discovered original article pages |
| `--offline` | Run fixture proof only; reports `fixture_only`, not collected data; defaults to `var/test-output/proof/` |
| `--source ID` | Limit the run to one source; repeat for multiple sources |
| `--topic ID` | Topic pack to use; defaults to `ACTIVE_TOPIC` |
| `--limit N` | Maximum articles sampled per route; default 1 |
| `--url ID=URL` | Fetch one direct original article URL |
| `--sample ID=PATH` | Verify one supplied HTML/XML/JSON sample |
| `--out PATH` | Override the proof output directory |

The proof script returns 0 after writing a report, even when the report contains blockers.

## Shared flags

`--date YYYY-MM-DD`, `--topic ID`, `--run-id ID`, `--trace-id ID`, `--out PATH`, `--verbose`
and `--dry-run` are accepted where relevant. `--offline` is a deprecated compatibility flag.
`fetch` and `batch` use their own parser branches, so not every shared flag applies to them.

Exit codes: `0` pass, `1` validation or threshold failure, `2` partial success with disclosed
coverage gaps, `3` configuration or T0/tier-block error (invalid mode flags, missing pinned model
or key and other config faults now return `3` rather than `2`). Re-runs are idempotent for pipeline and
storage operations; `sources --action add` and `topics --action init` deliberately refuse
duplicate creation.

## Common sequences

```bash
# Synthetic test replay, no network
uv run python -m media_quotes doctor
uv run python -m media_quotes run --date 2026-11-19 --fixtures

# Live-by-default operational run
uv run python -m media_quotes run --date 2026-11-19
uv run python -m media_quotes audit --date 2026-11-19

# AI missed-quote audit: plan, then paid execution behind a local estimate gate
uv run python -m media_quotes audit-quotes --date 2026-11-19 --limit 20
uv run python -m media_quotes audit-quotes --date 2026-11-19 --limit 20 --max-cost 1 --execute
uv run python -m media_quotes eval --gold-set synthetic-v1
uv run python -m media_quotes review --action list
uv run python -m media_quotes retention --dry-run
uv run python -m media_quotes restore-drill

# Per-source proof: fixtures only, or live public routes plus Bing URL discovery
uv run python -m scripts.source_proof --offline
uv run python -m scripts.source_proof --live --limit 5

# Optional explicit single-source fetch -> cached ingest without network
uv run python -m media_quotes fetch --source-id guardian
uv run python -m media_quotes fetch --source-id guardian --live
uv run python -m media_quotes run --date 2026-11-23 --from-blobs

# Bounded historical replay
uv run python -m media_quotes backfill --from 2026-11-18 --to 2026-11-20 --max-cost 50
uv run python -m media_quotes backfill --from 2026-11-18 --to 2026-11-20 --max-cost 50 --execute

# Bounded live archive discovery into blobs, then replay those blobs
uv run python -m media_quotes backfill --from 2026-10-01 --to 2026-11-23 --max-cost 50 --execute --fetch-archive --max-archive-articles 200

# Local Postgres: set PERSIST_TO_POSTGRES=true in .env before the run
docker compose up -d --wait
uv run python -m media_quotes bootstrap
uv run python -m media_quotes run --date 2026-11-19

# Derived Neo4j projection
docker compose --profile graph up -d --wait
uv run python -m media_quotes graph init
uv run python -m media_quotes graph status
uv run python -m media_quotes graph sync --from-db
uv run python -m media_quotes graph speaker --name "Andrii Kovalenko"
uv run python -m media_quotes graph lineage --quote-id Q-20261123-0001
uv run python -m media_quotes graph divergence
uv run python -m media_quotes graph context --max-tokens 4000
```

With `SYNC_TO_NEO4J=true`, the dashboard’s **Data** tab shows graph node and relationship
counts. Rich divergence, speaker, lineage and context queries remain available through
`mq graph`. Enabling the setting alone does not populate the graph; run `graph init` and
`graph sync` first. In Dagster, `neo4j_quote_projection` materializes after
`daily_quotes_digest` and validates export membership plus projection metadata. Fixture-mode
Dagster runs are always skipped for graph safety.

## Dagster historical backfill

Historical backfill is manual-only and uses the same service as `mq backfill`. A range executes
as one Dagster run and marks every selected `daily_quotes_digest` partition as materialised.

```bash
python -m dagster asset materialize -m media_quotes.orchestration --select daily_quotes_digest --partition-range 2026-10-01...2026-11-24 --config-json '{"ops":{"daily_quotes_digest":{"config":{"source_mode":"blobs","max_cost":400,"fetch_archive":true,"max_archive_articles":200,"persist_to_postgres":true}}}}'
```

Range config rules:

- `max_cost` is required and gates all archive/partition work.
- `source_mode` must be `blobs`.
- `fetch_archive=true` discovers dated RSS/sitemap articles once for the range.
- `fetch_archive=false` replays existing blobs without network access.
- `dry_run`, fixture mode and tier filters are rejected for ranges.
- A range failure retries the whole range; daily writes remain idempotent.

## Configuration

The operator config lives in `.env`, copied from [`.env.example`](../.env.example). Every setting
is documented there; the table below lists the common ones.

| Setting | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://news:news@127.0.0.1:5432/news` | Database connection URL |
| `PERSIST_TO_POSTGRES` | `false` | Enable database writes |
| `SYNC_TO_NEO4J` | `false` | Enable derived Neo4j graph projections |
| `NEO4J_URI` | `bolt://127.0.0.1:7687` | Neo4j Bolt connection URI |
| `NEO4J_USER` | `neo4j` | Neo4j authentication user |
| `NEO4J_PASSWORD` | `password123` | Application setting; Compose authentication uses `NEO4J_USER`/`NEO4J_PASSWORD` |
| `NEO4J_DATABASE` | `neo4j` | Neo4j target database name |
| `OUTPUT_DIR` | `var/output` | Shared root for digests, quotes, audit, logs, raw pages and proof |
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
| `EXTRACTION_FALLBACK` | `off` | `off`, `static`, or `rendered` |
| `FETCH_*` | see `.env.example` | HTTP timeout, rate, byte and connection limits |

`REPORT_DATE` defaults to today; `BACKFILL_START` is 2026-10-01 and the report window is 14 days.
All 29 source routes currently have `translation_rights_confirmed=false`, so translation is
unavailable for every source until a route is explicitly approved. Never put a real key in
source control.

## Scripts

- `scripts/run_all.ps1` — consolidated verification; supports `-Date`, `-SkipCI` and
  `-LaunchDashboard`. Its pipeline step calls `manual_validate.ps1` without `-Persist`.
- `scripts/check.ps1` — full quality gate; `install_check_profile.ps1` installs a shell
  `check` function that delegates to it instead of duplicating the command sequence.
- `scripts/run_dagster.ps1` — starts Dagster with local `DAGSTER_HOME` and its asset/check/schedule inventory.
- `scripts/run_dashboard.ps1` — starts Streamlit.
- `scripts/manual_validate.ps1` — fixture-backed collection/audit verification for one date;
  supports `-Date`, `-DryRun` and `-OfflineMarker`. The obsolete `-Persist` switch was removed:
  fixture runs skip Postgres and never set `PERSIST_TO_POSTGRES`.
- `scripts/test_scraper.py` — feed, HTML and route diagnostics. `--write-sample PATH` generates
  realistic sample HTML; the canonical generated path is `var/test-output/samples/article.html`,
  also used in `--html-file` examples and error hints. `--validate-source` and
  `--validate-all-sources` stay offline unless `--live-routes` is passed.
- `scripts/source_proof.py` — per-source JSON/Markdown proof report. It imports the shared
  extraction/probe helpers from `src/media_quotes/diagnostics/__init__.py`, not from the CLI script.
- `scripts/docs_qa.py` — documentation consistency checks.
- `scripts/validate_data.py` — registry/schema/data validation.
- `scripts/generate_topic_matrix.py` and `scripts/generate_gold_fixtures.py` — synthetic matrix
  and fixture generation. Both accept `--check`, which recomputes the expected files, reports
  drift with exit code 1 and writes nothing; the default still writes the generated files.

Remaining operational work is in [06-whats-left.md](06-whats-left.md).
