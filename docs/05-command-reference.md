# 05 — Command reference

The entry point is `mq` (or `python -m media_quotes`). Run `mq <command> --help` for exact flags.

## Commands

| Command | Purpose |
| --- | --- |
| `doctor`, `validate` | Check config, registry, schema and derived data |
| `bootstrap` | Apply `migrations/schema.sql` and sync the source registry |
| `run` | Daily pipeline, audit and logs (`--live`, `--from-blobs`, `--tier 1`) |
| `fetch` | Rights-gated feed discovery into blobs; dry-run by default (`--live` downloads) |
| `audit` | Generate or read the T0/coverage report; supports `--provenance` |
| `audit-quotes` | Paid shadow audit of missed quotes; `--execute` makes charged calls |
| `logs` | Inspect structured logs by run id and trace id |
| `eval` | Gold-set metrics; `--full` fails closed without human evidence |
| `sources` | List registry rows; `add`, `sync-csv`, `probe`, `backfill` actions |
| `topics` | List, show or scaffold topic packs |
| `version` | Print the version manifest |
| `backfill` | Bounded historical replay (`--from`, `--to`, `--max-cost`, `--live`, `--execute`) |
| `digest` | Regenerate the Markdown digest from local records |
| `export` | Write JSONL from local or Postgres records, or a bounded `--context` pack |
| `dashboard` | Print dashboard launch guidance |
| `dagster` | Print Dagster asset, partition and schedule guidance |
| `review` | List or decide obsolescence review flags |
| `blob-prune` | Prune old blobs; `--tombstones PATH` is required, `--execute` deletes |
| `retention` | Print the retention and lock-readiness plan |
| `restore-drill` | Compare two exports by quote id and content hash |
| `batch` | OpenRouter batch dry-run, submit, status and apply |
| `graph` | Derived Neo4j projection (`init`, `status`, `sync`, `lineage`, `speaker`, `divergence`, `context`) |

## Shared behaviour

`--date YYYY-MM-DD`, `--topic ID`, `--run-id ID`, `--out PATH`, `--verbose`, `--dry-run` and
`--provenance` are accepted where relevant. `--offline` is a deprecated compatibility flag; every
command is offline unless it is explicitly live.

Exit codes: `0` pass, `1` validation or threshold failure, `2` partial success with disclosed gaps,
`3` configuration or T0-gate error. Re-runs are idempotent.

`run` replays fixtures by default. `--from-blobs` uses pages downloaded by `fetch`; `--live`
collects from official feeds first, with Bing News as an opt-in fallback, and never uses fixtures.
Canonical daily paths belong to a complete `live_feed` run with no filter; other runs write under
`runs/<scope>/`. `backfill` is capped at 365 days, needs `--max-cost`, which is a flat
estimated-cost ceiling, not a provider spend cap. There is no `--force`.

## Common sequences

```bash
# Offline sanity
uv run python -m media_quotes doctor
uv run python -m media_quotes run --date 2026-11-19
uv run python -m media_quotes audit --date 2026-11-19
uv run python -m media_quotes eval --gold-set synthetic-v1
uv run python -m media_quotes review --action list

# Per-source proof
uv run python -m scripts.source_proof --offline
uv run python -m scripts.source_proof --live --limit 5

# Live fetch, then ingest
uv run python -m media_quotes fetch --source-id guardian --live
uv run python -m media_quotes run --date 2026-11-23 --from-blobs
uv run python -m media_quotes run --date 2026-11-23 --live

# Backfill: estimate, then execute
uv run python -m media_quotes backfill --from 2026-11-18 --to 2026-11-20 --max-cost 50
uv run python -m media_quotes backfill --from 2026-11-18 --to 2026-11-20 --max-cost 50 --execute

# Admission probe (grants no rights)
uv run python -m media_quotes sources --action probe --source-id bbc --live

# Local Postgres: set PERSIST_TO_POSTGRES=true in .env first
docker compose up -d --wait
uv run python -m media_quotes bootstrap
uv run python -m media_quotes run --date 2026-11-19

# Derived Neo4j projection (Compose graph profile, or an external NEO4J_URI)
uv sync --frozen --extra graph --group dev
uv run python -m media_quotes graph init
uv run python -m media_quotes graph sync --from-db
uv run python -m media_quotes graph context --max-tokens 4000
```

