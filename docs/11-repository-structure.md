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
├── __main__.py                       # python -m media_quotes (CLI entry point)
├── bootstrap.py                      # Alembic upgrade head + JSON source registry sync
├── config.py                         # Settings, PROJECT_ROOT, SecretStr DB URL, date defaults
├── enums.py                          # one definition of canonical enum values
├── exceptions.py                     # shared domain exception hierarchy
├── py.typed                          # PEP 561 marker for downstream type checking
├── adapters/                         # permission-gated adapter package (T0 fail-closed)
│   ├── __init__.py                   # public re-exports (was the old adapters.py API)
│   ├── base.py                       # SourceAdapter + discovery-method subclasses
│   ├── blob_cache.py                 # content-addressed BlobCache and path-confined FixtureSource
│   ├── html_extractor.py             # JSON-LD + HTML fixture parser, newspaper body extraction
│   ├── quote_extractor.py            # quote spans, speaker attribution, section fallback
│   ├── registry.py                   # adapter plugin registry and build_adapter()
│   ├── rss.py                        # RSS 2.0 / Atom discovery
│   └── sitemap.py                    # standard and Google News sitemap discovery
├── cli/                              # mq command-line interface (19 subcommands)
│   ├── __init__.py / __main__.py / app.py   # exports, python -m entry, parser and dispatch
│   ├── common.py                     # shared helpers: paths, run IDs, registry, topic pack, JSON I/O
│   ├── audit.py / logs.py / eval.py  # audit reports, log inspection, gold-set evaluation
│   ├── run.py / fetch.py             # offline collect + explicit rights-gated live fetch
│   ├── ops.py / maintenance.py       # backfill/sources/topics/digest/export; review/retention/drill
│   └── orchestration_info.py         # Dagster guidance text
├── db/                               # SQLAlchemy Core persistence
│   ├── engine.py / tables.py         # engine/session factory; table definitions
│   ├── repository.py                 # idempotent upserts for quotes, digests, runs, audit
│   ├── resources.py                  # Dagster-friendly session-context handle
│   └── schema.sql                    # authoritative DDL consumed by Alembic 0001
├── risk_controls/                    # RSK-07/08/11 policies (package with flat re-exports)
│   ├── models.py                     # review flags, estimates, retention policy
│   ├── obsolescence.py               # review queue and SLA helpers
│   ├── budget.py / retention.py      # backfill budget gate; partition/prune planner
├── validation/                       # data-asset validation (package with flat re-exports)
│   ├── common.py / sources.py / sections.py / quotes.py
│   └── modular.py                    # per-source JSON and per-topic pack checks
├── batch.py                          # provider Batch API planner (design stub, no production caller)
├── curation.py                       # section assignment, cross-outlet dedup, review badges
├── dashboard.py                      # Streamlit dashboard projection
├── digest.py                         # daily Markdown digest assembly
├── evaluation.py                     # pure acceptance-threshold contract
├── export.py                         # strict JSONL export with atomic writes
├── extraction.py                     # trafilatura extraction and quality gate
├── fetch.py                          # rate-limited, rights-gated HTTP client policy
├── gold_set.py                       # versioned gold set and 60/20/20 splits
├── judge.py                          # AI-judge contract (off/shadow/advisory/decision)
├── language.py                       # offline quote-language detection
├── live.py                           # shared live feed/article fetch loop
├── llm.py / llm_live.py / llm_openrouter.py   # provider protocol and pinned boundaries
├── llm_cache.py                      # Postgres-backed decision cache hooks
├── models.py                         # strict Pydantic boundary models
├── observability.py                  # structlog JSON logging, redaction, optional OTLP
├── orchestration.py                  # Dagster assets, 6 checks, jobs, schedule, sensor
├── pipeline.py                       # daily pipeline service shared by CLI and Dagster
├── registry.py                       # sources.csv -> SourceRegistryRow and T0 policy
├── relevance.py                      # deterministic versioned topic relevance rules
├── restore_drill.py                  # export integrity and retention readiness
├── rights_guard.py                   # DEC-10 word limits and attribution policy guard
├── source_configs.py / source_topics.py       # per-source JSON config and topic routes
├── text_utils.py                     # Unicode folding and exact-span helpers
├── topics.py                         # topic pack model, loaders and scaffold
├── translation.py                    # optional pinned-boundary translation
└── verification.py / verification_stages.py   # deterministic verification
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
├── unit/                             # pure, offline tests (one file per feature area)
│   ├── test_adapters.py / test_all_sources_matrix.py / test_newspaper_scraping.py
│   ├── test_config.py / test_enums.py / test_models.py / test_registry*.py
│   ├── test_curation.py / test_export_writer.py / test_text*.py
│   ├── test_relevance.py / test_topics*.py / test_language.py / test_translation.py / test_judge.py
│   ├── test_fetch*.py / test_llm*.py / test_openrouter.py / test_batch.py
│   ├── test_audit*.py / test_dashboard*.py / test_eval*.py / test_orchestration.py
│   ├── test_maintenance_cli.py / test_migrations.py / test_restore_drill.py / test_rights_guard.py
│   ├── test_observability*.py / test_gold_set.py / test_verification*.py / test_extraction.py
│   ├── risk_controls/                # test_policies.py, test_validation.py
│   └── validation/                   # test_helpers.py, test_service.py
├── integration/                      # CLI/bootstrap/DB boundary tests
│   ├── test_bootstrap.py / test_storage.py
│   └── cli/test_app.py / cli/test_errors.py
└── contract/                         # docs/data contract checks
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
├── 11-repository-structure.md        # this document
├── 12-implementation-plan.md         # phases A–F to cut over to live implementation
├── 13-code-review-and-feature-roadmap.md  # review and feature ideas
└── 14-command-reference.md           # every command, flag, script and operational order
```

The former doc 05 (critique and improvements) is retired. Do not recreate it; its content was folded into 01–04 and 06.

## 6. Migrations and repository tooling

```text
migrations/
├── env.py                            # Alembic environment
├── script.py.mako                    # Alembic revision template
└── versions/
    ├── 0001_initial.py               # initial schema
    ├── 0002_gold_set.py              # gold-set labels and eval runs
    ├── 0003_decision_cache.py        # decision cache and partition registry
    ├── 0004_topic_language_judge.py  # topic/language/translation provenance
    ├── 0005_topic_packs.py           # topic_id on quotes, digests and runs
    └── 0006_digest_topic_unique.py   # one digest per topic and date

