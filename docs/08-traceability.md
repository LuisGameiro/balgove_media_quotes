# 08 — Traceability

Single place where requirements, decisions, tasks and checks are joined. **Record changes
here** (plus the register row in [04](04-risks-and-open-questions.md)) instead of adding
prose notes to [01–03](README.md) — those documents stay declarative.

**Notation:** \"T0 gate\" = the rights gate in brief §1; task `T0` = the first task in
[03](03-work-breakdown.md). Statuses: **solved** / **accepted** (residual real-world
constraint) / **not adopted** (deliberately excluded). All are decided in
[04](04-risks-and-open-questions.md); there are no open questions left. Statuses are design
decisions, not runtime proof — checks move from defined to passing as T0–T10 are implemented.

## 1. Check registry (CHK)

| ID | Check | Where |
|---|---|---|
| CHK-01 | `mq doctor`: config, T0 registry, `license_reference`, schema, version manifest, registry sync equality | T1/T9 |
| CHK-02 | `mq audit`: T0 approvals, coverage contract, schema-valid rate, refusal/incomplete rate, false-quote rate, exact-match rate, first-hand flag, trace completeness | T9 |
| CHK-03 | `mq eval`: gold-set recall/precision, span/metadata accuracy, obsolete precision, judge κ | T0/T8 |
| CHK-04 | syrupy snapshots (prompts, schema, MD) with ID/date matchers and frozen clock | T8 |
| CHK-05 | Coverage contract rows per source per day (expected vs discovered vs quotes, gap reasons, freshness SLA) | T6/T8 (Dagster asset check) |
| CHK-06 | `mq logs`: correlated structured logs with `trace_id` | T1/T9 |
| CHK-07 | Failure-injection tests (network, paywall, low-quality extraction, LLM refusal/truncation) | T8 |
| CHK-08 | `scripts/docs_qa.py` (also `tests/test_docs_qa.py`): documentation consistency (links, IDs, thresholds, enums, paths) | CI/T10 |
| CHK-09 | VCR cassette replay suite (offline ingestion/extraction) | T2/T3/T8 |
| CHK-10 | `make check`: `docs_qa.py` + `ruff format --check .` + `ruff check .` + `mypy --strict src/` + `pytest` + `python -m build` + `mq --help` smoke | CI/T1/T10 |
| CHK-11 | `uv.lock` freshness and `uv sync --frozen --group dev` in CI/prod; weekly dependency audit | CI/T1/T10 |
| CHK-12 | Coverage **≥ 85%** on `src/media_quotes` (`pytest --cov`; floor in `[tool.coverage.report]`); unit tests never touch network/DB | T10/CI |
| CHK-13 | Secret/redaction test: no key material in logs, traces, audit rows or cassettes; `license_reference` is NOT redacted | T1/T10/CI |
| CHK-14 | Reserved for the Neo4j option: graph parity/provenance/rights check if adopted; see [09](09-neo4j-integration.md) §7 | not active — reserved |
| CHK-15 | Obsolescence review audit: evidence bundle + reviewer verdict on every flag; SLA met; no obsolete quote without `confirmed` | Unit policy layer implemented (`risk_controls.py`, `tests/test_risk_controls.py`); full audit T5/T6/T8 pending, see [10](10-risk-mitigation-plan.md) |
| CHK-16 | Backfill budget guard: dry-run within ±20%, cache hit ≥ 60%, no duplicate calls, hard cost cap | Unit policy layer implemented (`estimate_backfill`, `budget_gate`, tests); integration T2/T4/T9/T10 pending, see [10](10-risk-mitigation-plan.md) |
| CHK-17 | Storage/lock guard: retention job, detached partitions, longest txn < 60s, prune tombstones | Unit policy layer implemented (`RetentionPolicy`, tests); partitioning/job T1/T9/T10 pending, see [10](10-risk-mitigation-plan.md) |
| CHK-18 | Prompt-injection suite: adversarial article/feed text cannot alter system prompt, labels or verifier outcome | T4/T8 — planned (RSK-13) |
| CHK-19 | Rights/limits guard: no credential in logs/traces/cassettes (CHK-13/09 reused); renderer rejects >30-word quotes, wrong attribution and out-of-territory use | T6/T10 — planned (RSK-14/15) |

