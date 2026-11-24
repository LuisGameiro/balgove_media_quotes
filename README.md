# Media Quotes Collection System

Implementation of the media-quotes system. The source brief is
[`Test_News.md`](docs/Test_News.md). Six clean documents explain the code and the remaining
work; see the [documentation index](docs/README.md).

## Implemented today vs planned

| Area | Implemented today | Planned |
|---|---|---|
| Postgres schema + T0 gate | `src/media_quotes/db/schema.sql`, config, strict models, 12 tables; migration 0007 adds monthly `audit_events` partitions | Rehearse the migration rollback and detach cold partitions |
| Bootstrap | `python -m media_quotes bootstrap` (Alembic `upgrade head` + source sync) | Production migration rollback drills |
| CLI | 21 `mq` subcommands: offline fixture replay plus explicit, rights-gated `mq fetch`, `mq batch` and `mq blob-prune` | Cut commands over to live Postgres/Dagster backends |
| Export | `src/media_quotes/export.py`; `mq export --from-db` rebuilds the export from Postgres | Context packs from live Postgres with token budgets |
| Logs / traces | JSON logging, structlog redaction, OTLP/HTTP tracing configured once for CLI and Dagster runs | Deploy a production OTLP collector |
| Tests | 722 pytest tests, injected transports, fake LLMs, ~86% branch coverage (85% gate) | Real backfill cost validation and live load tests |
| Quality tooling | Alembic migrations, committed `uv.lock`, `Makefile` (`make check`), GitHub Actions CI, ruff/mypy/pytest, `mq doctor` | Asset bloat monitoring in Dagster asset checks |

All 28 sources are licensed (attested 2026-11-23) for machine retrieval, storage,
LLM grounding, verbatim quotes and the historical backfill. Reuters and BBC stay mandatory and
`license_held`. The T0 gate remains in code as a fail-closed guard: a `sources` row cannot be
enabled unless `rights_status` ∈ {`license_held`, `api_permitted`} and a `license_reference`
is recorded. Tier-4 state-controlled outlets stay disabled and `claim_only`. Offline
runs still use synthetic fixtures for deterministic replay; audits no longer report rights
gaps. The digest stays internal-only. Per-source detail: [`docs/03`](docs/03-sources.md).

## Layout

```
src/media_quotes/
  config.py          # Pydantic settings (env/.env only; REPORT_DATE, BACKFILL_START, …)
  models.py          # Strict boundary schemas
  enums.py           # Canonical enums (rights_status, access_mode, source_type, …)
  text_utils.py      # NFC + smart-quote + whitespace normalisation, exact-span check
  observability.py   # JSON logging, optional OTel traces
  adapters/          # Permission-gated adapters package: base, blob cache, RSS/Sitemap, HTML, quotes, registry
  topics.py          # Topic packs: sections, keywords, relevance rules, scaffold/load helpers
  relevance.py       # Deterministic, versioned topic relevance rule engine
  source_configs.py  # File-per-source JSON config, registry projection and CSV sync
  source_topics.py   # Strict CSV route loader and topic-route validation
  language.py        # Offline en/uk/ru/und quote-language detection
  translation.py     # Optional pinned-boundary translation with deterministic validation
  judge.py           # Unified AI-judge contract; off/shadow/advisory/decision modes
  live.py            # Shared live feed + article fetch loop used by mq fetch and the diagnostic script
  export.py          # Derived JSONL export with strict schema validation
  verification.py    # Deterministic quote verification
  verification_stages.py # Two-stage verification: pre-LLM filter + deterministic gate
  curation.py        # Section assignment, cross-outlet dedup and review badges
  gold_set.py        # Versioned gold set loading and deterministic 60/20/20 splits
  evaluation.py      # Pure evaluation thresholds contract
  fetch.py           # Rate-limited, rights-gated HTTP client policy
  extraction.py      # Trafilatura DOM-aware extraction and quality gate
  llm.py             # Inference provider protocol and local stand-in
  llm_live.py        # Pinned OpenAI client with structured outputs and decision caching
  llm_cache.py       # Postgres-backed decision cache hooks for the pinned LLM boundary
  llm_openrouter.py  # OpenRouter provider built on the same pinned boundary
  batch.py           # OpenRouter batch request builder wired to mq batch (dry-run default)
  batch_runner.py    # OpenRouter batch submission, polling and response retrieval
  rights_guard.py    # Word limits and attribution policy guard
  restore_drill.py   # Export integrity verification and retention readiness
  digest.py          # Deterministic Markdown renderer
  dashboard.py       # Streamlit read-only dashboard
  exceptions.py      # Domain exception hierarchy
  pipeline.py        # Shared daily collection/audit service used by CLI and Dagster
  orchestration.py   # Dagster assets, jobs, 6 checks, resources, schedule and SLA sensor
  __main__.py        # python -m media_quotes (CLI entry point, UTF-8 console fix)
  bootstrap.py       # Alembic upgrade head + source registry sync
  blob_prune.py      # Manifest-driven blob pruning (age + tombstone required)
  cli/               # mq command-line interface package (21 subcommands)
  risk_controls/     # Policy package: review queue, budget gate, retention
  validation/        # Data-asset validation package: sources, sections, quotes, modular configs
  db/                # schema.sql (DDL source), engine.py, tables.py, repository.py
docs/                # Requirements, architecture, sources, repo structure, command reference, remaining work
data/                # Registry, frozen schema, derived JSONL export, templates (no Python)
tests/               # pytest suite incl. tests/contract/test_docs_qa.py; network paths use injected transports
scripts/             # docs_qa.py, validate_data.py, check.ps1, run_dagster.ps1, run_dashboard.ps1, manual_validate.ps1, test_scraper.py
alembic.ini          # Alembic config; database URL comes from Settings, not this file
otelcol-config.yaml  # Local OTLP/HTTP collector pipeline
migrations/          # Alembic env.py, script.py.mako and versions/
  versions/0001_initial.py        # initial schema
  versions/0002_gold_set.py       # no-op (schema.sql is the single DDL source)
  versions/0003_decision_cache.py # no-op (schema.sql is the single DDL source)
  versions/0004_topic_language_judge.py # no-op (schema.sql is the single DDL source)
  versions/0005_topic_packs.py    # no-op (schema.sql is the single DDL source)
  versions/0006_digest_topic_unique.py # no-op (schema.sql is the single DDL source)
  versions/0007_audit_events_monthly_partition.py # monthly partitioning (all DDL in this file)
var/output/          # Generated artifacts under one root (gitignored)
  digests/           # YYYY-MM-DD.md (human-readable digest)
  quotes/            # quotes-YYYY-MM-DD.jsonl (schema-validated export)
  audit/             # audit-YYYY-MM-DD.json and .md
  logs/              # <run-id>.jsonl
  raw/               # <source-id>/<sha>.html (immutable raw snapshots)
```

