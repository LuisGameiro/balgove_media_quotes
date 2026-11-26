# 04 — Repository structure

## Root

```text
README.md                  operator quick start
pyproject.toml             metadata, dependencies and tool configuration
uv.lock                    locked dependency resolution
requirements.txt           generated deployment artifact for Streamlit Cloud
Makefile                   one-command quality gate (`make check`)
docker-compose.yml         local Postgres, OTLP collector and optional Neo4j graph profile
otelcol-config.yaml        collector configuration
.env.example               every supported setting with safe defaults
my_article.html            optional local sample page for test_scraper.py --html-file
data/                      registry, source configs, topic packs, fixtures and gold sets
docs/                      this document set and the immutable original brief
migrations/schema.sql      single authoritative DDL file
scripts/                   CI, ops, data and diagnostic helpers
src/                       Python application package
tests/                     unit and integration tests
var/output/                generated output root
```

## Scripts

```text
scripts/ci/          check.ps1 (full gate) and validate_data.py
scripts/ops/         run_all.ps1 (turnkey fixture verification), run_dagster.ps1,
                     enable_automation.ps1, blob_prune_scheduled.ps1 (tombstone-gated prune)
scripts/data/        gold-fixture and topic-matrix generators
scripts/diagnostics/ source_proof.py and test_scraper.py (the implementation modules)
scripts/source_proof.py, scripts/test_scraper.py   compatibility shims for old import paths
```

Documentation is edited by hand; there is no automated docs checker.

## Application package

```text
src/media_quotes/
├── adapters/               feed sitemap/RSS parsers, HTML/PDF extraction and blob cache
├── ai/                     LLM boundary, cache, judge, translation, language and quote audit
├── cli/                    argument parsing and command handlers
├── db/                     engine, table definitions and repository writes
├── governance/             evaluation, gold sets, relevance, rights, restore and blob pruning
├── graph/                  optional Neo4j projection and GraphRAG protocol
├── ingestion/              neutral collection service, fetch policy, live collection, archive,
│                           Bing and admission
├── processing/             extraction, verification, curation, digest and export
├── risk_controls/          obsolescence review, budget gate and retention policy
├── validation/             registry, schema and export validation
├── pipeline.py             daily pipeline shared by CLI and Dagster
├── orchestration.py        Dagster assets, checks, jobs and schedules
├── dashboard.py            read-only Streamlit dashboard
├── config.py               settings, pinned defaults and path resolution
└── source_configs.py       per-source JSON configs and CSV mirror

## Generated output

```text
var/output/digests/    daily Markdown, with fixtures/ and runs/<scope>/ trees
var/output/quotes/     daily JSONL exports
var/output/audit/      audit JSON/Markdown and review queues
var/output/logs/       one JSONL log file per run
var/output/raw/        downloaded HTML blobs
var/output/proof/      per-source proof reports
var/dagster/           Dagster home set by scripts/ops/run_dagster.ps1
var/output/dagster/    default home when DAGSTER_HOME is unset (see `mq doctor`)
```

## Placement rules

- Business logic belongs in `src/media_quotes/`, with a unit test.
- Schema changes go in `migrations/schema.sql`; `mq bootstrap` applies it idempotently.
- Runtime output belongs under `var/output/`, never inside the source package.
- Canonical daily files belong to a complete `live_feed` run; partial runs use `runs/<scope>/`.
- Canonical values live in `enums.py`; do not duplicate them as literals.
- Keep modules single-purpose; split only on independent change reasons.
