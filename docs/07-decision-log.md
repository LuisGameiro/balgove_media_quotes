# 07 — Decision log

Material design decisions, including ones made before this document set existed. Status describes
the current checkout. [`Test_News.md`](Test_News.md) remains the assignment.

A row reads **Active** unless the status says otherwise — the exceptions (stopped schedules,
pending evidence, partial rollout) are the ones worth reading. Paths in **Where** are relative to
`src/media_quotes/` unless they start with `scripts/`, `data/` or `docs/`.

| ID     | Decision                                                               | Why                                                 | Where                                             | Status                                               |
| ------ | ---------------------------------------------------------------------- | --------------------------------------------------- | ------------------------------------------------- | ---------------------------------------------------- |
| DEC-01 | Reuters and BBC stay mandatory behind T0                               | Preserve the brief without unapproved retrieval     | `registry.py`, `src/media_quotes/ingestion/fetch.py`, schema                 | Active; Reuters returns HTTP 401                     |
| DEC-02 | Digest is internal-only                                                | Verbatim republication needs rights review          | `config.py`, dashboard auth                       | Active; territory is metadata only                   |
| DEC-03 | Versioned gold set with recall, precision and κ targets                | Make "material" measurable                          | `src/media_quotes/governance/gold_set.py`, `src/media_quotes/governance/evaluation.py`                    | Active; production evidence pending                  |
| DEC-04 | Dagster for daily partitions and checks                                | Repeatable, auditable runs                          | `orchestration.py`                                | Active; schedules stopped by default                 |
| DEC-05 | Pin model snapshots and cache by content/version                       | Temperature zero is not determinism                 | `src/media_quotes/ai/llm_live.py`, `src/media_quotes/ai/llm_openrouter.py`                | Active; live use needs Postgres                      |
| DEC-06 | Keep evaluation in-house with strict Pydantic contracts                | Avoid an external eval service                      | `src/media_quotes/governance/evaluation.py`, `cli/eval.py`                    | Active                                               |
| DEC-07 | JSON logs, redaction and optional OTLP                                 | Trace articles without leaking secrets              | `observability.py`                                | Active; collector deployment pending                 |
| DEC-08 | Static extraction first, rendered JS optional                          | Handle JS pages without weighing every run          | `src/media_quotes/processing/extraction.py`, PDF extractor                    | Active; route validation pending                     |
| DEC-09 | Neo4j is an optional derived projection                                | Postgres stays authoritative                        | `graph/`                                          | Active, opt-in                                       |
| DEC-10 | Cap quotes at 30 words, require attribution, territory is metadata     | Conservative licence policy                         | `src/media_quotes/governance/rights_guard.py`, `config.py`                    | Active; word cap also applied to translated text     |
| DEC-11 | Balgove Innovations owns rights sign-off                               | Clear accountability                                | Registry notes and operator process               | Active                                               |
| DEC-12 | Calibrate judge agreement on human labels                              | LLM judgement is not ground truth                   | `src/media_quotes/ai/judge.py`, `src/media_quotes/governance/gold_set.py`                         | Active; evidence pending                             |
| DEC-13 | State-controlled and tier-4 material is claim-only                     | Prevent perspective becoming fact                   | Registry policy and schema                        | Active; six sources claim-only                       |
| DEC-14 | Licence risk needs recorded approval                                   | Automated access stays fail-closed                  | `license_reference`, T0 gate                      | All 28 recorded; grants unverified                   |
| DEC-15 | One consolidated idempotent SQL schema instead of Alembic              | Reviewable SQL, no chain to drift                   | `bootstrap.py`, `migrations/schema.sql`           | Active; backup/restore is rollback                   |
| DEC-16 | uv, ruff, strict mypy and 85% branch coverage                          | Consistent engineering baseline                     | `Makefile`, `scripts/ci/check.ps1`, CI            | Active                                               |
| DEC-17 | Obsolescence needs evidence, review and a 24-hour SLA                  | Avoid hiding quotes on weak judgement               | `risk_controls/obsolescence.py`, `pipeline.py`    | Daily proposals wired; evidence pending              |
| DEC-18 | Estimate backfill cost and prefer cached decisions                     | Avoid surprise spend                                | `risk_controls/budget.py`, `cli/ops.py`           | Estimate only; it cannot cap provider spend          |
| DEC-19 | Partition audit events and bound retention                             | Control growth without losing evidence              | `migrations/schema.sql`, `retention.py`           | Partitioning live; drill pending                     |
| DEC-20 | Source JSON configs are authoritative; CSV is a mirror                 | One structured source of truth                      | `source_configs.py`                               | Active                                               |
| DEC-21 | Default to offline fixtures and local JSONL                            | Safe deterministic development                      | `pipeline.py`, `src/media_quotes/processing/export.py`                        | Active                                               |
| DEC-22 | Versioned topic rules and deterministic dedupe                         | Explainable selection                               | `src/media_quotes/governance/relevance.py`, `src/media_quotes/processing/curation.py`                     | Active                                               |
| DEC-23 | Dashboard is read-only and password-gated                              | Reduce exposure of internal quotes                  | `dashboard.py`                                    | Active                                               |
| DEC-24 | Probe registered URLs; approval is not health                          | Avoid false failures from homepages                 | `scripts/diagnostics/test_scraper.py`             | Active; 11 live-verified on 2026-11-24               |
| DEC-25 | Enable all tiers for validation, claim-only where required             | Owner asked for all-source validation               | Source configs, validation rules                  | Active; 28 enabled, 0 T0-blocked                     |
| DEC-26 | Proof runner uses fixtures or supplied samples when a live route fails | Maximise proof coverage without keys                | `scripts/diagnostics/source_proof.py`             | Active; proof reports label evidence                 |
| DEC-27 | Pipelines stamp provenance and never use fixtures as live fallback     | Fixtures must not be mistaken for live evidence     | `enums.py`, schema, `src/media_quotes/processing/export.py`                   | Active                                               |
| DEC-28 | Registered official archives are the history source                    | Reconstruct from enumerable feeds, not guessed URLs | `src/media_quotes/ingestion/archive.py`, `src/media_quotes/ingestion/backfill.py`                       | Implemented; full backfill not executed              |
| DEC-29 | Admission probes grant no rights and enable nothing                    | Automated checks cannot bypass T0                   | `src/media_quotes/ingestion/admission.py`, `cli/ops.py`                      | Active                                               |
| DEC-30 | Canonical artifacts belong to complete live runs                       | Partial runs must not masquerade as canonical       | `cli/common.py`, `src/media_quotes/processing/export.py`, repository          | Active; scoped runs use `runs/<scope>/`              |
| DEC-31 | Fail closed on missing sign-off and zero mandatory quotes              | Gaps are not evidence                               | `source_configs.py`, `pipeline.py`, `cli/eval.py` | Active                                               |
| DEC-32 | Retention is observe-only without live Postgres months                 | Never fabricate a detach plan                       | `cli/maintenance.py`, `retention.py`              | Active                                               |
| DEC-33 | Filter legacy unlabelled artifacts at read time                        | Tagged trees are authoritative                      | `dashboard.py`                                    | Active                                               |
| DEC-34 | Pin the DNS-validated IP to the connection used by the fetch path       | An advisory check cannot stop DNS rebinding         | `src/media_quotes/ingestion/fetch.py`, `src/media_quotes/ingestion/live.py`, `src/media_quotes/ingestion/archive.py`                | Active; Host and SNI preserved                        |
| DEC-35 | Gate Dagster automation behind environment flags                        | Keep live schedules opt-in in every deployment      | `config.py`, `orchestration.py`                    | Active; flags default to false                        |

DEC-26 applies only to the proof report. DEC-27 governs the collection pipeline: a live run never
falls back to fixtures.

## Referenced checks

| ID | Check | Current state |
| --- | --- | --- |
| CHK-01 | Doctor validates config, registry, schema and versions | Implemented |
| CHK-03 | Gold-set metrics meet the acceptance contract | Implemented; exact-span target differs from gate |
| CHK-15 | Obsolescence flags have evidence, verdict and SLA | Policy wired; production evidence pending |
| CHK-16 | Backfill estimate, cache and budget controls | Estimate works; real runs pending |
| CHK-17 | Retention, partition, transaction and restore controls | Partial; load and cold-partition checks pending |
| CHK-19 | Rights limits reject over-length or unattributed quotes | Original and translated text enforced |
| CHK-20 | Live validation uses the registered URL and content parser | Regression tests pass; route health varies |
