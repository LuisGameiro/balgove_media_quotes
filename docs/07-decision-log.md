# 07 — Decision log

This register records material design decisions, including decisions that predate the current
explanatory document set. It does not replace the assignment in [`Test_News.md`](Test_News.md).
Status describes the current checkout, not historical intent.

| ID | Decision | Rationale | Enforced or implemented in | Current status |
|---|---|---|---|---|
| DEC-01 | Reuters and BBC remain mandatory behind the T0 rights gate | Preserve the brief while preventing unapproved retrieval | `registry.py`, `fetch.py`, `db/schema.sql` | Active; Reuters enabled but its route returns HTTP 401 |
| DEC-02 | Digest is internal-only | Verbatim republication requires rights review | `config.py`, dashboard authentication | Partial: territory is not renderer-enforced |
| DEC-03 | Use a versioned gold set with recall, precision and κ targets | Make “material” measurable rather than assumed | `gold_set.py`, `evaluation.py` | Active; production evidence pending |
| DEC-04 | Use Dagster for daily partitions and asset checks | Repeatable scheduling and auditable runs | `orchestration.py` | Defined; schedules stopped and explicitly live |
| DEC-05 | Pin LLM snapshots and cache decisions by content and prompt/schema versions | Temperature zero is not determinism | `llm_live.py`, `llm_openrouter.py`, `decision_cache` | Active; live use requires Postgres |
| DEC-06 | Keep evaluation in-house with strict Pydantic contracts | Avoid an external eval-service dependency | `evaluation.py`, `cli/eval.py` | Active |
| DEC-07 | Use structured JSON logs, redaction and optional OTLP tracing | Trace every article without leaking secrets | `observability.py` | Active; collector deployment pending |
| DEC-08 | Use static extraction first and optional rendered JS and PDF fallbacks | Handle JS-heavy pages and documents without making every run heavy | `extraction.py`, `pdf_extractor.py`, source extractor hints | Partial: JS supported; PDF extraction exists but is unvalidated on production routes |
| DEC-09 | Keep Neo4j as an optional derived projection | Relational Postgres remains authoritative | `graph/` | Active, opt-in |
| DEC-10 | Cap original quote text at 30 words and require attribution | Conservative licence-limit policy; translations are separate provenance and do not change the cap | `rights_guard.py`, `verification_stages.py` | Word cap and attribution enforced on the verbatim original; territory metadata only |
| DEC-11 | Balgove Innovations owns sign-off and counsel reviews rights | Clear accountability for source admission | Registry notes and operator process | Active |
| DEC-12 | Calibrate judge agreement against human gold labels | LLM judgement is not ground truth | `judge.py`, `gold_set.py` | Active; held-out human evidence pending |
| DEC-13 | Keep state-controlled material claim-only; tier-4 enablement is allowed only with claim-only | Prevent narrative claims from becoming established facts | Registry policy and schema constraints | Active; all tier-4 rows enabled as claim-only |
| DEC-14 | Treat licence risk as resolved only through recorded approval | Automated access remains fail-closed | `license_reference`, T0 gate | Rights recorded for all 28; grants not independently verified |
| DEC-15 | Use Alembic rather than raw SQL bootstrap | Versioned, reviewable schema changes | `bootstrap.py`, `migrations/` | Active |
| DEC-16 | Enforce uv, ruff, strict mypy and 85% branch coverage | Consistent engineering baseline | `Makefile`, `.github/workflows/ci.yml` | Active |
| DEC-17 | Require evidence, human review, a 24-hour SLA and drift guard for obsolescence | Avoid hiding quotes on weak model judgement | `risk_controls/obsolescence.py`, `pipeline.py`, `digest.py` | Partial: daily proposals are wired; live-judge accuracy, SLA evidence and held-out labels remain pending |
| DEC-18 | Estimate and cap backfill cost, then prefer cached decisions | Prevent surprise provider spend | `risk_controls/budget.py`, `cli/ops.py`, `archive.py` | Active for bounded fixture/blob/archive partitions; the initial production backfill has been executed, its audit remains pending |
| DEC-19 | Partition audit events and bound raw/log retention | Control growth without losing audit evidence | Migration `0007`, `retention.py`, `blob_prune.py` | Partial: production drill pending |
| DEC-20 | Treat per-source JSON configs as authoritative and CSV as a mirror | One structured source of registry truth | `source_configs.py` | Active |
| DEC-21 | Default to offline fixture replay and local JSONL | Safe, deterministic development without secrets or network | `pipeline.py`, `export.py` | Active |
| DEC-22 | Use versioned topic rules and deterministic dedupe | Keep selection explainable and repeatable | `relevance.py`, `curation.py` | Active |
| DEC-23 | Keep the dashboard read-only and password-gated | Reduce exposure of internal quotes and licence metadata | `dashboard.py` | Active; database access is row counts only |
| DEC-24 | Probe registered feed URLs and classify XML, JSON and HTML; route approval is not runtime health | Prevent false failures from homepages and false passes from HTTP 200 | `scripts/test_scraper.py` | Active; probe results are in the `scripts/test_scraper.py --validate-all-sources --live-routes` output, not the proof report |
| DEC-25 | Enable all tiers for validation while keeping tier-4 and state-controlled material claim-only | Owner requested all-source validation without policy blocks | Source configs, validation rules, migration `0008` | Active; 28 enabled, 0 T0-blocked |
| DEC-26 | Use official public feeds/sitemaps first, Bing News for original URL discovery second, and labelled fixtures only as non-collected software proof | Maximize real-source coverage without API keys or access bypasses | `scripts/source_proof.py`, `bing_news.py`, source configs | Active; 15 sources collected data and 76 live quotes on 2026-11-24 |
| DEC-27 | Track operational availability separately from editorial tier; use Bing News only for URL discovery and never count fixtures as collected data | Keep blocked, partial and discovery-only sources visible without overstating coverage | Migration `0009`, `bing_news.py`, `source_proof.py` | Active; 15 sources collected data after blocked-source enforcement on 2026-11-24 |
| DEC-28 | Use language-aware discovery queries, prevent quotes spanning paragraph breaks, reject weekdays as speakers and allow configured institutional byline fallbacks | Improve verified quote yield without inventing attribution or metadata | `source_proof.py`, `quote_extractor.py`, `html_extractor.py`, source configs | Active; Ukrainska Pravda and Xinhua became live-verified |
| DEC-29 | Use one shared acquisition service and enforce `tier_block` before network access | Prevent divergent fetch behavior and accidental collection from blocked sources | `collection.py`, `live.py`, `cli/fetch.py`, `test_scraper.py`, `source_proof.py` | Active; feed/URL/Bing paths share rights, redirect, date and error policy |
| DEC-30 | Isolate synthetic fixture and offline-proof artifacts under `var/test-output/` | Keep test data out of operational dashboards, exports and configured persistence | `config.py`, `pipeline.py`, `source_proof.py` | Active; fixture runs skip configured Postgres persistence unless explicitly requested |
| DEC-31 | Make `mq run` live-by-default with explicit cached-blob and fixture modes | Operational runs use real source data while tests remain reproducible and isolated | `cli/app.py`, `pipeline.py`, `collection.py` | Active; `--from-blobs` is offline cached ingest and `--fixtures` writes test output |
| DEC-32 | Share historical backfill execution between CLI and a manual single-run Dagster partition backfill | Preserve budget/archive safeguards while giving operators Dagster run history and partition state | `backfill.py`, `orchestration.py` | Active; live archive is the range default, `max_cost` is required, and fixtures/dry-run are rejected for ranges |
| DEC-33 | Make the Neo4j projection a first-class Dagster asset with its own checks | Graph freshness must be verifiable independently of the daily digest | `orchestration.py`, `graph/sync.py` | Active; the asset raises on a missing/empty export or projection errors, and three content-hash and freshness checks skip green for fixture or disabled partitions |
| DEC-34 | Keep digest rendering verbatim with enforced quality thresholds and hard exclusions | Protect fidelity while making topic, language and category coverage measurable and keeping fixture-only and non-first-hand material out of facts | `digest.py`, `evaluation.py`, `rights_guard.py`, `scripts/source_proof.py` | Active; five topic/language/category metrics are enforced when measured, while whitespace preservation and live-judge calibration remain open |

