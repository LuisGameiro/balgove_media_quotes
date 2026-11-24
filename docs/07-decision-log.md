# 07 — Decision log

This register records material design decisions, including decisions that predate the current
explanatory document set. It does not replace the assignment in [`Test_News.md`](Test_News.md).
Status describes the current checkout, not historical intent.

| ID | Decision | Rationale | Enforced or implemented in | Current status |
|---|---|---|---|---|
| DEC-01 | Reuters and BBC remain mandatory behind the T0 rights gate | Preserve the brief while preventing unapproved retrieval | `registry.py`, `fetch.py`, `db/schema.sql` | Active; Reuters enabled but its route returns HTTP 401 |
| DEC-02 | Digest is internal-only | Verbatim republication requires rights review | `config.py`, dashboard authentication | Active; territory remains metadata-only under DEC-10 |
| DEC-03 | Use a versioned gold set with recall, precision and κ targets | Make “material” measurable rather than assumed | `gold_set.py`, `evaluation.py` | Active; production evidence pending |
| DEC-04 | Use Dagster for daily partitions and asset checks | Repeatable scheduling and auditable runs | `orchestration.py` | Active; schedules stopped by default but issue `source_mode=live` run requests when enabled |
| DEC-05 | Pin LLM snapshots and cache decisions by content and prompt/schema versions | Temperature zero is not determinism | `llm_live.py`, `llm_openrouter.py`, `decision_cache` | Active; live use requires Postgres |
| DEC-06 | Keep evaluation in-house with strict Pydantic contracts | Avoid an external eval-service dependency | `evaluation.py`, `cli/eval.py` | Active |
| DEC-07 | Use structured JSON logs, redaction and optional OTLP tracing | Trace every article without leaking secrets | `observability.py` | Active; collector deployment pending |
| DEC-08 | Use static extraction first and optional rendered JS fallback | Handle JS-heavy pages without making every run heavy | `extraction.py`, `adapters/pdf_extractor.py`, source extractor hints | Active; JS and PDF supported, production route validation pending |
| DEC-09 | Keep Neo4j as an optional derived projection | Relational Postgres remains authoritative | `graph/` | Active, opt-in |
| DEC-10 | Cap quotes at 30 words and require attribution; treat territory as metadata only | Conservative licence-limit policy without unsupported geographic enforcement | `rights_guard.py`, `verification_stages.py`, `config.py` | Word cap and attribution enforced; territory metadata-only by decision |
| DEC-11 | Balgove Innovations owns sign-off and counsel reviews rights | Clear accountability for source admission | Registry notes and operator process | Active |
| DEC-12 | Calibrate judge agreement against human gold labels | LLM judgement is not ground truth | `judge.py`, `gold_set.py` | Active; held-out human evidence pending |
| DEC-13 | Keep state-controlled material claim-only; tier-4 enablement is allowed only with claim-only | Prevent narrative claims from becoming established facts | Registry policy and schema constraints | Active; all tier-4 rows enabled as claim-only |
| DEC-14 | Treat licence risk as resolved only through recorded approval | Automated access remains fail-closed | `license_reference`, T0 gate | Rights recorded for all 28; grants not independently verified |
| DEC-15 | Use Alembic rather than raw SQL bootstrap | Versioned, reviewable schema changes | `bootstrap.py`, `migrations/` | Active |
| DEC-16 | Enforce uv, ruff, strict mypy and 85% branch coverage | Consistent engineering baseline | `Makefile`, `.github/workflows/ci.yml` | Active |
| DEC-17 | Require evidence, human review, a 24-hour SLA and drift guard for obsolescence | Avoid hiding quotes on weak model judgement | `risk_controls/obsolescence.py`, `pipeline.py`, `digest.py` | Daily proposals wired with same-speaker gating and atomic persistence; production SLA and held-out precision evidence pending |
| DEC-18 | Estimate and cap backfill cost, then prefer cached decisions | Prevent surprise provider spend | `risk_controls/budget.py`, `cli/ops.py`, `backfill.py` | Active for fixture/blob partitions; live archive is paid-disabled pending actual-call accounting |
| DEC-19 | Partition audit events and bound raw/log retention | Control growth without losing audit evidence | Migration `0007`, `retention.py`, `blob_prune.py` | Partial: production drill pending |
| DEC-20 | Treat per-source JSON configs as authoritative and CSV as a mirror | One structured source of registry truth | `source_configs.py` | Active |
| DEC-21 | Default to offline fixture replay and local JSONL | Safe, deterministic development without secrets or network | `pipeline.py`, `export.py` | Active |
| DEC-22 | Use versioned topic rules and deterministic dedupe | Keep selection explainable and repeatable | `relevance.py`, `curation.py` | Active |
| DEC-23 | Keep the dashboard read-only and password-gated | Reduce exposure of internal quotes and licence metadata | `dashboard.py` | Active; governed provenance-tagged JSONL browser and read-only Postgres counts |
| DEC-24 | Probe registered feed URLs and classify XML, JSON and HTML; route approval is not runtime health | Prevent false failures from homepages and false passes from HTTP 200 | `scripts/test_scraper.py` | Active; 11 live-verified, others partial or blocked on 2026-11-24 |
| DEC-25 | Enable all tiers for validation while keeping tier-4 and state-controlled material claim-only | Owner requested all-source validation without policy blocks | Source configs, validation rules, migration `0008` | Active; 28 enabled, 0 T0-blocked |
| DEC-26 | Use official public feeds/sitemaps where available and fall back to labelled fixtures or supplied samples | Maximize proof coverage without API keys or access bypasses | `scripts/source_proof.py`, source configs | Active; 11 live-verified, 10 partial, seven fixture fallbacks on 2026-11-24 |
| DEC-27 | Isolate acquisition provenance and stamp it on stored artifacts | Fixture replay must never be mistaken for live evidence; provenance travels with the record | `enums.py`, `db/schema.sql`, `export.py`, `gold_set.py` | Active; `fixture`/`live_feed`/`live_bing`/`manual_sample` recorded on articles, quotes, exports, digests and review queues; live mode never falls back to fixtures |
| DEC-28 | Treat registered official archives as the authoritative history source | Reconstruct 2026-10-01 onward from enumerable feeds/sitemaps rather than guessed URLs | `archive.py`, `backfill.py`, `cli/common.py` | Implemented: bounded sitemap-index recursion, date filtering, resumable atomic manifests, `--source-id`, 365-day cap, paid-mode fail-closed; full production backfill not executed |
| DEC-29 | Keep admission probes read-only: probing grants no rights and enables no source | Automated checks inform manual review and cannot bypass the T0 gate | `admission.py`, `cli/ops.py` | Active; `add --probe --live` and `probe --source-id --live` are bounded, SSRF-safe, admission-only probes |

## Referenced checks

| ID | Check | Current state |
|---|---|---|
| CHK-01 | Doctor validates config, registry, schema, versions and sync | Implemented |
| CHK-03 | Gold-set metrics meet the acceptance contract | Implemented; exact-span target differs from default gate |
| CHK-15 | Every obsolescence flag has evidence, verdict and SLA | Policy and daily proposals implemented; production evidence pending |
| CHK-16 | Backfill estimate, cache and budget controls | Policy implemented; three real runs pending |
| CHK-17 | Retention, partition, transaction and restore controls | Partial; live load and cold-partition checks pending |
| CHK-19 | Rights limits reject over-length or unattributed quotes | Implemented for word count and attribution; territory metadata-only by DEC-10 |
| CHK-20 | Live validation uses the registered URL and content-specific parser | Regression tests pass; real route health remains source-dependent |
