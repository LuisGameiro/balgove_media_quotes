# Media Quotes Collection System

Implementation scaffold for the integrated plan in [`docs/`](docs/README.md). The source
brief is [`Test_News.md`](docs/Test_News.md); two defining design choices are explained in
[`docs/06`](docs/06-observations.md), the decision/risk register is
[`docs/04`](docs/04-risks-and-open-questions.md) (owner: Balgove Innovations), and
requirement→decision→task→check mappings live in
[`docs/08`](docs/08-traceability.md). An optional Neo4j integration is planned (not adopted)
in [`docs/09`](docs/09-neo4j-integration.md); plans to close the engineering risks are in
[`docs/10`](docs/10-risk-mitigation-plan.md). The canonical whole-repo file tree is in
[`docs/11`](docs/11-repository-structure.md), and the phase-by-phase implementation cutover
plan is in [`docs/12`](docs/12-implementation-plan.md).

## Implemented today vs planned

| Area | Today (T1 foundation) | Planned |
|---|---|---|
| Postgres schema + T0 gate | `src/media_quotes/db/schema.sql`, config, strict models | Full append-only tables, coverage contract, decision cache |
| Bootstrap | `python -m media_quotes.bootstrap` (Alembic `upgrade head` + source sync) | Live Dagster pipeline scheduling |
| CLI | 19 `mq` subcommands: offline fixture replay plus an explicit, rights-gated `mq fetch` | Live Dagster/Postgres/LLM orchestration |
| Export | `src/media_quotes/export.py` with strict `ExportQuoteRecord` validation | Context packs from live Postgres (`mq export`) |
| Logs / traces | JSON logging, structlog secret redaction, optional OTLP tracing (`observability.py`) | Distributed tracing with live collectors |
| Tests | 630 pytest tests, risk-control policy tests, ~86.7% coverage (85% gate) | VCR.py live recordings, Dagster asset checks |
| Quality tooling | Alembic migrations, committed `uv.lock`, `Makefile` (`make check`), GitHub Actions CI, ruff/mypy/pytest, `mq doctor` | Continuous asset contracts |

Reuters and BBC **stay mandatory** (DEC-01) but ingestion is operationally blocked until
written rights cover machine retrieval, storage, LLM/RAG grounding and verbatim
republication; their `robots.txt` files prohibit these uses. The Postgres schema enforces
this: a `sources` row cannot be enabled unless `rights_status` ∈ {`license_held`,
`api_permitted`} and a `license_reference` is recorded. Until then the pipeline is developed
against VCR fixtures and licensed-route adapters, and audits report the blocked sources as
explicit coverage gaps. The brief permits substitution if rights cannot be obtained; this
project records that choice as a deliberate deviation (DEC-01) and reports the gap instead.
Per-source detail: [`docs/07`](docs/07-sources.md).

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
  llm_openrouter.py  # OpenRouter provider built on the same pinned boundary
  batch.py           # Provider batch API planner — design stub, no production caller
  rights_guard.py    # DEC-10 word limits and attribution policy guard
  restore_drill.py   # Export integrity verification and retention readiness
  pipeline.py        # shared daily collection/audit service used by CLI and Dagster
  orchestration.py   # Dagster assets, jobs, 6 checks, resources, schedule and SLA sensor
  __main__.py        # python -m media_quotes (CLI entry point, UTF-8 console fix)
  bootstrap.py       # Alembic upgrade head + source registry sync
  cli/               # mq command-line interface package (19 subcommands)
  risk_controls/     # RSK-07/08/11 policy package: review queue, budget gate, retention
  validation/        # Data-asset validation package: sources, sections, quotes, modular configs
  db/                # schema.sql (DDL source), engine.py, tables.py, repository.py