## 2. Output requirements → traceability

| ID | Requirement | Decision | Task(s) | Check(s) | Status |
|---|---|---|---|---|---|
| OUT-01 | Auditable database | DEC-03, DEC-05 | T1 | CHK-01, CHK-02 | solved |
| OUT-02 | Dashboard (MD files, logs, DB) | DEC-07 | T6 | CHK-05, CHK-06 | solved |
| OUT-03 | Per-source coverage view | — | T6 | CHK-05 | solved |
| OUT-04 | GitHub repository | — | T1, T9 | CHK-08 | solved |
| OUT-05 | One-command operation | DEC-04 | T9 | CHK-01, CHK-02, CHK-06 | solved |

## 3. Quality requirements → traceability

| ID | Requirement | Decision | Task(s) | Check(s) | Status |
|---|---|---|---|---|---|
| REQ-01 | No ingestion without T0-gate approval; `license_reference` logged | DEC-01 | T1, T2 | CHK-01, CHK-02 | solved |
| REQ-02 | All material articles from mandatory sources detected | DEC-01, DEC-03, DEC-14 | T2, T8 | CHK-03, CHK-05 | **accepted** (sources `blocked`; measured in full mode after licence) |
| REQ-03 | Tier 3 represented; quotes verified first-hand | DEC-01 | T3, T7 | CHK-02, CHK-03 | solved (once sources enabled) |
| REQ-04 | Source type/attribution stored; claims never treated as fact | DEC-01 | T3, T5 | CHK-02, CHK-04 | solved |
| REQ-05 | All material quotes detected | DEC-03 | T0, T4, T8 | CHK-03 | solved (measured) |
| REQ-06 | Quotes relevant, substantial, cleansed | DEC-03, DEC-05 | T3, T4 | CHK-03, CHK-02 | solved |
| REQ-07 | Nothing important left out | DEC-03 | T2, T8 | CHK-03, CHK-05 | solved (measured) |
| REQ-08 | Accurate extraction of quotes/metadata | DEC-05 | T3 | CHK-02, CHK-09 | solved |
| REQ-09 | Accurate MD load (verbatim, categorisation, no obsolete) | DEC-04 | T5, T6 | CHK-04, CHK-03 | solved |
| REQ-10 | Section interpretations accurate | DEC-05, DEC-06 | T4 | CHK-03 | solved |
| REQ-11 | Database auditable, LLM-integrable | DEC-05, DEC-09 | T1 | CHK-01, CHK-02 | solved |
| REQ-12 | Logs saved for auditability | DEC-07 | T1 | CHK-06, CHK-02 | solved |
| REQ-13 | Error handling | DEC-08 | T2, T3 | CHK-07, CHK-09 | solved |
| REQ-14 | Any size/content type | DEC-08 | T3 | CHK-07, CHK-09 | solved |

## 4. Decision → implementation map

| Decision | Task(s) | Check(s) | Status |
|---|---|---|---|
| DEC-01 mandatory sources + T0 gate | T0, T2, T7 | CHK-01, CHK-02, CHK-05 | solved (operationally blocked) |
| DEC-02 internal-only digest | T0, T6 | CHK-04 | solved |
| DEC-03 gold set + thresholds | T0, T8 | CHK-03 | solved |
| DEC-04 Dagster | T1, T8 | CHK-05 | solved |
| DEC-05 pinned LLM + cache | T4, T8 | CHK-02, CHK-03 | solved |
| DEC-06 eval harness (no OpenAI Evals) | T0, T8 | CHK-03 | solved |
| DEC-07 OTel + local OTLP | T1, T6 | CHK-06 | solved |
| DEC-08 Playwright/PDF fallback | T3 | CHK-07, CHK-09 | solved |
| DEC-09 pgvector/Neo4j + optional tooling not adopted | — | — | not adopted |
| DEC-10 licence-limit policy (30 words, attribution, territory) | T6 | CHK-02, CHK-04 | solved (licence pending) |
| DEC-11 sign-off owner | T2, T7 | CHK-01, CHK-02 | solved |
| DEC-12 gold-set reviewer (dual κ calibration) | T0, T8 | CHK-03 | solved |
| DEC-13 state-controlled outlets stay disabled | T7 | CHK-01 | solved |
| DEC-14 residual licence risk accepted | T0, T2 | CHK-02 | accepted |
| DEC-15 Alembic migrations | T1 | CHK-01, CHK-10 | solved |
| DEC-16 Python engineering standards (uv, ruff, mypy --strict, 85%) | T1, T10 | CHK-10, CHK-11, CHK-12, CHK-13 | solved |
| DEC-17 obsolescence evidence bundle + review SLA + drift guard | T5, T6, T8 | CHK-03, CHK-15 | solved (policy layer in T1; runtime check in T5/T6/T8) |
| DEC-18 backfill dry-run estimate + budget gate + cache hit target | T1, T2, T4, T9 | CHK-11, CHK-16 | solved (policy layer in T1; runtime check in T2/T4/T9) |
| DEC-19 monthly partitioning + bounded retention + restore drill | T1, T9, T10 | CHK-01, CHK-17 | solved (policy layer in T1; runtime check in T1/T9/T10) |

