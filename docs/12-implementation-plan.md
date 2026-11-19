# 12 — Implementation start plan

This is the bridge from the planning docs to code. The scaffold already contains: strict
models, registry/validation, Alembic revision `0001`, 12 CLI commands with deterministic
file-based offline handlers, OTel/structlog secret redaction (`observability.py`), the
risk-control policy layer (`risk_controls.py`) and a green 146-test suite at 90%+ coverage.
What remains is cutting over from the file-based stand-in to **PostgreSQL-backed persistence**
(`db/repository.py`), **live ingestion/LLM adapters**, and **Dagster daily partitioned
orchestration** (`DEC-04`). This plan sequences that work.

**Notation:** "T0 gate" = the rights gate in brief §1; task `T0` = requirements task in
[03](03-work-breakdown.md). Owner of all work: **Balgove Innovations**. Task IDs and check
refs come from [03](03-work-breakdown.md) and [08](08-traceability.md).

## 0. Guiding rules for implementation

1. **Never activate a blocked source.** T0 gate stays fail-closed; fixtures only until rights.
2. **Add one vertical slice at a time** with its unit + contract tests, not a "big bang".
3. **Every boundary is strict** (Pydantic v2 strict, `extra="forbid"`); log validation failures.
4. **No unverified quote reaches the digest.** The deterministic verifier is the gate.
5. **Each slice ends with `make check`** (ruff, mypy, tests ≥85%, docs QA) green.

## 1. Phase A — T0 rights gate & acceptance completion (first, 1–2 sessions)

| Step | Work | Exit check |
|---|---|---|
| A1 | Confirm DEC-10 limits in renderer config; add `max_quote_words=30`, attribution format, territory to `Settings`/config (not to the frozen schema) | CHK-01 |
| A2 | Gold set v1 from `data/gold_set/synthetic-v1.json` → versioned `gold_set_version`, 60/20/20 split stored in Postgres via Alembic revision `0002` | CHK-03 |
| A3 | Cut over `mq eval` acceptance threshold checks (100% exact-match, 0 false-quote, recall/κ targets) from file-only fixtures to Postgres-backed evaluation runs | CHK-03 |
| A4 | Execute Two-Track source review ([07](07-sources.md) §3): fast-track Track A open/API sources (`guardian_api`, `gdelt`, `suspilne`, `kyivindependent`) for counsel sign-off; hold Track B (`reuters`, `bbc`, paywalls) blocked on fixtures until `DEC-14` licence decision | 04 register |

## 2. Phase B — T1/T10 foundation hardening (parallel, 1–2 sessions)

| Step | Work | Exit check |
|---|---|---|
| B1 | Alembic revision `0003` for append-only privileges + monthly partitioning of `audit_events`, `llm_decisions`, `verifier_results` (RSK-11 plan §3) | CHK-17 |
| B2 | Wire `risk_controls.build_cache_key()` into a `decision_cache` repository method (idempotent upsert) | CHK-16 |
| B3 | Wire `configure_observability` (structlog redaction + OTLP span exporter in `observability.py`) into live Postgres/Dagster pipeline runs and local OTel collector | CHK-13 |
| B4 | `uv.lock` + `make check` already present; ensure CI runs it on every PR | CHK-10/11 |

## 3. Phase C — T2/T3 ingestion vertical slice (2–3 sessions)

| Step | Work | Exit check |
|---|---|---|
| C1 | `fetch` adapter using async httpx with `Timeout`/`Limits`/token bucket; VCR cassette replay for blocked Track B fixtures + live fetch for approved Track A sources; dead-letter + coverage rows persisted in Postgres | CHK-05/09 |
| C2 | Trafilatura extraction with §4.1 params, DOM offsets; quality gate → Playwright fallback | CHK-07 |
| C3 | Two-stage verifier persisted to Postgres: Stage 1 pre-LLM candidate filter (drops zero-quote boilerplate before LLM spend) + Stage 2 post-LLM exact-span, speaker, metadata and first-hand evidence gate; `unverified` never published | CHK-02 |
| C4 | Cut over `mq run --offline` and Dagster daily partitioned assets (`DEC-04`) from file-only stand-ins to Postgres-persisted runs producing a degraded digest that explicitly lists blocked Tier 1 sources (Reuters/BBC) as coverage gaps | CHK-01/02 |