All generated files live under `OUTPUT_DIR` (`var/output/` by default). The individual `*_DIR`
settings still override individual folders if needed. Daily MD files use the canonical
name `digests/YYYY-MM-DD.md` inside the digest root. `data/reports/` is a legacy compatibility
directory only.

## Quick start

**PowerShell / Windows (recommended commands):**

```powershell
Copy-Item .env.example .env        # local dev defaults; replace secrets for real deployments
py -m pip install uv              # only if `uv` is not on PATH; or use the official PowerShell installer
uv sync --frozen --group dev
docker compose up -d --wait         # local Postgres and OTLP/HTTP collector
python -m media_quotes bootstrap   # Alembic `upgrade head`, then the registry sync
python -m media_quotes doctor
python -m media_quotes run --date 2026-11-23
pytest
```

Run `python -m media_quotes run --date YYYY-MM-DD` with any date. Exit code `2` means a
coverage gap is disclosed (including tier-4 outlets disabled by policy or sources with no
articles found); rights gaps are resolved because all sources are licensed.

### Small config

For an offline local demo, only these `.env` values matter:

```dotenv
OUTPUT_DIR=var/output
PERSIST_TO_POSTGRES=true
LLM_PROVIDER=local
DASHBOARD_PASSWORD=choose-a-local-password
```

Leave `OPENROUTER_API_KEY` empty and keep `LLM_PROVIDER=local`; that makes the run free and
offline. Individual `DIGEST_DIR`, `BLOB_DIR`, `AUDIT_DIR`, `EXPORT_DIR` and `LOGS_DIR` values
still override the shared root if you need them.

### How to see the data

Everything is under one root:

```powershell
Get-ChildItem var/output
```

| File | What it is |
|---|---|
| `var/output/digests/YYYY-MM-DD.md` | Daily digest (human-readable) |
| `var/output/quotes/quotes-YYYY-MM-DD.jsonl` | Verified quotes (machine-readable) |
| `var/output/audit/audit-YYYY-MM-DD.json` / `.md` | Audit report and coverage gaps |
| `var/output/logs/<run-id>.jsonl` | Run logs |
| `var/output/raw/<source-id>/<sha>.html` | Immutable raw pages |

Useful commands:

```powershell
Get-Content var/output/quotes/quotes-2026-11-23.jsonl -First 5   # first verified quotes
python -m media_quotes audit --date 2026-11-23                 # gate summary
python -m media_quotes sources                                  # registry / T0 state
python -m media_quotes logs --run-id <run-id>                  # one correlated run
docker compose exec postgres psql -U news -d news              # query sources, articles, quotes
```