`decision_cache` is used by `llm_cache.DbDecisionCache` for the pinned LLM boundary when
`PERSIST_TO_POSTGRES=true`. `storage_partitions` (migration 0003) is registered by
`mq retention --dry-run` with persistence on. `batch.py`, `batch_runner.py` and `cli/batch.py`
provide the cost-capped OpenRouter Batch API path; live submission needs a pinned batch model.
Physical table partitioning and a rendered (JS) extraction fallback remain deferred.

scripts/
├── docs_qa.py                        # documentation QA checker (CHK-08)
├── validate_data.py                  # thin wrapper around media_quotes.validation
├── check.ps1                         # Windows equivalent of `make check`
├── manual_validate.ps1               # manual collect/audit without Dagster
└── run_dagster.ps1                   # starts the Dagster development UI
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
| New command | `src/media_quotes/cli/app.py` (parser + dispatch), `src/media_quotes/cli/__init__.py` (re-export), root README, `docs/02-stack-and-architecture.md` |
| New planning document | `docs/`, with an entry in `docs/README.md` |
| New reusable Python logic | `src/media_quotes/` with a unit test |
| New CI/repo check | `Makefile` target and `.github/workflows/ci.yml` via `uv run make check` |
| New dependency | `pyproject.toml`, regenerate `uv.lock`, update docs if developer-facing |
| New generated output path | Add to `.gitignore` and document in this file |

Keep `pyproject.toml` as the single source of dependency and tool configuration. Do not add separate requirements files or ad hoc lint/type/test configuration in other files.
