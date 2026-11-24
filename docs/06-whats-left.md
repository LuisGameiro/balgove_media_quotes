# 06 — What is left

Only the steps still to do.

## 1. Production evidence

- [ ] Run three real backfills and confirm each estimate is within ±20% of actual spend.
- [ ] Confirm zero duplicate billed calls and at least 60% cache hits on a repeat backfill.
- [ ] Obtain held-out human obsolete labels and confirm obsolete precision ≥ 0.90.
- [ ] Confirm the review SLA is met on every flag.
- [ ] Run a live load test with no transaction over 60 seconds.
- [ ] Check cold-partition detachment, tombstone hashes and a quarterly restore drill.
- [ ] Agree a numeric index-bloat threshold and monitor it in the Dagster asset check.

## 2. System wiring and deployment

- [ ] Run migration `0007_audit_events_monthly_partition` on production during a scheduled
      maintenance window with backup and rollback verification.
- [ ] Configure the production OTLP collector endpoint (`OTEL_EXPORTER_OTLP_ENDPOINT`) and enable
      `OTEL_ENABLED=true` once the collector is provisioned.
- [ ] Schedule periodic `mq blob-prune --execute` runs backed by operator-approved tombstones.

## 3. Language, translation and review

- [ ] Finish the live pinned boundary so structured output preserves the native quote text and
      emits the English translation.
- [ ] Persist the held-out precision and drift guard, then cut over `mq eval --full` to full
      production thresholds.

## 4. Operator UX and integration

- [ ] Add automated admission probing (robots.txt, archive depth, first-hand check) to
      `mq sources --action add` for one-step source onboarding.
- [ ] Support PDF / document extraction for think-tank reports in addition to static HTML.

## 5. Conditional work

- [x] Rendered JavaScript fallback exists behind `EXTRACTION_FALLBACK=rendered` and
      `extractor_hints.render_js=true`; disabled by default and gated by the source rights check.
      Enable only if a rights-cleared JS-heavy source fails the static fallback.
- [x] Implement an optional Neo4j derived graph projection if multi-hop speaker/supersession
      GraphRAG queries are adopted (Postgres remains the system of record).