Proof counts in DEC-26 and DEC-27 are volatile: see
`var/output/proof/source-proof-YYYY-MM-DD.json` and `.md` for the current run. DEC-24 probe results
come from the scraper validator output instead.

## Referenced checks

| ID | Check | Enforced / tested in | Current state |
|---|---|---|---|
| CHK-01 | Doctor validates config, registry, schema, versions and sync | `src/media_quotes/registry.py`, `src/media_quotes/orchestration/__init__.py` (`t0_rights_boundary_enforced`), `tests/unit/test_registry.py` | Implemented |
| CHK-03 | Gold-set metrics meet the acceptance contract | `src/media_quotes/evaluation.py`, `src/media_quotes/cli/eval.py`, `src/media_quotes/orchestration/__init__.py` (`gold_set_thresholds_met`), `tests/unit/test_eval_extended.py` | Implemented; exact-span target differs from default gate |
| CHK-15 | Every obsolescence flag has evidence, verdict and SLA | `src/media_quotes/risk_controls/obsolescence.py`, `src/media_quotes/orchestration/__init__.py` (`review_queue_sla_sensor`), `tests/unit/risk_controls/test_policies.py` | Policy implemented; production evidence pending |
| CHK-16 | Backfill estimate, cache and budget controls | `src/media_quotes/risk_controls/budget.py`, `src/media_quotes/backfill.py`, `tests/unit/risk_controls/test_policies.py`, `tests/unit/test_backfill.py` | Policy implemented; three real runs pending |
| CHK-17 | Retention, partition, transaction and restore controls | `src/media_quotes/risk_controls/retention.py`, `src/media_quotes/blob_prune.py`, `src/media_quotes/orchestration/__init__.py`, `tests/unit/risk_controls/test_policies.py` | Partial; live load and cold-partition checks pending |
| CHK-19 | Rights limits reject over-length or unattributed quotes | `src/media_quotes/rights_guard.py`, `src/media_quotes/orchestration/__init__.py` (`quote_rights_limits_respected`), `tests/unit/test_rights_guard.py` | Implemented for word count and attribution; territory not enforced |
| CHK-20 | Live validation uses the registered URL and content-specific parser | `scripts/test_scraper.py`, `tests/unit/test_newspaper_scraping.py`, `tests/unit/test_all_sources_matrix.py` | Regression tests pass; real route health remains source-dependent |
