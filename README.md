# Media Quotes Collection System

A rights-gated Python CLI that collects quotes from registered media sources, verifies them
against deterministic rules, and writes a daily digest, JSONL export, audit report and logs.
`mq run` collects live by default; synthetic fixtures are an explicit `--fixtures` test mode.
Paid inference remains opt-in. The original brief is immutable in
[`docs/Test_News.md`](docs/Test_News.md).

## Documentation

Start with the [documentation index](docs/README.md); it lists all seven explanatory documents
and the immutable brief.

## Requirements

- **OS:** Windows 11, macOS 13+, or a modern Linux distribution. No single OS is required.
- **Internet:** needed once to install tools and dependencies. Live collection, Dagster and
  OpenRouter need network access later.
- **Git:** to clone the repository.
- **uv:** installs and manages Python 3.12 for you. You do not need a system Python.

The repository contains the source registry (`data/sources.csv`, `data/sources/*.json`) and
synthetic fixtures for explicit tests. Cloning is enough for `mq run --fixtures`; a normal live
run needs network access. “Backfill” means historical ingestion of real articles from live
sources, which is a separate, rights-gated task.

## Fresh install — Windows, macOS or Linux

### 1. Install Git and uv

**Windows (PowerShell):**

```powershell
winget install --id Git.Git -e --source winget
winget install --id Astral-Sh.uv -e --source winget
```

**macOS (Homebrew):**

```bash
brew install git uv
```

**Linux (Debian/Ubuntu):**

```bash
sudo apt-get update && sudo apt-get install -y git curl
curl -LsSf https://astral.sh/uv/install.sh | sh
```