## 5. How to record a change

1. New decision or risk → add a `DEC-`/`RSK-` row in [04](04-risks-and-open-questions.md).
2. New/changed requirement → add or edit the `REQ-`/`OUT-` row in
   [01](01-requirements-analysis.md) §4/§5 **and** add its row here. Edit 01–03 only when the
   requirement/design itself changes — never to note a status.
3. Update the matching row(s) here (status, tasks, checks, brief anchor).
4. Add affected checks to the task that must run them (T0–T10 in [03](03-work-breakdown.md)).
5. Run `scripts/docs_qa.py` (CHK-08, ideally via `make check`) before merge.

## 6. Tracked code changes (found in scaffold review)

| ID | Change | Owner | Target task | Verify | Priority |
|---|---|---|---|---|---|
| ENUM-01 | `SourceIn.source_type` in `src/media_quotes/models.py` uses `Literal["publisher", "aggregator", "feed", "other"]`, conflicting with the canonical five-value enum in [01](01-requirements-analysis.md) §2 (completed in T1: unified in `src/media_quotes/enums.py` and `validation.py`; `data/sources.csv` and `data/quotes.schema.json` preserved) | Balgove Innovations | T1 | CHK-10, CHK-12 | Done (T1) |
| BOOT-01 | `storage.py` executes raw `schema.sql` by splitting on semicolons (completed in T1: DEC-15 replaced with Alembic migration `0001_initial.py`; dev bootstrap delegates to Alembic) | Balgove Innovations | T1 | CHK-01, CHK-10 | Done (T1) |
| TRACE-01 | `observability.py` uses a console exporter (completed in T1: DEC-07 added structlog redaction processor and OTLP exporter alongside console) | Balgove Innovations | T1 | CHK-13 | Done (T1) |

## 7. Accepted constraints and residual follow-up

No open questions remain. Two residual real-world conditions are **accepted**, with fixed
mitigations and explicit revisit triggers (DEC-14, [04](04-risks-and-open-questions.md)):

- **REQ-02** (mandatory-source coverage) remains measurable only in licensed/full mode;
  until then the blocked sources are explicit coverage gaps, not silent passes.
- The **licence decision** (signed or declined) is the only external event that changes
  DEC-01/DEC-10/DEC-14. When it lands, the operator updates `data/sources.csv` as part of
  the enablement task (T2/T7), records the limits in [07](07-sources.md) and DEC-10, and
  re-runs CHK-01/CHK-02/CHK-03.

## 8. Closed items (moved out of the active register in 04)

Solved decisions and risks are recorded here — not in [04](04-risks-and-open-questions.md) —
so IDs stay resolvable and status changes never require rewriting 01–03. This is an index,
not a fix history: one line per item, with where it was implemented.