Start the dashboard with `powershell -ExecutionPolicy Bypass -File scripts/run_dashboard.ps1`,
open `http://localhost:8501`, and enter the `DASHBOARD_PASSWORD` from `.env`.

If `uv` is not installed and the project virtualenv is already active, skip the `uv` line and
use `python` directly. For an env-var-only flag in PowerShell, use `$env:NAME="value"`, for
example:

```powershell
$env:PERSIST_TO_POSTGRES = "true"
python -m media_quotes run --date 2026-11-19
```

Equivalent Bash/macOS form for the same flag: `PERSIST_TO_POSTGRES=true python -m media_quotes run --date 2026-11-19`.

## OpenRouter inference

Inference provider selection is environment-driven; there is no CLI flag. The default is
`LLM_PROVIDER=local`, which is deterministic and makes no network call. Set
`LLM_PROVIDER=openrouter` to generate section summaries through OpenRouter's
OpenAI-compatible Chat Completions API. Real keys belong only in `.env`, never in source code.

```powershell
$env:LLM_PROVIDER = "openrouter"
$env:LLM_MODEL_SNAPSHOT = "openai/gpt-4o-mini-2024-07-18"
$env:OPENROUTER_API_KEY = "your-key"
python -m media_quotes run --date 2026-11-19
```

`LLM_MODEL_SNAPSHOT` must be an explicit OpenRouter model id, not `unset` or `latest`.
`OPENROUTER_BASE_URL`, `OPENROUTER_APP_URL`, `OPENROUTER_APP_TITLE`,
`OPENROUTER_TIMEOUT_SECONDS`, and `OPENROUTER_MAX_RETRIES` are also configurable.
OpenRouter never changes quote selection, deterministic verification, or the T0 source gate.

Extras: `dashboard` (Streamlit), `orchestration` (Dagster), `extraction`
(trafilatura; `httpx` is in core), `llm` (OpenAI). Dev/test tooling is in the `dev`, `test` and
`lint` dependency groups. Markdown rendering is built into `digest.py` — no template engine.
`uv.lock` is committed; CI/prod use `uv sync --frozen --all-extras`. Engineering standards (ruff, `mypy --strict`, 85%
coverage, Alembic, secrets redaction) are specified in
[`docs/02`](docs/02-architecture.md).

## Command interface

The implemented entry point is `mq` (`python -m media_quotes` / `python -m media_quotes.cli`).
All subcommands below use deterministic offline fixture replay, except `mq fetch --live` and
`mq batch --execute`, which make network calls. Live fetching is rights-gated and writes no
database rows. Full command examples live in
[`docs/05`](docs/05-command-reference.md).

| Command | Purpose |
|---|---|
| `mq doctor` | Audit config, T0 registry, schema, versions, templates, DB; no network |
| `mq validate` | Validate `sources.csv`, frozen JSON Schema and derived JSONL paths |
| `mq run --date YYYY-MM-DD` | Full pipeline **plus** audit report and log summary in one command |
| `mq audit [--date …] [--run-id …]` | T0/coverage/schema/false-quote/exact-match/first-hand checks → JSON + MD in `var/output/audit/` |
| `mq logs --run-id ID [--trace-id ID]` | Correlated structured logs with `trace_id` |
| `mq eval --gold-set <version> [--split …]` | Deterministic metrics + κ-calibrated judge (optional `all|train|dev|test` filter) |
| `mq sources`, `mq topics`, `mq version` | Registry + rights status, topic-pack management, version manifest |
| `mq fetch --source-id <id> [--live]` | Plan a rights-cleared fetch; add `--live` to actually contact the source |
| `mq backfill`, `mq batch`, `mq digest`, `mq export`, `mq dashboard` | Backfill estimate, OpenRouter batch with estimated-cost check, regenerate MD, derived JSONL, Streamlit UI |
| `mq dagster` | Show Dagster asset, partition, schedule and persistence guidance |
| `mq review`, `mq retention`, `mq restore-drill` | Review queue (`list|seed|decide`), retention plan, derived-export integrity |
| `mq blob-prune --tombstones PATH [--execute]` | Delete old blobs only when their digest is in the manifest; dry-run by default |

Shared flags: `--topic`, `--dry-run`, `--date`, `--run-id`/`--trace-id`, `--out`, `--verbose`.
`--offline` is deprecated and accepted only for compatibility: every `mq` command is offline
unless you explicitly run a live command. Exit codes: `0` pass · `1`
validation/threshold failure · `2` partial success with coverage gaps · `3` config/T0-gate error.
Re-runs are idempotent.

`make docs-qa`, `make format`, `make lint`, `make typecheck`, `make test`, `make coverage`,
`make build`, `make smoke` and `make check` (the full gate, using `coverage`) wrap the local
quality workflow; the architecture and quality standards are specified in
[`docs/02`](docs/02-architecture.md).

