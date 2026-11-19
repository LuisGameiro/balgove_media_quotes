# 10 — Risk mitigation plan (RSK-07, RSK-08, RSK-11)

**Status:** mitigations **partially applied** in `src/media_quotes/risk_controls.py` with
27 unit tests in `tests/test_risk_controls.py` (CHK-15/16/17 policy layer, all passing).
Applied: evidence bundle + triage, review-queue state machine (add/decide/overdue/hideable),
drift guard, cost estimate + budget gate + canonical cache-key builder, retention policy +
partition/retention planner. Design decisions (DEC-17/18/19) and policy closures are indexed
in [08](08-traceability.md) §8; runtime integration exit criteria (fixture runs, three real
backfills, load test, restore drill) are enforced as T1–T10 land. RSK-01/RSK-02 are deliberately
**not** here: they depend on a signed licence, not engineering.

**Notation:** \"T0 gate\" = the rights gate in brief §1; task `T0` = the first task in
[03](03-work-breakdown.md). Owner of all mitigations: **Balgove Innovations**.

| Risk | Goal | Where the plan lands | Verify |
|---|---|---|---|
| RSK-07 — obsolescence errors | Every supersession is evidence-backed and reviewed before it hides a quote | T5 (rule), T6 (queue), T8 (held-out eval) | CHK-03, new CHK-15 |
| RSK-08 — LLM cost/latency | Backfills are estimated, capped, cached and replayable; no surprise spend | T1/T2 (cache + estimate), T4 (batch routing), T9 (reporting) | CHK-03, CHK-11, new CHK-16 |
| RSK-11 — append-only growth | Bounded storage and no long row locks, without losing audit history | T1 (partitioning), T10 (asset checks), T9 (retention command) | CHK-01, new CHK-17 |

## 1. RSK-07 — obsolescence judgements error-prone

**Root cause:** the LLM can flag a quote as superseded on weak or non-first-hand evidence; the
review queue exists in outline but has no evidence bundle, SLA enforcement, or failure policy.

### Mitigation design

1. **Evidence bundle required for every flag.** Store with each obsolescence flag:
   - older quote `quote_id` + `content_hash` + DOM offsets (proof of verbatim text);
   - newer source article `article_id`, `rights_status`, `original_reporting_flag`,
     `content_hash` and the exact superseding span;
   - LLM `decision_id`, confidence, rationale, model snapshot and prompt/schema versions;
   - reviewer verdict, timestamp, reviewer id and reason (free text + reason code).
2. **Confidence-based triage** (threshold tuned on the gold-set **dev** split only):
   - `p ≥ 0.80` → flag as `pending` review, quote **provisional-held** (queued for priority review; shown only with a `[pending supersession check]` note if the SLA window expires, never permanently marked `is_obsolete=true` in DB until verdict);
   - `0.50 ≤ p < 0.80` → flag as `needs_evidence`, gather a second first-hand source or keep active;
   - `p < 0.50` → keep the quote active, log the non-decision for monitoring.
   No quote becomes permanently `is_obsolete=true` without a human `confirmed` verdict — fail-closed.
3. **Review queue in T6** with states `pending → confirmed | rejected | escalated`;
   `claim_only` material always shown as perspective. SLA: **1 working day (24h weekday / 48h weekend window)**; on breach the
   run audit records an overdue-review gap (exit `2`).
4. **Human override:** a reviewer may reject an LLM flag; reject reason and LLM rationale are
   both retained for calibration. Escalation goes to Balgove Innovations.
5. **Drift guard:** held-out `obsolete_precision ≥ 0.90` ([01](01-requirements-analysis.md) §7).
   If two consecutive `mq eval` runs fall below it, disable LLM obsoletion and require
   human-only review until recalibrated; record the change in [08](08-traceability.md).

**Code artifact:** `src/media_quotes/risk_controls.py` — `ObsolescenceEvidence`,
`ObsolescenceFlag.may_hide_quote()`, `triage_obsolescence()`, `review_overdue()`,
`ReviewQueue` (state machine, SLA list, hideable list), `obsoletion_allowed()`;
`tests/test_risk_controls.py` covers the branches (unit layer done).

### Tasks / checks / exit criteria

- Tasks: T5 (rule + evidence bundle), T6 (queue UI + SLA), T8 (eval + drift guard).
- Checks: CHK-03 (precision + calibration), **CHK-15** (every flagged quote has a complete
  evidence bundle and a reviewer verdict; no `confirmed` obsolete quote without first-hand
  rights-cleared evidence; SLA breaches reported).
- Done when: 100% of flagged rows in a fixture run have evidence bundle + verdict; held-out
  precision ≥ 0.90; zero obsolete quotes in a digest without a `confirmed` verdict.

## 2. RSK-08 — LLM cost/latency on backfills

**Root cause:** ~50-day backfill over many sources × several LLM calls per article, with no
pre-flight estimate, hard budget, or cache-hit monitoring.

### Mitigation design

1. **Pre-flight estimate and budget gate.** `mq backfill --dry-run --from … --to …` prints
   estimated articles, LLM calls, token range, cost range and wall-clock range, using the
   T2-measured articles/day/source and the pinned-model price. Running for real requires
   `--max-cost <amount>`; exceeding the cap exits `3` unless `--force` is given with an audit
   note. Budget: 80% of cap → warning; 100% → block.
2. **Cache first, LLM second.** The content-addressed decision cache (key = source hash +
   extractor version + prompt version + schema hash + model snapshot + decoding params) is
   checked before every call; idempotent Postgres upserts prevent duplicate decisions.
   Target **≥ 60% hit rate** on a repeat backfill; below that, the audit report names the
   version component that changed.
