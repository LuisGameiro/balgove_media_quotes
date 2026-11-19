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
| CLI | All 12 `mq` subcommands implemented in offline fixture-replay mode | Live Dagster/Postgres/LLM orchestration |
| Export | `src/media_quotes/export.py` with strict `ExportQuoteRecord` validation | Context packs from live Postgres (`mq export`) |
| Logs / traces | JSON logging, structlog secret redaction, optional OTLP tracing (`observability.py`) | Distributed tracing with live collectors |
| Tests | 146 pytest tests, 23 risk-control policy tests, 95% coverage | VCR.py live recordings, Dagster asset checks |
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
  risk_controls.py   # RSK-07/08/11 policy: review evidence, budget gate, retention
  observability.py   # JSON logging, optional OTel traces
  adapters.py        # Single permission-gated source adapter
  export.py          # Derived JSONL export with strict schema validation
  validation.py      # Importable registry/schema/quote validation
  cli.py             # all 12 mq subcommands implemented (offline fixture-replay mode)
  __main__.py        # python -m media_quotes
  bootstrap.py       # Alembic upgrade head + source registry sync
  db/                # schema.sql (DDL source), engine.py, tables.py, repository.py
docs/                # Requirements, architecture, tasks, decisions, rationale, repo tree (11), start plan (12)
data/                # Registry, frozen schema, derived JSONL export, templates (no Python)
tests/               # pytest suite incl. test_docs_qa.py; VCR.py/syrupy planned for full stack
scripts/             # docs_qa.py (CHK-08), validate_data.py (registry/data checks)
alembic.ini          # Alembic config; database URL comes from Settings, not this file
migrations/          # Alembic env.py, script.py.mako and versions/
  versions/0001_initial.py  # initial schema, executed from db/schema.sql via op.execute
digests/, var/{blobs,audit}/ # Generated artifacts (gitignored)
```

Daily MD files use the canonical path `digests/YYYY-MM-DD.md`. `data/reports/` is a legacy
compatibility directory only.

## Quick start

**Works today (T1 foundation):**

```sh
cp .env.example .env              # optional; secrets come from the environment
uv sync --all-extras --dev
docker compose up -d
python -m media_quotes.bootstrap  # Alembic `upgrade head`, then the registry sync
mq doctor                         # or: python -m media_quotes doctor
pytest
make check                        # docs QA, ruff, mypy, tests, build, CLI smoke
```

Extras: `test` (pytest/VCR.py/syrupy/jsonschema), `eval` (Pydantic Evals/Ragas), `render`
(Jinja2), `dashboard` (Streamlit/pandas), `orchestration` (Dagster), `extraction`
(httpx/trafilatura/Playwright), `llm` (OpenAI/PydanticAI). `uv.lock` is committed; CI/prod
use `uv sync --frozen --all-extras`. Engineering standards (ruff, `mypy --strict`, 85%
coverage, Alembic, secrets redaction) are specified in
[`docs/02`](docs/02-stack-and-architecture.md) §7.

## Command interface (offline fixture-replay CLI implemented today; full live pipeline in T9)

The implemented entry point is `mq` (`python -m media_quotes` / `python -m media_quotes.cli`).
All 12 subcommands in the table below function today in deterministic offline fixture-replay mode,
satisfying audit, evaluation and digest workflows without network access. T9 completes the live
Dagster asset scheduling, external LLM provider calls, and production database connections.

| Command | Purpose |
|---|---|
| `mq doctor` | Audit config, T0 registry, schema, versions, templates, DB; no network |
| `mq validate` | Validate `sources.csv`, frozen JSON Schema and derived JSONL paths (available today) |
| `mq run --date YYYY-MM-DD` | Full pipeline **plus** audit report and log summary in one command |
| `mq audit [--date …] [--run-id …]` | T0/coverage/schema/false-quote/exact-match/first-hand checks → JSON + MD in `var/audit/` |
| `mq logs --run-id ID [--trace-id ID]` | Correlated structured logs with `trace_id` |
| `mq eval --gold-set <version>` | Deterministic metrics + κ-calibrated judge |
| `mq sources`, `mq version` | Registry + rights status at a glance; version manifest |
| `mq backfill`, `mq digest`, `mq export`, `mq dashboard` | Backfill, regenerate MD, derived JSONL, Streamlit UI |

Shared flags: `--offline` (VCR replay), `--dry-run`, `--date`, `--run-id`/`--trace-id`,
`--out`, `--verbose`. Exit codes: `0` pass · `1` validation/threshold failure · `2` partial
success with coverage gaps · `3` config/T0-gate error. Re-runs are idempotent.

`make docs-qa`, `make format`, `make lint`, `make typecheck`, `make test`, `make build`,
`make smoke` and `make check` (all of the above plus the `mq --help` smoke test) wrap the
local gate; the full live orchestration contract is specified in
[`docs/02`](docs/02-stack-and-architecture.md) §5.

## Dates

`BACKFILL_START=2026-10-01`; `REPORT_DATE` defaults to today; `REPORT_WINDOW_DAYS=14`.
Snapshot tests freeze the clock. Currently that makes the digest window 6–19 Nov 2026.

## Source registry and adapters

`data/sources.csv` is the authoritative source registry (explained in
[`docs/07-sources.md`](docs/07-sources.md)). At bootstrap it is validated into
`SourceRegistryRow` objects and synced into the Postgres `sources` table. The schema
enforces the T0 rights gate: a source cannot be enabled unless rights are approved, a
licence reference exists, access mode is permitted and discovery is enumerable.

Per-source behaviour lives in `src/media_quotes/adapters.py` — a single permission-gated
adapter selected by `discovery_method` (`sitemap`, `rss`, `api`, `gdelt`, `licensed_database`,
`web`). All rows are currently disabled, so the adapter refuses network access with
`SourceNotPermitted`. Methods are skeletons and must be implemented during onboarding after
rights clearance.
`CoverageStats` records discovered/fetched/parsed/rejected/failed/skipped counts in
`pipeline_runs.stats`.

## Validation commands

| Goal | Command | State |
|---|---|---|
| Documentation consistency (CHK-08) | `python scripts/docs_qa.py` or `pytest tests/test_docs_qa.py` | works today |
| Unit/full test suite | `python -m pytest -q -o addopts=''` | works today (use `-o` until `pytest-cov` is installed) |
| Full gate with 85% coverage | `make check` | works today; single CI gate |
| Lint/format | `python -m ruff check .` / `ruff format --check .` | works today |
| Registry/schema/data checks | `python scripts/validate_data.py` or `mq doctor` | works today |

`pytest` uses coverage flags in default `addopts`; `pytest-cov` ships in the dev/test extras.
Pass `-o addopts=''` as shown above for a quick run without coverage.
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