**Windows / PowerShell:** `make` is not available by default. Run the same gate with
`powershell -ExecutionPolicy Bypass -File scripts/check.ps1`, or install the one-time `check`
function with `powershell -ExecutionPolicy Bypass -File scripts/install_check_profile.ps1`
(adds it to both Windows PowerShell and PowerShell 7 profiles) and then just run `check` from
the project root (or `check C:\path\to\balgoveinnovations` from anywhere).

**Manual quote validation without Dagster:**

```powershell
powershell -ExecutionPolicy Bypass -File scripts/manual_validate.ps1 -Date 2026-11-19
```

This runs the same `_collect` pipeline as the Dagster asset and prints the audit result.
Use `-DryRun` for planning only or `-Persist` to write Postgres rows.

## Streamlit Community Cloud deployment

The app is a Streamlit dashboard at `src/media_quotes/dashboard.py`. Streamlit Community Cloud
requires the code to live in a published GitHub repository. The repository already has an `origin`
remote; deployment requires:

1. Commit and push the current `main` branch.
2. In Streamlit Cloud, choose that repository, branch `main`, and main file path
   `src/media_quotes/dashboard.py`.
3. Use the root `requirements.txt`, which installs the project with all extras.
4. Set the `DASHBOARD_PASSWORD` secret in Streamlit Cloud. The dashboard refuses to render
   internal digests, audit reports or licence metadata without it.

The dashboard reads local replay artifacts and does not require a database or API key.

## Dates

`BACKFILL_START=2026-10-01`; `REPORT_DATE` defaults to today; `REPORT_WINDOW_DAYS=14`.
For example, on `2026-11-23` the rolling 14-day window covers `10–23 Nov 2026`.

## Source registry and adapters

The authoritative source definitions are the JSON files in `data/sources/<source_id>.json`
(explained in [`docs/03-sources.md`](docs/03-sources.md)), mirrored to `data/sources.csv` and
`data/source_topic_routes.csv`. At bootstrap they are validated into `SourceRegistryRow` objects
and synced into the Postgres `sources` table. The schema enforces the T0 rights gate: a source
cannot be enabled unless rights are approved, a licence reference exists, access mode is
permitted and discovery is enumerable.

Per-source behaviour lives in the `src/media_quotes/adapters/` package — permission-gated
adapters selected by `discovery_method` (`sitemap`, `rss`, `api`, `gdelt`, `licensed_database`,
`web`) with optional per-source plugins. All 28 sources are licensed and 23 pass the T0 gate;
the 5 tier-4 state-controlled outlets stay disabled as `claim_only`. `mq fetch` rejects any
unlicensed row with exit code 3 (via the internal `SourceNotPermitted` guard).
Per-source discovered/quotes counts are written to coverage rows in the audit report and to
`audit_events`; `pipeline_runs.stats` holds run-level totals.

## Validation commands

| Goal | Command | State |
|---|---|---|
| Documentation consistency  | `python scripts/docs_qa.py` or `pytest tests/contract/test_docs_qa.py` | works today |
| Unit/full test suite | `python -m pytest -q` | works today; targeted and full runs stay fast |
| Full gate with 85% coverage | `make coverage` or `make check` | coverage is enforced only in these full-gate commands |
| Lint/format | `python -m ruff check .` / `ruff format --check .` | works today |
| Registry/schema/data checks | `python scripts/validate_data.py` or `mq doctor` | works today |

`pytest` does not enable coverage by default. Running a targeted test file will not trigger
the global 85% threshold. `make coverage` runs the full suite with coverage and enforces the
floor defined once in `[tool.coverage.report]`. `make check` is the canonical CI gate.
See [docs/02-architecture.md](docs/02-architecture.md) for the engineering standard.

## Design invariants

1. **Quotes are never trusted from an LLM** — the deterministic span verifier must pass.
2. **Decisions are cached by content hash + model/prompt/schema versions**, not by `temperature=0`.
3. **Postgres is the system of record**; JSONL is a derived export.
4. **No source is contacted until its rights record permits the exact use case** — all sources
   are licensed now; the T0 gate still refuses any future unlicensed row.
5. **Validation is strict at every boundary**; schemas and prompts are frozen with snapshots.
6. **One OTel trace per article** with `trace_id` propagated to logs and the dashboard.
7. **Eval is built early** against a labelled gold set: 60/20/20 split, recall ≥ 0.90,
   exact-match 100%, false-quote 0, judge κ ≥ 0.70.
8. **Every action is reachable through one easy command** (`mq`) for audits and logs.

## Glossary

The documentation index is [`docs/README.md`](docs/README.md). Key notation: "T0 gate" means
the rights gate in the brief; task `T0` is the first task in that brief.