3. **Batch path for backfills.** Use the provider Batch API with `custom_id`, one batch per
   daily partition, store raw provider response ids and output JSON in `llm_decisions`;
   re-running a partition reuses existing results instead of re-billing.
4. **Cheap gates before LLM, strict verifier after.** Stage 1 deterministic DOM extraction, pre-LLM span checks, junk filters and
   reject caps are applied **before** any LLM call so only genuine candidates reach a model; Stage 2 exact-span verification gates final publication.
   Any additional lower-cost model for first-pass relevance must be pinned in the manifest
   and validated against the gold set before use (DEC-05 update recorded in [08](08-traceability.md)).
5. **Bounded concurrency and telemetry.** Async httpx with semaphore + provider rate limits;
   p95 per-article latency target set at T2; `mq audit` JSON/MD includes calls, cache hits,
   tokens, cost, latency and batch ids per run.

**Code artifact:** `src/media_quotes/risk_controls.py` — `estimate_backfill()`,
`budget_gate()`, `BudgetExceededError`, `build_cache_key()` (SHA-256 over canonical JSON of
the six versioned components); `tests/test_risk_controls.py` covers estimate, cache-hit
reduction, the 80%/100% gate and key stability/sensitivity (unit layer done).

### Tasks / checks / exit criteria

- Tasks: T1 (cache schema), T2 (measurement + dry-run estimate), T4 (batch routing),
  T9 (budget flags + audit report), T10 (checks).
- Checks: **CHK-16** (dry-run estimate within ±20% of actual for the first three real
  backfills; zero duplicate provider calls; cache hit rate ≥ 60% on repeat; hard cost cap
  never exceeded without `--force` audit note), CHK-11 (lockfile), CHK-03 (gold-set metrics
  unchanged by routing changes).
- Done when: three backfills run within ±20% of estimate, no duplicate billed calls, and a
  deliberate budget-breach test exits `3` with an audit row.

## 3. RSK-11 — append-only growth / row locks

**Root cause:** append-only audit/decision tables grow without bound, `jsonb` updates lock
rows, and no retention/partitioning job exists.

### Mitigation design

1. **Monthly declarative partitioning** by `created_at` for `audit_events`, `llm_decisions`,
   `verifier_results`, `source_checks` and `runs` (Alembic migration, DEC-15). Current month
   is hot; older partitions are read-only. Archival = **detach to cold storage, never delete**
   unless counsel sets a legal retention limit — audit history is immutable evidence.
2. **Raw bytes, logs and exports.** Raw blobs stored externally with pointer + `content_hash`
   in Postgres; lifecycle prunes blobs older than **180 days** after verifying the audit row
   and derived exports still reference only hashes (tombstone record retained). JSON logs
   retained **90 days**; derived JSONL/context packs regenerable via `mq export`.
3. **JSONB discipline.** No in-place `UPDATE` on audit rows — corrections are new versioned
   rows. GIN indexes only on fields actually queried (e.g. `rights` on the active source
   subset); monitor bloat and autovacuum statistics.
4. **Short transactions, no long locks.** Write per article/daily partition with short
   commits; no long-running read transaction inside a write; connection pool limits from
   config; alert if any transaction exceeds **60 seconds**.
5. **Monitoring and recovery.** Dagster asset check reports table/partition size trend,
   index bloat, longest transaction age and retention-job status. Quarterly restore drill:
   rebuild `quotes.jsonl` from a restored partition set + blob store and compare hashes.

**Code artifact:** `src/media_quotes/risk_controls.py` — `RetentionPolicy`
(`prune_cutoffs`, `allow_prune`, `lock_ok`), `partition_name()`, `RetentionPlan` and
`retention_plan()` (monthly partitions to detach/keep); `tests/test_risk_controls.py`
covers cutoffs, tombstones, audit immutability, the 60s lock bound and the planner
(unit layer done).

### Tasks / checks / exit criteria

- Tasks: T1 (partitioned schema + Alembic migration), T9 (`mq` retention/report commands),
  T10 (asset checks), T8 (restore drill in CI/fixture).
- Checks: **CHK-17** (retention job succeeded; detached partitions present in cold storage;
  longest transaction < 60s under load test; index bloat below the T1-agreed threshold;
  blob prune left a tombstone hash for each removed object), CHK-01 (schema/version manifest).
- Done when: a fixture load test shows bounded storage growth, no lock over 60s, a restore
  drill reproduces the JSONL hash, and a prune job leaves audit rows untouched.

## 4. Sequencing and closure

| Order | Work | Risks touched | Closure evidence |
|---|---|---|---|
| 1 | T1 partitioning + cache schema | RSK-08, RSK-11 | Alembic head green; CHK-01 |
| 2 | T5 evidence bundle + T6 queue | RSK-07 | CHK-15 on fixtures |
| 3 | T2 dry-run estimate + T4 batch routing | RSK-08 | CHK-16 on three backfills |
| 4 | T9 budget/retention commands + T10 checks | RSK-08, RSK-11 | CHK-16/CHK-17 in CI |
| 5 | T8 held-out eval + restore drill | all three | CHK-03/CHK-15/CHK-17 reports |

DEC-17/18/19 and RSK-07/08/11 are indexed in [08](08-traceability.md) §8 with policy-layer unit
tests passing in T1, leaving only the licence and operational residuals (RSK-01/02/13/14/15/16) in
the active register in [04](04-risks-and-open-questions.md). Full runtime closure is gated on
CHK-15/16/17 in T5–T10.
