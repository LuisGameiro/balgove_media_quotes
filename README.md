# Media Quotes Collection System

A rights-gated Python CLI that collects quotes from registered media sources, verifies them
against deterministic rules, and writes a daily digest, JSONL export, audit report and logs.
It runs offline with synthetic fixtures by default; live fetching and paid inference are explicit
opt-ins. The original brief is immutable in [`docs/Test_News.md`](docs/Test_News.md).

## Documentation

Start with the [documentation index](docs/README.md). Key references:

| Doc | Use it for |
|---|---|
| [Requirements](docs/01-requirements.md) | Guarantees, outputs and acceptance targets |
| [Architecture](docs/02-architecture.md) | Data flow, settings and quality standards |
| [Sources](docs/03-sources.md) | Registry, tiers and the T0 rights gate |
| [Repository structure](docs/04-repository-structure.md) | Where each file and artifact belongs |
| [Command reference](docs/05-command-reference.md) | All commands, flags and configuration |
| [What is left](docs/06-whats-left.md) | Remaining production work |
| [Decision log](docs/07-decision-log.md) | Why the design is this way |

## Requirements

- **OS:** Windows 11, macOS 13+, or a modern Linux distribution. No single OS is required.
- **Internet:** needed once to install tools and dependencies. Live collection, Dagster and
  OpenRouter need network access later.
- **Git:** to clone the repository.
- **uv:** installs and manages Python 3.12 for you. You do not need a system Python.

The repository already contains the source registry (`data/sources.csv`, `data/sources/*.json`)
and synthetic fixture articles for offline replay. Cloning the repo is enough for a local run —
there is nothing to download or backfill first. “Backfill” in this project means historical
ingestion of real articles from live sources, which is a separate, rights-gated task.

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

Keep `LLM_PROVIDER=local` and `PERSIST_TO_POSTGRES=false` for a free, offline run. Never commit
`.env`.

### 4. Install dependencies and run

```bash
uv sync --frozen --extra dashboard --extra extraction --group dev
uv run python -m media_quotes doctor
uv run python -m media_quotes run --date 2026-11-23
```

Exit code `2` is normal while sources disclose coverage gaps. The run writes everything under
`var/output/`; no database or network access is needed.

## See the data

```bash
# Windows
Get-ChildItem var/output
Get-Content var/output/quotes/2026-11-23.jsonl -First 5

# macOS/Linux
ls -R var/output
head -n 5 var/output/quotes/2026-11-23.jsonl
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
The **Quotes** tab is a read-only, topic-scoped browser over the governed JSONL exports; it
shows quote text and article metadata/links, not article bodies.

## Collecting new days

The normal flow is one command per day. Change the date to any day in your window:

```bash
uv run python -m media_quotes run --date YYYY-MM-DD
uv run python -m media_quotes audit --date YYYY-MM-DD
```

### Store results in Postgres

Docker is only needed if you want database rows. It is not needed for the offline run above.

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

Live collection is rights-gated. `mq run --date YYYY-MM-DD --live` collects current articles from
each usable source's official feed or sitemap first; Bing News is used only as an opt-in discovery
fallback. Live mode never falls back to fixtures — if no usable live route yields articles, the run
reports a coverage gap rather than replaying synthetic data. Provenance is recorded per article,
quote, export, digest and review flag as `fixture`, `live_feed`, `live_bing` or `manual_sample`,
and fixture artifacts are isolated from live outputs. `mq fetch` is the separate plan/download step
that writes HTML blobs locally; it does not write quote rows:

```bash
uv run python -m media_quotes fetch --source-id guardian
uv run python -m media_quotes fetch --source-id guardian --live
uv run python -m media_quotes run --date YYYY-MM-DD --live
uv run python -m media_quotes run --date YYYY-MM-DD --from-blobs
```

`--from-blobs` ingests pages already downloaded by `fetch`; it does not contact a source. See
[Sources](docs/03-sources.md) for the registry and T0 gate.

### Backfill a date range

A backfill replays one partition per day. The first command estimates cost; `--execute` runs it.
`--max-cost` checks a flat estimate, not a provider-side spend cap. `--live` discovers historical
articles from registered RSS/sitemap/sitemap-index archives with bounded recursion, date filtering
and resumable atomic manifests; `--source-id` restricts the run. The window is capped at 365 days
and paid historical inference fails closed. A full production backfill has not yet been executed.

```bash
uv run python -m media_quotes backfill --from 2026-10-01 --to 2026-11-23 --max-cost 10
uv run python -m media_quotes backfill --from 2026-10-01 --to 2026-11-23 --max-cost 10 --execute --live --source-id bbc
uv run python -m media_quotes backfill --from 2026-10-01 --to 2026-11-23 --max-cost 10 --execute --from-blobs
```

See the [command reference](docs/05-command-reference.md) for all flags.

### Change sources and topics

The source registry and topic packs are files in the repository. Edit them, then sync the CSV
contracts:

```bash
uv run python -m media_quotes sources --action list
uv run python -m media_quotes sources --action add --source-id <id> --name "<name>" --homepage-url <url>
uv run python -m media_quotes sources --action sync-csv
uv run python -m media_quotes topics --action list
uv run python -m media_quotes topics --action show --topic ukraine-war
uv run python -m media_quotes topics --action init --name ai-policy --description "AI policy"
```

Edit `data/sources/*.json`, `data/source_topic_routes.csv`, `data/topics/*.json`,
`data/topic_rules.json` and `data/sections.csv` as needed. Select a topic per command with
`--topic ID`, or set `ACTIVE_TOPIC` in `.env`. Details are in
[Sources](docs/03-sources.md) and the [command reference](docs/05-command-reference.md).

### Run on a schedule with Dagster

Install the orchestration extra, then start the UI:

```bash
uv sync --frozen --extra orchestration --group dev
uv run python -m media_quotes dagster
uv run dagster dev -m media_quotes.orchestration
```

On Windows you can also run `scripts/run_dagster.ps1`. Schedules and the review SLA sensor are
stopped by default; when enabled, the daily and Tier-1 schedules request `source_mode=live` rather
than fixture replay. Enable `daily_digest_schedule` in the Dagster UI after rights review. Set
`PERSIST_TO_POSTGRES=true` to write run rows and audit events. Concurrency limits live in
`var/dagster/dagster.yaml`.

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

The proof report is written to `var/output/proof/source-proof-YYYY-MM-DD.json` and `.md`.
It distinguishes live, manual-sample and fixture evidence and lists blockers per source.