For other Linux distributions, follow the [uv installation guide](https://docs.astral.sh/uv/getting-started/installation/).
Restart your shell after installing so `git` and `uv` are on `PATH`.

### 2. Get the code and Python

Works the same on all three operating systems:

```bash
uv python install 3.12
git clone https://github.com/LuisGameiro/balgove_media_quotes.git balgoveinnovations
cd balgoveinnovations
```

### 3. Create local config

**Windows:** `Copy-Item .env.example .env`
**macOS/Linux:** `cp .env.example .env`

Open `.env` and set a dashboard password:

```dotenv
DASHBOARD_PASSWORD=choose-a-local-password
```

Keep `LLM_PROVIDER=local` and `PERSIST_TO_POSTGRES=false` for a free live run without database
writes. Use `mq run --fixtures` for a network-free synthetic test. Never commit `.env`.

### 4. Install dependencies and run

```bash
uv sync --frozen --all-extras --group dev
uv run python -m media_quotes doctor
uv run python -m media_quotes run --date 2026-11-23
```

For `EXTRACTION_FALLBACK=rendered`, also install the optional Playwright group:
`uv sync --group render`.

Exit code `2` is normal while sources disclose coverage gaps. Plain `mq run` fetches enabled
sources live and writes operational artifacts under `var/output/`. Use `--from-blobs` for
cached live HTML or `--fixtures` for isolated synthetic output under `var/test-output/`.

## See the data

```bash
# Windows
Get-ChildItem var/output
Get-Content var/output/quotes/quotes-2026-11-23.jsonl -First 5

# macOS/Linux
ls -R var/output
head -n 5 var/output/quotes/quotes-2026-11-23.jsonl
```

| Path | What it is |
|---|---|
| `var/output/digests/YYYY-MM-DD.md` | Human-readable digest |
| `var/output/quotes/quotes-YYYY-MM-DD.jsonl` | Verified quotes |
| `var/output/audit/audit-YYYY-MM-DD.json` | Gates, coverage gaps and version manifest |
| `var/output/logs/<run-id>.jsonl` | Correlated run logs |
| `var/output/raw/<source-id>/<sha>.html` | Immutable raw pages |

To inspect the registry: `uv run python -m media_quotes sources`.

## Dashboard

**Windows:**

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_dashboard.ps1
```

**macOS/Linux:**

```bash
uv run streamlit run src/media_quotes/dashboard.py --server.address 127.0.0.1
```

Open `http://localhost:8501` and enter the `DASHBOARD_PASSWORD` from `.env`.

## Article collection driver

`mq run` is the main driver for collecting and verifying articles. A plain run fetches every
enabled, non-blocked source live, stores the raw pages, verifies quotes and writes the digest,
JSONL export, audit report and logs under `var/output/`.

```bash
# Live collection for one report date
uv run python -m media_quotes run --date YYYY-MM-DD

# Process pages you already downloaded; no network calls
uv run python -m media_quotes run --date YYYY-MM-DD --from-blobs

# Synthetic test replay only; writes under var/test-output/
uv run python -m media_quotes run --date YYYY-MM-DD --fixtures

# Optional: download one source separately
uv run python -m media_quotes fetch --source-id bbc --live
```

Live mode never falls back to fixtures. A failed source becomes a coverage gap. To collect only
BBC and Guardian, set `enabled=false` in every other `data/sources/*.json`, then run
`uv run python -m media_quotes sources --action sync-csv` and
`uv run python -m media_quotes validate`. Fixture test mode still replays every source that has
fixtures; use a restricted `FIXTURES_DIR` when a test must cover only BBC and Guardian.

## Full collection runbook

This is the complete sequence for the brief's period: **2026-10-01 through the current date**.
Change `--to` when you run this later. Exit code `2` is expected when gaps are disclosed.

### 1. Prepare

```bash
# Windows: Copy-Item .env.example .env
# macOS/Linux:
cp .env.example .env
```

Set these in `.env`:

```dotenv
PERSIST_TO_POSTGRES=true
DASHBOARD_PASSWORD=choose-a-local-password
LLM_PROVIDER=local
OUTPUT_DIR=var/output
```

Keep `LLM_PROVIDER=local` for free, deterministic section summaries. An OpenRouter key is only
needed for paid model calls.

```bash
uv sync --frozen --all-extras --group dev
docker compose up -d --wait
uv run python -m media_quotes doctor
uv run python -m media_quotes bootstrap
uv run python -m media_quotes sources
uv run python -m media_quotes topics --action list
```

### 2. Collect the requirement period

Free offline replay from the brief's start date:

```bash
uv run python -m media_quotes backfill \
  --from 2026-10-01 --to 2026-11-24 --max-cost 400 --execute
```

Bounded live archive discovery from approved RSS/sitemap routes, then replay the stored blobs:

```bash
uv run python -m media_quotes backfill \
  --from 2026-10-01 --to 2026-11-24 --max-cost 400 --execute \
  --fetch-archive --max-archive-articles 500
```

`--max-cost` checks a flat estimate; it is not a provider-side spend cap. Tier-4 sources are
excluded. The archive stage will not discover more than the source feed/sitemap exposes and the
article cap allows.

### 3. Refresh the current day from live routes

```bash
uv run python -m media_quotes run --date 2026-11-24
uv run python -m media_quotes audit --date 2026-11-24
```

`mq fetch --source-id <id> --live` remains available when you want to download one source
separately; `mq run --from-blobs` then processes those cached pages without network access.

### 4. Add another source or topic

```bash
uv run python -m media_quotes sources --action add \
  --source-id <id> --name "<name>" --homepage-url <url> --feed-url <rss-or-sitemap-url>
# Complete rights/access/licence fields in data/sources/<id>.json and the route CSV, then:
uv run python -m media_quotes sources --action sync-csv
uv run python -m media_quotes doctor

uv run python -m media_quotes topics --action list
uv run python -m media_quotes topics --action init \
  --topic ai-policy --name "AI policy" --description "AI policy quotes"
uv run python -m media_quotes topics --action show --topic ai-policy
uv run python -m media_quotes run --date 2026-11-24 --topic ai-policy
```

A new source is not collectable until its rights and route are approved. Use `--topic ID` or
`ACTIVE_TOPIC` in `.env` to switch topics.

### 5. Verify and inspect

```bash
uv run python -m media_quotes audit --date 2026-11-24
uv run python -m media_quotes eval --gold-set synthetic-v1
uv run python -m media_quotes restore-drill --date 2026-11-24
uv run python -m scripts.source_proof --offline
uv run python -m scripts.source_proof --live --limit 5
```

Results are in `var/output/digests/`, `var/output/quotes/`, `var/output/audit/` and
`var/output/logs/`. To review missed quotes with AI, use the `audit-quotes` commands below.

### 6. Optional paid missed-quote audit

```bash
uv run python -m media_quotes audit-quotes --date 2026-11-24 --limit 20
uv run python -m media_quotes audit-quotes --date 2026-11-24 --limit 20 --max-cost 1 --execute
```

Execution requires `LLM_PROVIDER=openrouter`, a pinned `LLM_MODEL_SNAPSHOT`, `OPENROUTER_API_KEY`
and `PERSIST_TO_POSTGRES=true` with Postgres running; `--max-cost` is required with `--execute` and
is a local estimate gate, not a provider-side cap.

### 7. Dashboard and scheduler

```bash
# Windows
powershell -ExecutionPolicy Bypass -File scripts/run_dashboard.ps1
# macOS/Linux
uv run streamlit run src/media_quotes/dashboard.py --server.address 127.0.0.1

# Dagster: PowerShell users can run scripts/run_dagster.ps1 instead.
export DAGSTER_HOME=var/dagster
uv run dagster dev -m media_quotes.orchestration
```

Enable `daily_digest_schedule` and, after tier-1 rights review, `tier1_six_hourly_schedule`
(every six hours, tier 1) in the Dagster UI. Both are stopped by default and pass
`source_mode=live` explicitly for operational live collection; fixture replay is only an explicit
materialization config and writes under `var/test-output/`, never to the live graph.

For manual historical backfill, use the Dagster partition-range command in
[Backfill a date range](#backfill-a-date-range). It runs as one Dagster run and marks every
selected daily partition as materialised.

## Collecting new days

The normal flow is one command per day. Change the date to any day in your window:

```bash
uv run python -m media_quotes run --date YYYY-MM-DD
uv run python -m media_quotes audit --date YYYY-MM-DD
```

### Store results in Postgres

Docker is only needed if you want database rows. It is not needed for the file-only live run above.

```bash
docker compose up -d --wait
```

Set `PERSIST_TO_POSTGRES=true` in `.env`, then:

```bash
uv run python -m media_quotes bootstrap
uv run python -m media_quotes run --date YYYY-MM-DD
```

`mq bootstrap` applies the schema and syncs the committed source configs into Postgres. It is only
needed when you use the database; it does not download sources. Inspect rows with
`docker compose exec postgres psql -U news -d news`.

### Gather live articles

Live fetching is rights-gated and writes HTML blobs locally; it does not write quote rows.
BBC, Guardian and other configured public feeds are available. Reuters remains enabled but
`tier_block=true` because its public sitemap and direct pages return HTTP 401. Start with a plan,
then use `--live`:

```bash
uv run python -m media_quotes fetch --source-id guardian
uv run python -m media_quotes fetch --source-id guardian --live
uv run python -m media_quotes run --date YYYY-MM-DD --from-blobs
```

`--from-blobs` makes the pipeline use the downloaded pages instead of synthetic fixtures. See
[Sources](docs/03-sources.md) for the registry and T0 gate.

### Backfill a date range

A CLI backfill replays one partition per day. The first command estimates cost; `--execute` runs
it. `--max-cost` checks a flat estimate, not a provider-side spend cap. Tier-4 sources are
excluded. Plain `backfill --execute` remains fixture replay; use `--fetch-archive` or
`--from-blobs` for real articles.

```bash
uv run python -m media_quotes backfill --from 2026-10-01 --to 2026-11-23 --max-cost 10
uv run python -m media_quotes backfill --from 2026-10-01 --to 2026-11-23 --max-cost 10 --execute
uv run python -m media_quotes backfill --from 2026-10-01 --to 2026-11-23 --max-cost 10 --execute --from-blobs
# Discover dated articles from approved live RSS/sitemap routes, then replay those blobs:
uv run python -m media_quotes backfill --from 2026-10-01 --to 2026-11-23 --max-cost 10 --execute --fetch-archive --max-archive-articles 200
```

The maximum window is 365 days; archive discovery defaults to at most 200 articles.

Run the same historical operation through Dagster as one manual range backfill:

```bash
uv run dagster asset materialize -m media_quotes.orchestration --select daily_quotes_digest --partition-range 2026-10-01...2026-11-24 --config-json '{"ops":{"daily_quotes_digest":{"config":{"source_mode":"blobs","max_cost":400,"fetch_archive":true,"max_archive_articles":200,"persist_to_postgres":true}}}}'
```

Use `"fetch_archive": false` to replay existing blobs without network access. A range failure
retries the whole range. See the [command reference](docs/05-command-reference.md) for all flags.

### Change sources and topics

The source registry and topic packs are files in the repository. Edit them, then sync the CSV
contracts:

```bash
uv run python -m media_quotes sources --action list
uv run python -m media_quotes sources --action add --source-id <id> --name "<name>" --homepage-url <url>
uv run python -m media_quotes sources --action sync-csv
uv run python -m media_quotes topics --action list
uv run python -m media_quotes topics --action show --topic ukraine-war
uv run python -m media_quotes topics --action init --topic ai-policy --name ai-policy --description "AI policy"
```

Edit `data/sources/*.json`, `data/source_topic_routes.csv`, `data/topics/*.json`,
`data/topic_rules.json` and `data/sections.csv` as needed. Select a topic per command with
`--topic ID`, or set `ACTIVE_TOPIC` in `.env`. Details are in
[Sources](docs/03-sources.md) and the [command reference](docs/05-command-reference.md).

### Run on a schedule with Dagster

Extras are already installed by the fresh-install command above. Set `DAGSTER_HOME` so Dagster
loads `var/dagster/dagster.yaml`, then start the UI:

```bash
export DAGSTER_HOME=var/dagster
uv run python -m media_quotes dagster
uv run dagster dev -m media_quotes.orchestration
```

On Windows set `$env:DAGSTER_HOME = "var/dagster"` (or run `scripts/run_dagster.ps1`, which sets it
for you). Schedules and the review SLA sensor are stopped by default; enable
`daily_digest_schedule` in the Dagster UI after rights review. Set `PERSIST_TO_POSTGRES=true` to
write run rows and audit events. Concurrency limits live in `var/dagster/dagster.yaml`.

### Optional Neo4j projection

Start the local graph database and load the verified Postgres quotes:

```bash
# .env
SYNC_TO_NEO4J=true
NEO4J_USER=neo4j
NEO4J_PASSWORD=choose-a-local-password

docker compose --profile graph up -d --wait
uv run python -m media_quotes graph init
uv run python -m media_quotes graph sync --from-db
uv run python -m media_quotes graph status
uv run python -m media_quotes graph divergence
uv run python -m media_quotes graph context --max-tokens 4000
```

`graph status` reports connectivity plus node and relationship counts. In Dagster,
`neo4j_quote_projection` is a separate daily-partitioned asset downstream of
`daily_quotes_digest`. It retries independently and has three checks:
`neo4j_projection_no_errors` (no per-record errors), `neo4j_export_quotes_present` (export
membership by content hash) and `neo4j_projection_metadata_current` (freshness). It raises on a
missing or empty export or projection errors, never projects fixture replays or dry runs, and its
checks skip green for fixture or disabled partitions. Schedules explicitly use
`source_mode="live"`; an explicit fixture run safely skips the graph.

### Model-backed section summaries

Live model calls cost money. Keep `LLM_PROVIDER=local` for deterministic, free summaries. To use
OpenRouter, set a pinned model, a key and Postgres caching:

```dotenv
LLM_PROVIDER=openrouter
LLM_MODEL_SNAPSHOT=openai/gpt-4o-mini-2024-07-18
OPENROUTER_API_KEY=your-key
PERSIST_TO_POSTGRES=true
```

The model writes only 1–2 sentence section summaries. It does not change quote selection,
verification or the T0 source gate. Live fetching remains rights-gated and never bypasses the
registry check.

### Audit missed quotes with AI

An opt-in shadow command asks the model to find quotes the rules may have missed. It is free to
plan and paid only with `--execute`:

```bash
uv run python -m media_quotes audit-quotes --date YYYY-MM-DD --limit 20
uv run python -m media_quotes audit-quotes --date YYYY-MM-DD --limit 20 --max-cost 1 --execute
```

Every AI candidate is verified by the same deterministic rules. Plan mode prints JSON to stdout;
`--execute` also writes `var/output/audit/missed-quotes-YYYY-MM-DD.json`. The digest and export
are never changed. `--execute` needs `LLM_PROVIDER=openrouter`, a pinned `LLM_MODEL_SNAPSHOT`,
`OPENROUTER_API_KEY` and `PERSIST_TO_POSTGRES=true` with Postgres running (the client caches
decisions there), plus a positive `--max-cost`.

For every setting, see [`.env.example`](.env.example) and the
[command reference](docs/05-command-reference.md).

## Troubleshooting

- `docker compose up` fails: start Docker Desktop (Windows/macOS) or Docker Engine (Linux).
- Dashboard refuses to render: set `DASHBOARD_PASSWORD` in `.env` and restart the dashboard.
- Exit code `2` from `mq run`: disclosed coverage gaps, not a failure.
- `python: command not found`: use `uv run python ...` or install Python through `uv python install 3.12`.

## Quality gate

**Windows:**

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check.ps1
```

**macOS/Linux:**

```bash
uv run make check
```

This runs docs QA, format, lint, strict typing, tests with the 85% coverage floor, packaging and
CLI smoke checks. Engineering standards are in [docs/02](docs/02-architecture.md).

## Source proof report

```bash
uv run python -m scripts.source_proof --offline
uv run python -m scripts.source_proof --live --limit 5
```

Live proof reports are written to `var/output/proof/source-proof-YYYY-MM-DD.json` and `.md`.
Offline fixture proof defaults to `var/test-output/proof/`. The report distinguishes collected
live/sample data from fixture-only evidence, records `tier_block`
and blockers, and never counts fixture quotes as acquired source data. The 2026-11-24 run
collected data from 15 sources and verified 76 live quotes after blocked-source enforcement.
Seventeen sources are not blocked; 15 yielded verified quotes, Suspilne was quote-free in the
sample, and Anadolu had a route failure. GDELT and Guardian API remain discovery-only.
.
