# 04 — Repository structure

## Root

```text
README.md                 entry point and operator overview
pyproject.toml            project metadata, dependencies and tool config
uv.lock                   locked dependency resolution
Makefile / scripts/check.ps1   one-command quality gate
docker-compose.yml        local Postgres (Neo4j runs separately for graph features)
alembic.ini               Alembic configuration
requirements.txt          generated deployment artifact for Streamlit Cloud
data/                     registry, frozen schema, fixtures, gold set, topics
docs/                     this documentation set and the original brief
migrations/               Alembic environment and revision chain
scripts/                  repository, CI and validation scripts
src/                      Python application package
tests/                    unit, integration and contract tests
var/output/               generated output root (digests, quotes, audit, logs, raw)
```

## Application package

```text
src/media_quotes/
├── __init__.py / __main__.py / cli/   package marker, module entry, parser and handlers
├── adapters/                   source adapters, blob cache, RSS/sitemap, HTML and PDF parsing
├── archive.py                  bounded historical RSS/sitemap/sitemap-index discovery
├── db/                         engine, table definitions, schema.sql, session helpers
├── graph/                      derived Neo4j projection, queries and GraphRAG protocol
├── risk_controls/              review queue, budget gate, retention policy
├── validation/                 data-asset validation for registry, schema and exports
├── batch.py / batch_runner.py  OpenRouter batch payloads and submit/poll client
├── blob_prune.py               tombstone-gated raw blob pruning
├── context_pack.py             bounded, provenance-tagged context packs
├── bootstrap.py                Alembic upgrade plus source sync
├── config.py                   Settings, pinned defaults and path resolution
├── curation.py                 section assignment, dedupe and review badges
├── dashboard.py                Streamlit read-only dashboard
├── digest.py                   deterministic Markdown renderer
├── enums.py / exceptions.py    canonical values and domain error hierarchy
├── evaluation.py               acceptance-threshold contract
├── export.py                   strict JSONL export with atomic writes
├── extraction.py               trafilatura extraction, quality gate and offsets
├── fetch.py                    rate-limited, rights-gated HTTP client policy
├── gold_set.py                 versioned gold set and 60/20/20 splits
├── judge.py                    optional AI-judge boundary
├── language.py                 offline quote-language detection
├── live.py                     shared feed/article fetch loop
├── llm.py / llm_live.py         provider protocol and pinned LLM boundary
├── llm_cache.py                decision-cache hooks
├── llm_openrouter.py           OpenRouter chat-completions provider
├── models.py                   strict Pydantic boundary models
├── observability.py            structlog redaction and optional OTLP tracing
├── orchestration.py            Dagster assets, checks, jobs, schedules and sensor
├── pipeline.py                 daily pipeline service shared by CLI and Dagster
├── py.typed                    PEP 561 typing marker
├── registry.py                 CSV registry and T0 policy
├── relevance.py                deterministic versioned topic rules
├── restore_drill.py            export integrity and retention readiness
├── rights_guard.py             word limits and attribution policy guard
├── source_configs.py           per-source JSON config and registry projection
├── source_topics.py            strict topic-route loader
├── text_utils.py               Unicode folding and safe artifact ids
├── topics.py                   topic pack model and loaders
├── translation.py              optional pinned-boundary translation
├── verification.py             exact-span verifier
└── verification_stages.py      pre-LLM filter and publishable-only pipeline
```

## Documentation set

```text
docs/README.md                 index
docs/01-requirements.md        requirement traceability and current status
docs/02-architecture.md        how it is built
docs/03-sources.md             source catalogue and rights state
docs/04-repository-structure.md this file
docs/05-command-reference.md   commands, flags and configuration
docs/06-whats-left.md          remaining steps
docs/07-decision-log.md        design decisions and rationale
docs/Test_News.md              immutable original assignment
```

## Placement rules

- Business logic belongs in `src/media_quotes/`, with a unit test.
- Database changes go through Alembic; `schema.sql` is the base DDL source.
- Runtime output belongs under `var/output/` (or `OUTPUT_DIR`), never in the source package.
- Canonical daily files are reserved for a complete `live_feed` run with no tier/source filter;
  partial runs write under `var/output/runs/<scope>/`, create no canonical Postgres digest
  manifest, and fixture replay keeps its own `fixtures/` tree.
- Canonical values live in `enums.py`; do not duplicate them as literals.
- Generated Python modules stay single-purpose; split only on independent change reasons.
