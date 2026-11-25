# 04 — Repository structure

## Root

```text
README.md                     entry point and operator overview
pyproject.toml                project metadata, dependencies and tool config
uv.lock                       locked dependency resolution
.python-version               pinned Python version
.env.example                  documented configuration template; `.env` is local and ignored
Makefile / scripts/check.ps1  one-command quality gate
docker-compose.yml            local Postgres plus optional Neo4j (profile graph)
alembic.ini                   Alembic configuration
otelcol-config.yaml           local OpenTelemetry collector configuration
.streamlit/config.toml        dashboard runtime configuration
.github/                      CI workflow and repository automation
requirements.txt              generated deployment artifact for Streamlit Cloud
data/                         registry, frozen schema, fixtures, gold set and topics
docs/                         explanatory documents and immutable original brief
migrations/                   Alembic environment and revision chain
scripts/                      repository, CI, proof and validation scripts
src/                          Python application package
tests/                        unit, integration and contract tests
var/dagster/dagster.yaml      local Dagster runtime settings
var/output/                   operational digests, quotes, audit, logs, raw and live proof output
var/test-output/              isolated synthetic fixture and offline-proof output
```

## Application package

```text
src/media_quotes/
├── __init__.py / __main__.py  package marker and module entry point
├── adapters/
│   ├── base.py               source adapter protocol and rendered-JS policy
│   ├── blob_cache.py         content-addressed raw response cache
│   ├── html_extractor.py     article HTML extraction and metadata
│   ├── pdf_extractor.py      PDF/document extraction
│   ├── quote_extractor.py    candidate quote spans and speaker attribution
│   ├── registry.py           source adapter selection
│   ├── rss.py                RSS/Atom feed parsing
│   └── sitemap.py            sitemap parsing
├── cli/
│   ├── __init__.py / __main__.py
│   ├── app.py                top-level argparse parser and dispatch
│   ├── audit.py              deterministic audit handler
│   ├── batch.py              OpenRouter batch handler
│   ├── common.py             shared exit codes and argument helpers
│   ├── eval.py               gold-set evaluation handler
│   ├── fetch.py              live fetch handler
│   ├── graph.py              Neo4j command handlers
│   ├── logs.py               structured-log handler
│   ├── maintenance.py        review, pruning, retention and restore handlers
│   ├── ops.py                source, topic, digest, export and backfill handlers
│   ├── orchestration_info.py Dagster guidance handler
│   └── run.py                daily pipeline handler
├── db/
│   ├── engine.py             SQLAlchemy engine construction
│   ├── repository/           authoritative business and audit writes
│   │   ├── __init__.py       stable façade for repository aggregates
│   │   ├── common.py         shared UUID/JSON/rowcount helpers
│   │   ├── content.py        article, quote, classification and digest writes
│   │   ├── evaluation.py     eval runs, labels and decision cache
│   │   ├── review.py         obsolescence review flags
│   │   ├── runs.py           pipeline runs and audit events
│   │   └── sources.py        source registry sync
│   ├── resources.py          database resource helpers
│   ├── schema.sql            base DDL source
│   └── tables.py             SQLAlchemy table definitions
├── graph/
│   ├── client.py             Neo4j driver boundary and lazy resource handle
│   ├── protocol.py           graph service protocol
│   ├── queries.py            lineage, speaker, divergence and context queries
│   ├── schema.py             constraints and indexes
│   └── sync.py               idempotent Postgres-to-Neo4j projection
├── risk_controls/
│   ├── budget.py             backfill cost estimates and budget gate
│   ├── models.py             review and budget boundary models
│   ├── obsolescence.py       evidence-based supersession proposals
│   └── retention.py          retention and partition policy
├── validation/
│   ├── common.py             shared validation helpers
│   ├── modular.py            composed data validation
│   ├── quotes.py             export/schema validation
│   ├── sections.py           section catalogue validation
│   └── sources.py            source registry/config validation
├── archive.py                bounded historical feed/sitemap discovery
├── backfill.py               shared CLI/Dagster historical planning and execution
├── batch.py / batch_runner.py OpenRouter batch payloads and submit/poll client
├── bing_news.py              free Bing News URL discovery
├── blob_prune.py             tombstone-gated raw blob pruning
├── bootstrap.py              Alembic upgrade plus source sync
├── collection.py             shared feed, URL and Bing article acquisition
├── config.py                 Settings, pinned defaults and path resolution
├── context_pack.py           bounded, provenance-tagged context packs
├── curation.py               section assignment, dedupe and review badges
├── dashboard.py              Streamlit read-only dashboard
├── diagnostics/              shared offline-safe scraper/route diagnostics
│   ├── __init__.py           stable public façade for diagnostics helpers
│   ├── html.py               article HTML inspection and quote extraction
│   ├── routes.py             route/topic configuration and gate helpers
│   └── sources.py            live feed, fixture and source validation helpers
├── digest.py                 deterministic Markdown renderer
├── enums.py / exceptions.py  canonical values and domain error hierarchy
├── evaluation.py             acceptance-threshold contract
├── export.py                  strict JSONL export with atomic writes
├── extraction.py             trafilatura extraction, quality gate and offsets
├── fetch.py                  rate-limited, rights-gated HTTP client policy
├── gold_set.py               versioned gold set and 60/20/20 splits
├── judge.py                  optional AI-judge boundary
├── language.py               offline quote-language detection
├── live.py                   shared feed/article fetch loop
├── llm.py / llm_live.py       provider protocol and pinned LLM boundary
├── llm_cache.py              decision-cache hooks
├── llm_openrouter.py         OpenRouter chat-completions provider
├── models.py                 strict Pydantic boundary models
├── observability.py          structlog redaction and optional OTLP tracing
├── orchestration/            Dagster assets, checks, jobs, schedules and sensor
│   ├── __init__.py           stable façade and module-level defs
│   ├── backfill_range.py     manual historical range backfill helpers
│   ├── checks.py             asset-check factories
│   ├── common.py             constants, configs, resources and shared helpers
│   └── definitions.py        asset/job/schedule/sensor declarations
├── pipeline.py               daily pipeline service shared by CLI and Dagster
├── py.typed                  PEP 561 typing marker
├── quote_audit.py            AI shadow audit for missed quotes
├── registry.py               strict registry row model and T0 policy
├── relevance.py              deterministic versioned topic rules
├── restore_drill.py          export integrity and retention readiness
├── rights_guard.py           word limits and attribution policy guard
├── source_configs.py         per-source JSON config and registry projection
├── source_processing.py      shared source discovery, topic gate and verification service
├── source_topics.py          strict topic-route loader
├── text_utils.py             Unicode folding and safe artifact ids
├── topics.py                 topic pack model and loaders
├── translation.py            optional pinned-boundary translation
├── verification.py           exact-span verifier
└── verification_stages.py    pre-LLM filter and publishable-only pipeline
```