docs/                # Requirements, architecture, tasks, decisions, rationale, repo tree (11), start plan (12), command reference (14)
data/                # Registry, frozen schema, derived JSONL export, templates (no Python)
tests/               # pytest suite incl. test_docs_qa.py; VCR.py/syrupy planned for full stack
scripts/             # docs_qa.py (CHK-08), validate_data.py, check.ps1, run_dagster.ps1, run_dashboard.ps1, manual_validate.ps1
alembic.ini          # Alembic config; database URL comes from Settings, not this file
migrations/          # Alembic env.py, script.py.mako and versions/
  versions/0001_initial.py        # initial schema
  versions/0002_gold_set.py       # gold set labels and eval runs
  versions/0003_decision_cache.py # decision cache and partition registry
  versions/0004_topic_language_judge.py # topic/language/translation provenance
  versions/0005_topic_packs.py    # topic_id on quotes, digests and pipeline runs
  versions/0006_digest_topic_unique.py # one digest manifest per topic and date
digests/, var/{blobs,audit}/ # Generated artifacts (gitignored)
```

Daily MD files use the canonical path `digests/YYYY-MM-DD.md`. `data/reports/` is a legacy
compatibility directory only.

## Quick start

**Works today (T1 foundation):**

**PowerShell / Windows (recommended commands):**

```powershell
Copy-Item .env.example .env        # local dev defaults; replace secrets for real deployments
py -m pip install uv              # only if `uv` is not on PATH; or use the official PowerShell installer
uv sync --frozen --group dev
docker compose up -d --wait
python -m media_quotes.bootstrap  # Alembic `upgrade head`, then the registry sync
python -m media_quotes doctor
pytest
```

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

Extras: `dashboard` (Streamlit/pandas), `orchestration` (Dagster), `extraction`
(httpx/trafilatura), `llm` (OpenAI/PydanticAI). Dev/test tooling is in the `dev`, `test` and
`lint` dependency groups. Markdown rendering is built into `digest.py` — no template engine.
`uv.lock` is committed; CI/prod use `uv sync --frozen --all-extras`. Engineering standards (ruff, `mypy --strict`, 85%
coverage, Alembic, secrets redaction) are specified in
[`docs/02`](docs/02-stack-and-architecture.md) §7.

## Command interface (offline fixture-replay CLI implemented today; full live pipeline in T9)

The implemented entry point is `mq` (`python -m media_quotes` / `python -m media_quotes.cli`).
All subcommands below use deterministic offline fixture replay, except `mq fetch`, which is an
explicit live command and is a no-network dry-run unless you remove `--dry-run`. Live fetching is
rights-gated and writes no database rows. T9 completes live Dagster asset scheduling, external LLM
provider calls and production database connections. Full command examples live in
[`docs/14`](docs/14-command-reference.md); the switch reference is in
[`docs/17`](docs/17-connection-and-fetching-reference.md).

| Command | Purpose |
|---|---|
| `mq doctor` | Audit config, T0 registry, schema, versions, templates, DB; no network |
| `mq validate` | Validate `sources.csv`, frozen JSON Schema and derived JSONL paths (available today) |
| `mq run --date YYYY-MM-DD` | Full pipeline **plus** audit report and log summary in one command |
| `mq audit [--date …] [--run-id …]` | T0/coverage/schema/false-quote/exact-match/first-hand checks → JSON + MD in `var/audit/` |
| `mq logs --run-id ID [--trace-id ID]` | Correlated structured logs with `trace_id` |
| `mq eval --gold-set <version> [--split …]` | Deterministic metrics + κ-calibrated judge (optional `all|train|dev|test` filter) |
| `mq sources`, `mq topics`, `mq version` | Registry + rights status, topic-pack management, version manifest |
| `mq fetch --source-id <id> [--live]` | Plan a rights-cleared fetch; add `--live` to actually contact the source |
| `mq backfill`, `mq digest`, `mq export`, `mq dashboard` | Backfill, regenerate MD, derived JSONL, Streamlit UI |
| `mq dagster` | Show Dagster asset, partition, schedule and persistence guidance |
| `mq review`, `mq retention`, `mq restore-drill` | Review queue (`list|seed|decide`), retention plan, derived-export integrity |

Shared flags: `--topic`, `--dry-run`, `--date`, `--run-id`/`--trace-id`, `--out`, `--verbose`.
`--offline` is deprecated and accepted only for compatibility: every `mq` command is offline
unless you explicitly run `mq fetch` without `--dry-run`. Exit codes: `0` pass · `1`
validation/threshold failure · `2` partial success with coverage gaps · `3` config/T0-gate error.
Re-runs are idempotent.

`make docs-qa`, `make format`, `make lint`, `make typecheck`, `make test`, `make coverage`,
`make build`, `make smoke` and `make check` (the full gate, using `coverage`) wrap the local
quality workflow; the live orchestration contract is specified in
[`docs/02`](docs/02-stack-and-architecture.md) §5.

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
requires the code to live in a published GitHub repository. The local repository currently has no
remote configured, so deployment requires:

1. Create a GitHub repository and add it as the `origin` remote.
2. Commit and push the current `main` branch.
3. In Streamlit Cloud, choose that repository, branch `main`, and main file path
   `src/media_quotes/dashboard.py`.
4. Use the root `requirements.txt`, which installs the project with the `dashboard` extra.

The dashboard reads local replay artifacts and does not require a database or API key.

## Dates

`BACKFILL_START=2026-10-01`; `REPORT_DATE` defaults to today; `REPORT_WINDOW_DAYS=14`.
Snapshot tests freeze the clock. Currently that makes the digest window 6–19 Nov 2026.

## Source registry and adapters

The authoritative source definitions are the JSON files in `data/sources/<source_id>.json`
(explained in [`docs/07-sources.md`](docs/07-sources.md)), mirrored to `data/sources.csv` and
`data/source_topic_routes.csv`. At bootstrap they are validated into `SourceRegistryRow` objects
and synced into the Postgres `sources` table. The schema enforces the T0 rights gate: a source
cannot be enabled unless rights are approved, a licence reference exists, access mode is
permitted and discovery is enumerable.

Per-source behaviour lives in the `src/media_quotes/adapters/` package — permission-gated
adapters selected by `discovery_method` (`sitemap`, `rss`, `api`, `gdelt`, `licensed_database`,
`web`) with optional per-source plugins. Ten sources currently pass the T0 gate; the remaining
16 are pending counsel review and 2 (Reuters and BBC) are blocked, so `mq audit` reports them as
explicit coverage gaps. `mq fetch` refuses a blocked source with `SourceNotPermitted`.
Per-source discovered/quotes counts are written to coverage rows in the audit report and to
`audit_events`; `pipeline_runs.stats` holds run-level totals.

## Validation commands

| Goal | Command | State |
|---|---|---|
| Documentation consistency (CHK-08) | `python scripts/docs_qa.py` or `pytest tests/test_docs_qa.py` | works today |
| Unit/full test suite | `python -m pytest -q` | works today; targeted and full runs stay fast |
| Full gate with 85% coverage | `make coverage` or `make check` | coverage is enforced only in these full-gate commands |
| Lint/format | `python -m ruff check .` / `ruff format --check .` | works today |
| Registry/schema/data checks | `python scripts/validate_data.py` or `mq doctor` | works today |

`pytest` does not enable coverage by default. Running a targeted test file will not trigger
the global 85% threshold. `make coverage` runs the full suite with coverage and enforces the
floor defined once in `[tool.coverage.report]`. `make check` is the canonical CI gate.
See docs/02 §7 for the full engineering standard.

## Design invariants

1. **Quotes are never trusted from an LLM** — the deterministic span verifier must pass.
2. **Decisions are cached by content hash + model/prompt/schema versions**, not by `temperature=0`.
3. **Postgres is the system of record**; JSONL is a derived export.
4. **No source is contacted until its rights record permits the exact use case** — mandatory
   sources stay mandatory but `blocked` under the T0 gate.
5. **Validation is strict at every boundary**; schemas and prompts are frozen with snapshots.
6. **One OTel trace per article** with `trace_id` propagated to logs and the dashboard.
7. **Eval is built early** against a labelled gold set: 60/20/20 split, recall ≥ 0.90,
   exact-match 100%, false-quote 0, judge κ ≥ 0.70.
8. **Every action is reachable through one easy command** (`mq`) for audits and logs.

## Glossary

The glossary lives in [`docs/README.md`](docs/README.md) (single source). Key notation:
"T0 gate" = the rights gate (brief §1); task `T0` = the first task in `docs/03`;
DEC/RSK register IDs are defined in [`docs/04`](docs/04-risks-and-open-questions.md).
