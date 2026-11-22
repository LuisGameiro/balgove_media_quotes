# 12 — Implementation start plan

This is the bridge from the planning docs to code. The scaffold already contains: strict
models, registry/validation, Alembic revisions `0001..0006`, 19 CLI commands (18 offline plus
`mq fetch`), OTel/structlog secret redaction (`observability.py`), the `risk_controls/` policy
package and a green 629-test suite at ~86.7% coverage.
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
| B1 | Monthly partitioning of `audit_events` and `llm_decisions` (RSK-11 plan §3) — planned, not applied | CHK-17 |
| B2 | Done: `llm_cache.DbDecisionCache` wires the pinned LLM boundary to `decision_cache` when `PERSIST_TO_POSTGRES=true` | CHK-16 |
| B3 | Wire `configure_observability` (structlog redaction + OTLP span exporter in `observability.py`) into live Postgres/Dagster pipeline runs and local OTel collector | CHK-13 |
| B4 | `uv.lock` + `make check` already present; ensure CI runs it on every PR | CHK-10/11 |

## 3. Phase C — T2/T3 ingestion vertical slice (2–3 sessions)

| Step | Work | Exit check |
|---|---|---|
| C1 | `fetch` adapter using async httpx with `Timeout`/`Limits`/token bucket; VCR cassette replay for blocked Track B fixtures + live fetch for approved Track A sources; dead-letter + coverage rows persisted in Postgres | CHK-05/09 |
| C2 | Trafilatura extraction with §4.1 params, DOM offsets; quality gate → Playwright fallback | CHK-07 |
| C3 | Two-stage verifier persisted to Postgres: Stage 1 pre-LLM candidate filter (drops zero-quote boilerplate before LLM spend) + Stage 2 post-LLM exact-span, speaker, metadata and first-hand evidence gate; `unverified` never published | CHK-02 |
| C4 | Cut over `mq run` and Dagster daily partitioned assets (`DEC-04`) from file-only stand-ins to Postgres-persisted runs producing a degraded digest that explicitly lists blocked Tier 1 sources (Reuters/BBC) as coverage gaps | CHK-01/02 |

**C1/C2/C3/C4 progress:** `fetch.py` (rights-gated async httpx), `extraction.py` (strict
`ExtractionParams`, fail-closed quality gate, trafilatura Document/dict wrapper, injectable
fallback hook, `find_span()` for exact offsets), `verification_stages.py` (Stage 1 pre-LLM
junk/word-limit/duplicate filter + Stage 2 existing `verify_quote`, publishable-only output),
repository run/audit/quote persistence and `orchestration.py` (Dagster daily partitions, code
version) are implemented with offline tests. Phase 5 added the public `pipeline.py` service,
Dagster resources/config (`settings`, `database`, `DailyPipelineConfig`), three asset checks,
UTC schedule metadata, in-process materialization tests and CLI/Dagster service parity. `mq run` persists run, article, quote and audit
rows when `PERSIST_TO_POSTGRES=true` (Settings has no env prefix); otherwise it stays
file-based. No source is
activated; every run emits explicit mandatory-source coverage gaps. Persisted quote rows are
never written without exact offsets from the verifier.

## 4. Phase D — T4/T5 reasoning and obsolescence (2–3 sessions)

**D progress:** `llm_live.py` (pinned model, cache-first, strict JSON schema, refusal/incomplete,
injected client), `llm_openrouter.py` (env-selected `LLM_PROVIDER=openrouter`, OpenAI-compatible
Chat Completions client, pinned OpenRouter model id, JSON inference validation), `batch.py`
(deterministic `custom_id` + Batch JSONL), `curation.py` (section allow-list, cross-outlet dedup,
`[Under Review]` badges) and `rights_guard.py` (DEC-10 limits before rendering) are implemented
with offline tests. There is no CLI flag for provider selection.

| Step | Work | Exit check |
|---|---|---|
| PD1 | Live OpenAI boundary with pinned snapshot, strict structured output (preserving native `quote_text` + generating English `quote_text_en` translation), provider response id, refusal/incomplete logging, and Postgres `decision_cache` lookup before call | CHK-02 |
| PD2 | Batch API path for backfills using `custom_id` (still a stub); done: `mq backfill` records budget estimate and gate result in `pipeline_runs` when persistence is on | CHK-16 |
| PD3 | Section assignment from the 6-slug allow-list in `data/sections.csv`, cross-outlet quote deduplication (14-day window), and obsolescence rule with evidence bundle + `risk_controls.ReviewQueue` (24h `provisional_hold` `[Under Review]` badge) | CHK-15 |
| PD4 | Held-out eval: obsolete precision ≥ 0.90 and drift guard (`obsoletion_allowed`) persisted in Postgres | CHK-03 |

