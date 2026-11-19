# 11 — Repository structure (monorepo)

This document is the canonical map of the repository. It records **where files live, what belongs there, and which files are generated**. The root [README](../README.md) has a short summary; this document is the complete tree.

The repository uses a Python `src/` layout. Application code lives in `src/media_quotes/`, repository tooling in `scripts/`, configuration and data assets at the root and in `data/`, plans and decisions in `docs/`, schema migrations in `migrations/`, and tests in `tests/`. Generated outputs live in `digests/` and `var/` and are gitignored.

## 1. Top-level tree

```text
.
├── .github/
│   └── workflows/
│       └── ci.yml                    # canonical CI gate; calls make check + wheel smoke
├── .env.example                      # environment variable template; no secrets
├── .gitignore                        # keeps caches, venvs, secrets and generated data out of git
├── .python-version                   # pinned Python 3.12 for uv/CI
├── Makefile                          # local/CI quality gate: make check
├── README.md                         # operator-facing overview and quick start
├── alembic.ini                       # Alembic config; DB URL comes from Settings
├── docker-compose.yml                # local Postgres service
├── pyproject.toml                    # project metadata, deps, ruff/mypy/pytest/coverage config
├── uv.lock                           # locked, reproducible dependency resolution
├── data/                             # committed data assets; no Python modules
├── digests/                          # generated daily Markdown digests
├── docs/                             # requirements, architecture, tasks, risks, traceability
├── migrations/                       # Alembic migration scripts
├── scripts/                          # thin repository/CI wrappers
├── src/                              # Python application package
├── tests/                            # unit, integration and contract tests
└── var/                              # generated runtime artifacts
```

Ignored and intentionally absent from a clean checkout: `.venv/`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.coverage`, `build/`, `dist/`, `*.egg-info/`, `.env`, generated `digests/*.md`, generated `var/**/*`, and `data/quotes.jsonl`.

## 2. Application package

```text
src/media_quotes/
├── __init__.py                       # package marker
├── __main__.py                       # python -m media_quotes entry point
├── adapters.py                       # permission-gated source adapter; T0 fail-closed
├── bootstrap.py                      # Alembic upgrade head + source registry sync
├── cli/                              # mq package: app, shared helpers and command modules
│   ├── __init__.py                   # stable public API (main, render_errors, ...)
│   ├── __main__.py                   # python -m media_quotes.cli
│   ├── app.py                        # parser/dispatch and exit-code boundary
│   ├── common.py                     # constants and shared helpers
│   ├── run.py                        # offline blocked/degraded pipeline run
│   ├── audit.py                      # audit report generation and rendering
│   ├── logs.py                       # run/trace log viewer
│   ├── eval.py                       # synthetic gold-set evaluation
│   └── ops.py                        # backfill, sources, digest, export, dashboard commands
├── config.py                         # Settings, PROJECT_ROOT, SecretStr DB URL, date defaults
├── coverage.py                       # per-source coverage counters
├── dashboard.py                      # dashboard projection scaffold
├── digest.py                         # daily MD digest assembly scaffold
├── enums.py                          # one definition of canonical enum values
├── exceptions.py                     # shared domain exception hierarchy
├── export.py                         # derived JSONL writer
├── llm.py                            # LLM provider boundary scaffold
├── models.py                         # strict Pydantic boundary models
├── observability.py                  # structlog JSON logging, redaction, optional OTLP
├── py.typed                          # PEP 561 marker for downstream type checking
├── registry.py                       # sources.csv -> SourceRegistryRow and T0 policy
├── risk_controls/                    # RSK-07/08/11 policy package with flat re-exports
│   ├── __init__.py                   # stable public API for existing callers
│   ├── models.py                     # strict policy models and value objects
│   ├── obsolescence.py               # review queue and obsolete-flag policy
│   ├── budget.py                     # backfill cost estimate and budget gate
│   ├── retention.py                  # partition and retention planning
│   └── cache.py                      # content-addressed decision-cache key
├── text_utils.py                     # Unicode folding and exact-span helpers
├── validation/                       # data-asset validation package with flat re-exports
│   ├── __init__.py                   # validate_data_dir + stable helper API
│   ├── common.py                     # CSV reading and column checks
│   ├── sources.py                    # sources.csv and T0 validation
│   ├── sections.py                   # sections.csv validation
│   └── quotes.py                     # derived JSONL/schema validation
└── db/
    ├── __init__.py                   # database subpackage marker
    ├── engine.py                     # SQLAlchemy engine and session factory
    ├── repository.py                 # registry upsert/query operations
    ├── schema.sql                    # SQL DDL source consumed by Alembic bootstrap
    └── tables.py                     # SQLAlchemy Core tables used by repository code
```

Rules:

- Business logic belongs in `src/media_quotes/`, never in `scripts/` or `data/`.
- Modules should remain single-purpose. Split a module only when it has independently changing responsibilities.
- Canonical values such as `rights_status`, `access_mode`, `discovery_method` and section IDs belong in `enums.py`, not duplicated as literals across code.
- Database bootstrap goes through Alembic and `migrations/`; no raw DDL execution should be added outside the migration path.
- Runtime outputs must be written under `digests/` or `var/`, never into the source package.

## 3. Data assets

```text
data/
├── README.md                         # data asset contract and validation workflow
├── sources.csv                       # authoritative source registry (committed input)
├── sections.csv                      # canonical digest sections
├── quotes.schema.json                # frozen JSON Schema for derived quote export
├── quotes.example.jsonl              # synthetic examples; not real quotes
├── report_template.md                # report structure reference
├── fixtures/
│   ├── bbc/
│   │   ├── synthetic-bbc-2026-11-16.json
│   │   └── synthetic-bbc-2026-11-18.html
│   └── reuters/
│       ├── synthetic-reuters-2026-11-17.html
│       └── synthetic-reuters-2026-11-18.html
├── gold_set/
│   └── synthetic-v1.json             # synthetic gold-set fixture for offline eval
└── reports/
    └── .gitkeep                      # legacy compatibility directory only