| ID | Closed decision (one line) | Implemented in |
|---|---|---|
| DEC-01 | Reuters/BBC stay mandatory but `blocked` under the T0 gate; no substitution | [01](01-requirements-analysis.md) §2; [06](06-observations.md) §1; [07](07-sources.md) |
| DEC-02 | Digest internal-only; external publication needs a new rights sign-off | [01](01-requirements-analysis.md) §6; T6 |
| DEC-03 | Own stratified gold set, 60/20/20 split, κ ≥ 0.70 | [01](01-requirements-analysis.md) §6 #15, §7; T0/T8 |
| DEC-04 | Dagster with daily partitions, asset checks, `code_version` | [02](02-stack-and-architecture.md) §1; [06](06-observations.md) §2; T1 |
| DEC-05 | Pinned OpenAI structured outputs + decision cache; provider boundary swappable | [02](02-stack-and-architecture.md) §1; T4 |
| DEC-06 | In-house Pydantic Evals-style harness; never OpenAI Evals (went read-only 2026-10-31, shuts down 2026-11-30) | [01](01-requirements-analysis.md) §7; T0/T8 |
| DEC-07 | One OTel trace per article, OTLP local-only, JSON logs | [02](02-stack-and-architecture.md) §4; T1/T6 |
| DEC-08 | Playwright/PDF rendering fallback after quality gate | T3 |
| DEC-10 | Licence limits: ≤ 30 words, attribution format, internal worldwide | [07](07-sources.md) intro; T6 renderer |
| DEC-11 | Balgove Innovations owns sign-off; counsel reviews; `license_reference` recorded | T2/T7; [07](07-sources.md) enabling checklist |
| DEC-12 | Dual κ calibration on ~20% gold-set slice: blinded ≥7-day delayed re-label (or second annotator) for label stability, plus judge-vs-human κ ≥ 0.70 | [01](01-requirements-analysis.md) §6 #15, §7; T0/T8 |
| DEC-13 | State-controlled outlets stay disabled; no perspective opt-in | [07](07-sources.md) Tier 4 |
| DEC-15 | Alembic migrations replace raw SQL bootstrap | [02](02-stack-and-architecture.md) §1/§3/§7; T1 |
| DEC-16 | Python engineering baseline (uv, ruff, mypy --strict, 85% coverage, redaction) | [02](02-stack-and-architecture.md) §7; T1/T10 |
| DEC-17 | Obsolescence evidence bundle + human review queue + drift guard (RSK-07) | [10-risk-mitigation-plan.md](10-risk-mitigation-plan.md) §1; T5/T6/T8 |
| DEC-18 | Backfill cost guard: dry-run estimate, `--max-cost`, cache hit target, batch routing (RSK-08) | [10-risk-mitigation-plan.md](10-risk-mitigation-plan.md) §2; T1/T2/T4/T9 |
| DEC-19 | Monthly partitioning, bounded retention, short transactions, restore drill (RSK-11) | [10-risk-mitigation-plan.md](10-risk-mitigation-plan.md) §3; T1/T9/T10 |

| ID | Closed risk | Mitigation now standard |
|---|---|---|
| RSK-03 | \"All material\" unmeasurable | Gold set + thresholds; CHK-03/CHK-05 |
| RSK-04 | Gold-set calibration drift | Dual κ calibration (human stability + judge-vs-human); CHK-03 |
| RSK-05 | Structured outputs are not truth | Deterministic verifier; CHK-02/CHK-12 |
| RSK-06 | `temperature=0` mistaken for determinism | Content-addressed cache; CHK-01/CHK-02 |
| RSK-09 | Low-quality/JS-only extraction | Precision/recall + quality gate + rendering fallback; CHK-07 |
| RSK-10 | Network/paywall gaps → silence | Retries, circuit breakers, coverage rows; CHK-05/CHK-07 |
| RSK-12 | One-command run hides partial failure | Exit codes 0–3; CHK-02 |
| RSK-07 | Obsolescence judgements error-prone | Policy layer in `risk_controls.py`; CHK-15 + held-out precision ≥ 0.90 ([10](10-risk-mitigation-plan.md) §1) — **planned** |
| RSK-08 | LLM cost/latency on backfills | Policy layer in `risk_controls.py`; CHK-16 + budget gate ([10](10-risk-mitigation-plan.md) §2) — **planned** |
| RSK-11 | Append-only growth / row locks | Policy layer in `risk_controls.py`; CHK-17 + partitioning/retention ([10](10-risk-mitigation-plan.md) §3) — **planned** |

Neo4j planning is tracked in [09-neo4j-integration.md](09-neo4j-integration.md); CHK-14 there
is a **proposed future check**, not an active CHK row. The three new CHK rows (15–17) are
planned until the mitigation plan's exit criteria pass; they do not replace active checks.