PDF extraction lives in `src/media_quotes/adapters/pdf_extractor.py`, not at the package root.

Key full paths are `src/media_quotes/adapters/pdf_extractor.py`, `src/media_quotes/archive.py`,
`src/media_quotes/backfill.py`, `src/media_quotes/cli/app.py`, `src/media_quotes/collection.py` and
`src/media_quotes/graph/sync.py`.

## Migrations

```text
migrations/versions/
├── 0001_initial.py
├── 0002_gold_set.py
├── 0003_decision_cache.py
├── 0004_topic_language_judge.py
├── 0005_topic_packs.py
├── 0006_digest_topic_unique.py
├── 0007_widen_alembic_version.py
├── 0007_audit_events_monthly_partition.py
├── 0008_enable_tier4.py
└── 0009_source_availability.py
```

The two `0007` files are sequential: `0007_widen_alembic_version.py` defines revision
`0007_widen_version`, and the monthly-partition revision follows it. The active chain then adds
tier-4 claim-only support and operational source-availability fields.

## Scripts

Use these scripts directly from the repository root. None of them is required for normal
`mq` use; they are maintenance, proof and local-verification tools.

| Script | Purpose and when to use | Safe / offline | Main outputs |
|---|---|---|---|
| `scripts/check.ps1` | Full local quality gate before a change is handed off | Safe; local-only, no application data writes | Console results for docs QA, format, lint, types, tests, build and CLI smoke checks |
| `scripts/docs_qa.py` | Check the explanatory docs against the code and each other; also run by `check.ps1` | Safe; standard library, read-only | Pass/fail lines on stdout; exit 1 on any failed check |
| `scripts/test_scraper.py` | Interactive feed, HTML, quote and route diagnostics; `--validate-source` / `--validate-all-sources` for T0 readiness | Offline by default; `--live`, `--live-feed`, `--live-routes` are opt-in network probes | JSON reports on stdout; `--write-sample` writes `var/test-output/samples/article.html` |
| `scripts/source_proof.py` | Per-source JSON/Markdown proof report from fixtures, a supplied sample/URL or opt-in live routes | `--offline` is safe and offline; `--live` and `--url` are network opt-in | `var/test-output/proof/` for fixture-only runs, otherwise `var/output/proof/`, with `source-proof-YYYY-MM-DD.json` and `.md` |
| `scripts/validate_data.py` | Registry, schema and derived-data validation when source or data files change | Safe; read-only local validation | Console validation results and a process exit code |
| `scripts/generate_gold_fixtures.py` | Regenerate synthetic-v1 JSON fixtures and the gold set after an intentional fixture change; use `--check` to detect drift | Safe and deterministic; no network, no database | Generated JSON under `data/fixtures/` and `data/gold_set/synthetic-v1.json`; `--check` writes nothing |
| `scripts/generate_topic_matrix.py` | Regenerate the source x section HTML matrix and synthetic-v2 gold set; use `--check` in CI-style drift checks | Safe and deterministic; no network, no database | HTML under `data/fixtures/`, `data/gold_set/synthetic-v2.json`; `--check` writes nothing |
| `scripts/manual_validate.ps1` | One-date fixture-backed validate -> doctor -> run -> audit sequence without Dagster | Offline; fixture replay skips Postgres and never sets `PERSIST_TO_POSTGRES` | Artifacts under `var/test-output/`, including the digest, quotes and audit for the requested date |
| `scripts/run_all.ps1` | Consolidated turnkey verification from Docker startup through CI, migrations, fixture replay, evals and Dagster materialization | Requires Docker/Postgres for the database steps; pipeline execution remains fixture-backed | `var/test-output/` deliverables plus CI, eval and Dagster console results |
| `scripts/run_dagster.ps1` | Start the Dagster development UI with a local `DAGSTER_HOME` | Local UI; schedules and the sensor are stopped by default | `var/dagster/` runtime files and materializations only when explicitly triggered |
| `scripts/run_dashboard.ps1` | Start the read-only Streamlit dashboard | Safe; local read of existing artifacts | Dashboard process; no new data |
| `scripts/install_check_profile.ps1` | One-time install of a `check` shell function that delegates to `check.ps1` | Safe; edits PowerShell profile files only | A `check` function in Windows PowerShell and PowerShell 7 profiles |

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
- Canonical values live in `enums.py`; do not duplicate them as literals.
- Generated Python modules stay single-purpose; split only on independent change reasons.
