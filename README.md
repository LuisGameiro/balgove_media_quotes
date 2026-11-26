# Media Quotes Collection System

A rights-gated Python CLI that collects quotes from registered news sources, verifies them with
deterministic rules, and writes a daily Markdown digest, a strict JSONL export, an audit report
and correlated logs.

Offline fixture replay is the default. Live fetching, Postgres writes and paid model calls are
explicit opt-ins. The original assignment is in [`docs/Test_News.md`](docs/Test_News.md) and is
immutable. Start with the [documentation index](docs/README.md).

## Quick start

You need Git and [uv](https://docs.astral.sh/uv/getting-started/installation/), which installs
Python 3.12 for you. Docker is only needed for Postgres.

```bash
uv python install 3.12
git clone https://github.com/LuisGameiro/balgove_media_quotes.git balgoveinnovations
cd balgoveinnovations
cp .env.example .env          # Windows: Copy-Item .env.example .env
uv sync --frozen --extra dashboard --extra extraction --group dev
uv run python -m media_quotes doctor
uv run python -m media_quotes run --date 2026-11-23
```

`mq` and `python -m media_quotes` are the same entry point; both forms work everywhere in these docs.

Set `DASHBOARD_PASSWORD` in `.env` before starting the dashboard. Exit code `2` from `mq run`
means disclosed coverage gaps, not a crash. Everything is written under `var/output/`.

## What a run produces

| Artifact | Offline fixtures (default) | Complete live run |
| --- | --- | --- |
| Digest | `var/output/digests/fixtures/YYYY-MM-DD.md` | `var/output/digests/YYYY-MM-DD.md` |
| Quotes | `var/output/quotes/fixtures/quotes-YYYY-MM-DD.jsonl` | `var/output/quotes/quotes-YYYY-MM-DD.jsonl` |
| Audit | `var/output/audit/fixtures/audit-YYYY-MM-DD.json` | `var/output/audit/audit-YYYY-MM-DD.json` |
| Logs | `var/output/logs/<run-id>.jsonl` | `var/output/logs/<run-id>.jsonl` |
| Raw pages | `data/fixtures/` (read-only) | `var/output/raw/<source-id>/<sha>.html` |

Scoped runs (`--tier`, `--source-id`, `--from-blobs`, `live_bing`, `manual_sample`) write under
`var/output/*/runs/<scope>/` instead of the canonical paths.

## Modes

| Mode | Command | Network | Database |
| --- | --- | --- | --- |
| Fixture replay | `mq run --date D` | no | no |
| Blob ingest | `mq run --date D --from-blobs` | no | optional |
| Live collection | `mq run --date D --live` | official feeds first | optional |
| Backfill | `mq backfill ... --execute --live` | registered archives | optional |
| Persistent | set `PERSIST_TO_POSTGRES=true` | as above | Postgres |

Live mode never falls back to fixtures. Each article, quote, export and digest carries its
acquisition provenance (`fixture`, `live_feed`, `live_bing`, `manual_sample`). See
[Sources and rights](docs/03-sources.md).

## Common tasks

```bash
# Dashboard
uv run streamlit run src/media_quotes/dashboard.py --server.address 127.0.0.1

# Postgres (optional)
docker compose up -d --wait
uv run python -m media_quotes bootstrap
uv run python -m media_quotes run --date 2026-11-23 --live

# Live fetch, then ingest the downloaded pages
uv run python -m media_quotes fetch --source-id guardian --live
uv run python -m media_quotes run --date 2026-11-23 --from-blobs

# Backfill an estimated range, then execute it
uv run python -m media_quotes backfill --from 2026-11-18 --to 2026-11-20 --max-cost 50
uv run python -m media_quotes backfill --from 2026-11-18 --to 2026-11-20 --max-cost 50 --execute

# Registry and topics
uv run python -m media_quotes sources --action list
uv run python -m media_quotes topics --action list

# Dagster
uv sync --frozen --extra dashboard --extra extraction --extra orchestration --group dev
uv run dagster dev -m media_quotes.orchestration
```

`--max-cost` checks a flat local estimate; it does not cap provider spend. Dagster schedules and
the review sensor are stopped by default; set `ENABLE_TIER1_SCHEDULES=true` and
`ENABLE_REVIEW_SLA_SENSOR=true` in `.env`, then start the existing ones with
`scripts/ops/enable_automation.ps1 -EnableSchedules -EnableSensor`. The switches are required:
without them the script starts nothing.

## Model-backed summaries

Keep `LLM_PROVIDER=local` for deterministic, free summaries. Live inference needs a pinned model,
a key and Postgres caching:

```dotenv
LLM_PROVIDER=openrouter
LLM_MODEL_SNAPSHOT=openai/gpt-4o-mini-2024-07-18
OPENROUTER_API_KEY=your-key
PERSIST_TO_POSTGRES=true
```

The model only writes section summaries. It does not choose quotes, verify them or bypass the
source gate.

## Quality gate

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\ci\check.ps1
```

macOS/Linux (or Git Bash on Windows):

```bash
make check
```

The gate runs format and lint checks, strict typing, tests with an 85% branch-coverage floor, a
package build and CLI smoke checks. CI also checks the lockfile and scans for secrets.

## Read next

- [Requirements and current status](docs/01-requirements.md)
- [Architecture](docs/02-architecture.md)
- [Sources and rights](docs/03-sources.md)
- [Repository structure](docs/04-repository-structure.md)
- [Command reference](docs/05-command-reference.md)
- [What is left](docs/06-whats-left.md)
- [Decision log](docs/07-decision-log.md)