**C1/C2/C3 progress:** `fetch.py` (rights-gated async httpx), `extraction.py` (strict
`ExtractionParams`, fail-closed quality gate, trafilatura Document/dict wrapper, injectable
fallback hook, `find_span()` for exact offsets) and `verification_stages.py` (Stage 1 pre-LLM
junk/word-limit/duplicate filter + Stage 2 existing `verify_quote`, publishable-only output)
are implemented with offline tests. No source is activated; wiring into `mq run` remains C4.

## 4. Phase D — T4/T5 reasoning and obsolescence (2–3 sessions)

| Step | Work | Exit check |
|---|---|---|
| PD1 | Live OpenAI boundary with pinned snapshot, strict structured output (preserving native `quote_text` + generating English `quote_text_en` translation), provider response id, refusal/incomplete logging, and Postgres `decision_cache` lookup before call | CHK-02 |
| PD2 | Batch API path for backfills using `custom_id`; wire `risk_controls.budget_gate` + `--max-cost` on `mq backfill` to Postgres `pipeline_runs` | CHK-16 |
| PD3 | Section assignment from 16-slug allow-list, cross-outlet quote deduplication (14-day window), and obsolescence rule with evidence bundle + `risk_controls.ReviewQueue` (24h `provisional_hold` `[Under Review]` badge) | CHK-15 |
| PD4 | Held-out eval: obsolete precision ≥ 0.90 and drift guard (`obsoletion_allowed`) persisted in Postgres | CHK-03 |

## 5. Phase E — T6/T9 presentation and operator UX (1–2 sessions)

| Step | Work | Exit check |
|---|---|---|
| E1 | Jinja2 digest from `digests/` template with verbatim quotes (`quote_text` + `quote_text_en`), DEC-10 limits, `[Under Review]` provisional-hold badges and snapshots | CHK-04 |
| E2 | Dashboard: latest/archive digests, per-source coverage view, review queue, logs, DB explorer (read-only against Postgres) | CHK-05/06 |
| E3 | Cut over all 12 CLI commands (`audit`, `logs`, `eval`, `run`, `sources`, `version`, `backfill`, `digest`, `export`, `dashboard`, `doctor`, `validate`) to live Postgres/Dagster backends with exit codes 0–3 | CHK-01/02/06 |

## 6. Phase F — T8 quality gate & closure (continuous, final)

| Step | Work | Exit check |
|---|---|---|
| F1 | Prompt-injection suite (RSK-13, CHK-18) and rights/limits guard (CHK-19) | CHK-18/19 |
| F2 | Retention job + load test (60s lock bound) + restore drill | CHK-17 |
| F3 | `mq eval` full gold-set metrics; all §7 thresholds met | CHK-03 |
| F4 | Once CHK-15/16/17 pass on live Postgres/load tests: record production verification evidence in [08](08-traceability.md) §8 and [10](10-risk-mitigation-plan.md) while retaining required `RSK-07/08/11` and `CHK-15/16/17` tokens so `scripts/docs_qa.py` (`CHK-08`) stays green | docs QA |

## 7. Definition of "implementation started"

Phase A1–A4 plus Phase C1–C4 complete = the system has cut over from the file-based mock to a
**Postgres-persisted, Dagster-orchestrated, rights-compliant** daily digest end-to-end with
audit/log artifacts, Track A source readiness, and explicit mandatory-source gaps for blocked
Tier 1 sources. That is the first production-backed deliverable against the minimum brief
§3–§4; Phases D–F complete live LLM reasoning, operator UX and production hardening.

## 8. Immediate next action

Start **A1**: add DEC-10 limit settings and tests; then **A2/A3** to lock Postgres-backed
acceptance evaluation and **A4** to submit the Track A open/API source review packet while
holding Track B commercial sources blocked until `DEC-14` is recorded.