```

`data/` is for versioned inputs, schemas, fixtures and templates. Do not add Python files here. `quotes.jsonl` is derived from Postgres and is gitignored; it is never the system of record.

## 4. Tests

```text
tests/
├── conftest.py                       # shared fixtures and warning policy
├── unit/                             # pure, offline tests; mirrors src modules
│   ├── __init__.py
│   ├── test_adapters.py
│   ├── test_config.py
│   ├── test_coverage.py
│   ├── test_enums.py
│   ├── test_export_writer.py
│   ├── test_models.py
│   ├── test_observability.py
│   ├── test_observability_policy.py
│   ├── test_registry.py
│   ├── test_registry_coercions.py
│   ├── test_text_and_export.py
│   ├── test_text_utils_edges.py
│   ├── risk_controls/                # mirrors media_quotes/risk_controls/
│   │   ├── __init__.py
│   │   ├── test_policies.py
│   │   └── test_validation.py
│   └── validation/                   # mirrors media_quotes/validation/
│       ├── __init__.py
│       ├── test_helpers.py
│       └── test_service.py
├── integration/                      # CLI/bootstrap/DB boundary tests
│   ├── __init__.py
│   ├── test_bootstrap.py
│   ├── test_storage.py
│   └── cli/                          # mirrors media_quotes/cli/
│       ├── __init__.py
│       ├── test_app.py
│       └── test_errors.py
└── contract/                         # docs/data contract checks
    ├── __init__.py
    └── test_docs_qa.py
```

Rules:

- Unit tests stay offline: no network, no database, no real source calls.
- Infrastructure-backed tests belong in `tests/integration/` and use fakes unless explicitly marked `integration` or `network`.
- Documentation and data-contract checks belong in `tests/contract/`.
- Use descriptive test filenames; avoid generic suffixes such as `*_extra.py`.

## 5. Documentation

```text
docs/
├── README.md                         # docs index, glossary and QA checklist
├── Test_News.md                      # canonical source brief, Markdown
├── Test_News.docx                    # source brief, Word copy
├── 01-requirements-analysis.md       # REQ/OUT and interpretation decisions
├── 02-stack-and-architecture.md      # stack, architecture and engineering standards
├── 03-work-breakdown.md              # T0–T10 tasks and sequencing
├── 04-risks-and-open-questions.md    # active decision/risk register
├── 06-observations.md                # mandatory-source and design observations
├── 07-sources.md                     # source catalogue and rights status
├── 08-traceability.md                # REQ/OUT -> DEC -> task -> CHK matrix
├── 09-neo4j-integration.md           # optional graph integration plan; not adopted
├── 10-risk-mitigation-plan.md        # plan for closing RSK-07/08/11
└── 11-repository-structure.md        # this document
```

Former `docs/05-critique-and-improvements.md` is retired. Do not recreate it; its content was folded into 01–04 and 06.

## 6. Migrations and repository tooling

```text
migrations/
├── env.py                            # Alembic environment
├── script.py.mako                    # Alembic revision template
└── versions/
    └── 0001_initial.py               # initial schema revision

scripts/
├── docs_qa.py                        # documentation QA checker (CHK-08)
└── validate_data.py                  # thin wrapper around media_quotes.validation
```

`scripts/` contains repository/CI tools only. It must not become a second application package. Any reusable logic discovered in a script should move into `src/media_quotes/` and the script reduced to a thin wrapper.

## 7. Generated artifacts

```text
digests/
├── .gitkeep
└── YYYY-MM-DD.md                     # generated daily digest; gitignored

var/
├── audit/                            # audit reports and eval artifacts (gitignored)
├── blobs/                            # immutable raw article snapshots (gitignored)
│   └── .gitkeep
├── export/                           # derived JSONL exports (gitignored)
│   └── .gitkeep
└── logs/                             # run-scoped structured logs (gitignored)
    └── .gitkeep
```

The `.gitkeep` files preserve empty directories in git. Actual generated files under these directories are runtime outputs and must not be edited by hand or committed.

## 8. Where to add new work

| Change | Correct location |
| --- | --- |
| New source | `data/sources.csv`, `docs/07-sources.md`, and validation tests |
| New canonical enum value | `src/media_quotes/enums.py`, `data/quotes.schema.json`, `src/media_quotes/db/schema.sql`, docs, tests |
| New DB table/column | `migrations/versions/`, `src/media_quotes/db/schema.sql`, `src/media_quotes/db/tables.py`, `src/media_quotes/models.py` |
| New command | `src/media_quotes/cli/` (add a command module and wire in `app.py`), root README, `docs/02-stack-and-architecture.md` |
| New planning document | `docs/`, with an entry in `docs/README.md` |
| New reusable Python logic | `src/media_quotes/` with a unit test |
| New CI/repo check | `Makefile` target and `.github/workflows/ci.yml` via `uv run make check` |
| New dependency | `pyproject.toml`, regenerate `uv.lock`, update docs if developer-facing |
| New generated output path | Add to `.gitignore` and document in this file |

Keep `pyproject.toml` as the single source of dependency and tool configuration. Do not add separate requirements files or ad hoc lint/type/test configuration in other files.