### Dagster automation

Schedules and the review sensor are stopped by default. Starting them takes two things: the matching
flag in `.env` (`ENABLE_TIER1_SCHEDULES=true` and/or `ENABLE_REVIEW_SLA_SENSOR=true`), which sets
their default status when definitions load, and the explicit switch on
`scripts/ops/enable_automation.ps1` (`-EnableSchedules` and/or `-EnableSensor`), which starts the
existing schedules and sensor. Flags alone start nothing. When enabled, the daily and Tier-1
schedules request `source_mode=live`; a manual asset run still uses fixtures.

## Configuration

Copy [`.env.example`](../.env.example) to `.env`. Common settings:

| Setting | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://news:news@127.0.0.1:5432/news` | Database connection |
| `PERSIST_TO_POSTGRES` | `false` | Enable database writes |
| `SYNC_TO_NEO4J` | `false` | Enable the derived graph projection |
| `NEO4J_URI` | `bolt://127.0.0.1:7687` | Neo4j Bolt URI |
| `NEO4J_USER` | `neo4j` | Neo4j user |
| `NEO4J_PASSWORD` | unset; required when `SYNC_TO_NEO4J=true` | Neo4j password; Compose falls back to `password123` |
| `NEO4J_DATABASE` | `neo4j` | Target database |
| `OUTPUT_DIR` | `var/output` | Shared generated-output root |
| `ACTIVE_TOPIC` | `ukraine-war` | Active topic pack |
| `LLM_PROVIDER` | `local` | `local` or `openrouter` |
| `LLM_MODEL_SNAPSHOT` | `unset` | Pinned model; required for OpenRouter |
| `OPENROUTER_API_KEY` | empty | Required for live inference and batch work |
| `TRANSLATION_MODE` | `off` | `off`, `detect_only`, `translate` |
| `AI_JUDGE_MODE` | `off` | `off`, `shadow`, `advisory`, `decision` |
| `DASHBOARD_PASSWORD` | empty | Required to view the dashboard |
| `EXTRACTION_FALLBACK` | `off` | `off`, `static`, `rendered` (`rendered` needs `uv sync --group render`) |
| `FETCH_*` | see `.env.example` | Timeout, rate, byte and connection limits |
| `ENABLE_TIER1_SCHEDULES` | `false` | Set default status to running for the daily and six-hour Tier-1 Dagster schedules |
| `ENABLE_REVIEW_SLA_SENSOR` | `false` | Set default status to running for the obsolescence review SLA sensor |

`REPORT_DATE` defaults to today, `BACKFILL_START` is 2026-10-01, and the report window is 14 days.
Translation also needs route-level `translation_rights_confirmed=true`; Reuters and BBC do not have
that approval. Never commit a real key.

## Scripts

- `scripts/ci/check.ps1` — full quality gate; `make check` is the cross-platform entry point.
- `scripts/ops/run_dagster.ps1` — starts the local Dagster UI with `DAGSTER_HOME` set.
- `scripts/ops/enable_automation.ps1` — starts the existing schedules/sensor whose `ENABLE_*` flags are
  true; pass `-EnableSchedules` and/or `-EnableSensor` explicitly.
- `scripts/ops/blob_prune_scheduled.ps1` — operator-run prune with a required tombstone file.
- `scripts/diagnostics/source_proof.py`, `scripts/diagnostics/test_scraper.py` — route evidence and
  health probes. `scripts/source_proof.py` and `scripts/test_scraper.py` are compatibility shims.
- `scripts/data/` — gold-fixture and topic-matrix generators.

Remaining work is in [06 — What is left](06-whats-left.md).
