# 12 — Implementation start plan

This is the bridge from the planning docs to code. The scaffold already contains: strict
models, registry/validation, Alembic bootstrap, 13 CLI command parsers, offline fixtures, the
risk-control policy layer and a green 146-test suite at 90% coverage. What remains is the
**real pipeline** behind those commands. This plan sequences that work.

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
| A2 | Gold set v1 from `data/gold_set/synthetic-v1.json` → versioned `gold_set_version`, 60/20/20 split stored in Postgres via Alembic revision 0002 | CHK-03 |
| A3 | Acceptance threshold fixtures (100% exact-match, 0 false-quote, recall/κ targets) wired into `mq eval` | CHK-03 |
| A4 | Revisit DEC-14 only on licence decision; until then no source enablement | 04 register |

## 2. Phase B — T1/T10 foundation hardening (parallel, 1–2 sessions)

| Step | Work | Exit check |
|---|---|---|
| B1 | Alembic revision for append-only tables + monthly partitioning of `audit_events`, `llm_decisions`, `verifier_results` (RSK-11 plan §3) | CHK-17 |
| B2 | Wire `risk_controls.build_cache_key()` into a `decision_cache` repository method (idempotent upsert) | CHK-16 |
| B3 | OTel local collector config + structlog redaction processor into `configure_observability` | CHK-13 |
| B4 | `uv.lock` + `make check` already present; ensure CI runs it on every PR | CHK-10/11 |

## 3. Phase C — T2/T3 ingestion vertical slice (2–3 sessions)

| Step | Work | Exit check |
|---|---|---|
| C1 | `fetch` adapter using async httpx with `Timeout`/`Limits`/token bucket; VCR cassette fixture replay only; dead-letter + coverage rows | CHK-05/09 |
| C2 | Trafilatura extraction with §4.1 params, DOM offsets; quality gate → Playwright fallback | CHK-07 |
| C3 | Verifier: exact-span + attribution + metadata + first-hand evidence; `unverified` never published | CHK-02 |
| C4 | First "blocked/degraded" `mq run --offline` producing a digest that explicitly lists Reuters/BBC as gaps | CHK-01/02 |

## 4. Phase D — T4/T5 reasoning and obsolescence (2–3 sessions)

| Step | Work | Exit check |
|---|---|---|
| PD1 | LLM boundary with pinned snapshot, strict structured output, provider response id, refusal/incomplete logging, cache lookup before call | CHK-02 |
| PD2 | Batch API path for backfills using `custom_id`; budget gate via `risk_controls.budget_gate` + `--max-cost` on `mq backfill` | CHK-16 |
| PD3 | Section assignment from allow-list; obsolescence rule with evidence bundle and `risk_controls.ReviewQueue` | CHK-15 |
| PD4 | Held-out eval: obsolete precision ≥ 0.90 and drift guard (`obsoletion_allowed`) | CHK-03 |

## 5. Phase E — T6/T9 presentation and operator UX (1–2 sessions)

| Step | Work | Exit check |
|---|---|---|
| E1 | Jinja2 digest from `digests/` template with verbatim quotes + DEC-10 limits + snapshots | CHK-04 |
| E2 | Dashboard: latest/archive digests, per-source coverage view, logs, DB explorer (read-only) | CHK-05/06 |
| E3 | Complete CLI commands (`audit`, `logs`, `eval`, `run`, `sources`, `version`) end-to-end with exit codes 0–3 | CHK-01/02/06 |

## 6. Phase F — T8 quality gate & closure (continuous, final)

| Step | Work | Exit check |
|---|---|---|
| F1 | Prompt-injection suite (RSK-13, CHK-18) and rights/limits guard (CHK-19) | CHK-18/19 |
| F2 | Retention job + load test (60s lock bound) + restore drill | CHK-17 |
| F3 | `mq eval` full gold-set metrics; all §7 thresholds met | CHK-03 |
| F4 | Once CHK-15/16/17 pass: close RSK-07/08/11, collapse [10](10-risk-mitigation-plan.md) to a short closed-items note in [08](08-traceability.md) §8 | docs QA |

## 7. Definition of "implementation started"

Phase A1–A3 plus Phase C1–C4 complete = the system can run one **offline, rights-compliant**
daily digest end-to-end with audit/log artifacts and explicit mandatory-source gaps. That is
the first real deliverable against the minimum brief §3–§4; everything after is hardening and
plus-scope.

## 8. Immediate next action

Start **A1**: add DEC-10 limit settings and tests; then A2/A3 to lock acceptance. Do not touch
live sources until the licence decision tracked by DEC-14 is recorded.