## 5. Phase E — T6/T9 presentation and operator UX (1–2 sessions)

**E progress:** dashboard gains per-source coverage and obsolescence review views read from
the latest audit report; audit reports now carry a `review_queue` key. Existing CLI commands
remain offline-safe; no live provider/Postgres backend is yet required.

| Step | Work | Exit check |
|---|---|---|
| E1 | Python-built digest (`digest.py`) from verified records with verbatim quotes (`quote_text` + `quote_text_en`), DEC-10 limits, `[Under Review]` provisional-hold badges and snapshots | CHK-04 |
| E2 | Done: dashboard shows latest digests, per-source coverage, review queue, logs and read-only Postgres row counts | CHK-05/06 |
| E3 | Cut over all 19 CLI commands to live Postgres/Dagster backends with exit codes 0–3 | CHK-01/02/06 |

## 6. Phase F — T8 quality gate & closure (continuous, final)

**F progress:** prompt-injection guard + strict LLM boundary (CHK-18), rights/limits guard
(CHK-19) and offline restore-drill/retention-readiness helpers (CHK-17 groundwork) landed.
Operator commands (`mq review`, `mq retention`, `mq restore-drill`) and Postgres-backed
`mq eval` results close the offline CLI surface. Live load tests, real backfill budget metrics
and production verification evidence remain open; RSK-07/08/11 stay accepted until those
gates pass.

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

## 8. Operator next steps (current state)

All code phases A–F are implemented and green (`check`). What remains is operational.
PowerShell is the primary shell; `check` runs the full gate (see root README).

### 8.1 Offline sanity (no secrets, no network)

```powershell
check
python -m media_quotes run --date 2026-11-19
python -m media_quotes audit --date 2026-11-19
python -m media_quotes eval --gold-set synthetic-v1
python -m media_quotes review --action list
python -m media_quotes retention --dry-run
python -m media_quotes restore-drill
```
Reuters/BBC coverage gaps are expected while Track B stays blocked.

### 8.2 Secrets and configuration (`.env`, gitignored)

- Rotate any API key that was ever pasted in chat; put only the new key in `.env`.
- Keep one `LLM_MODEL_SNAPSHOT` line with an explicit model id (no duplicate `unset`, no `latest`).
- `LLM_PROVIDER=local` keeps inference offline/deterministic; set `openrouter` only for live summaries.
- `PERSIST_TO_POSTGRES=true` enables DB writes; remove it to stay file-based.
- Required for `openrouter`: provider, pinned snapshot, `OPENROUTER_API_KEY`; all other OpenRouter vars have defaults.

### 8.3 Local Postgres

```powershell
docker compose up -d --wait
python -m media_quotes.bootstrap    # Alembic 0001..0006 + data/sources/*.json sync
python -m media_quotes doctor
python -m media_quotes run --date 2026-11-19   # with PERSIST_TO_POSTGRES=true
python -m media_quotes sources
```

### 8.4 Track A rights gate (stakeholder action)

For `guardian_api`, `gdelt`, `suspilne`, `kyivindependent`: put the written consent/licence ID
in `data/sources.csv` → `license_reference`, set `rights_status=api_permitted` (or
`license_held`), approved `access_mode`, `enabled=true`, update `docs/07` and register rows.
GDELT remains **discovery only** — no publisher-text republication.
Keep Reuters/BBC (`Track B`) `enabled=false` until the commercial licence is signed.
Re-run bootstrap + doctor after the edit.

### 8.5 Dagster and live inference

```powershell
dagster dev -m media_quotes.orchestration   # daily partitions from 2026-10-01
```
Materialise 2026-11-19. Switch `.env` to `LLM_PROVIDER=openrouter` only with a rotated key
and pinned model; this changes only section inferences, never verification or the T0 gate.

### 8.6 Close the accepted risks with production evidence

- RSK-07: held-out obsolete precision >= 0.90; review SLA met.
- RSK-08: three real backfills within +/-20% of estimate; `--max-cost` breach exits 3.
- RSK-11: load test with transactions < 60s; retention job and restore drill pass.
Then change RSK-07/08/11 from `accepted` to `solved` in [04](04-risks-and-open-questions.md)
and [08](08-traceability.md), keeping the ID tokens so `docs_qa.py` stays green.
