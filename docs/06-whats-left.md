# 06 — What is left

Unfinished work, grouped by the original requirement it limits, plus completed highlights.

## 1. FR-2.2 — automatic daily source checks

- [ ] Enable the daily and six-hour Tier-1 Dagster schedules in the target environment.
- [x] Wire live discovery/fetch into the scheduled pipeline: the daily and Tier-1 schedules now
      request `source_mode=live` when enabled (schedules remain stopped by default).
- [ ] Prove Reuters and BBC are checked on every required day, with failures recorded as coverage gaps.
- [ ] Supply a working approved Reuters licensed endpoint and clear its HTTP 401 coverage gap.
- [ ] Clear AP's HTTP 401 and verify live quote extraction.
- [ ] Provide a Guardian API key and a licensed NYT full-text route; keep GDELT discovery-only.
- [ ] Fix RT timeout and Xinhua's stale HTTP-only feed; improve TASS/RIA quote yield.
- [ ] Provide licensed full-text routes for Bloomberg, FT and Economist, or keep them explicitly partial.
- [ ] Replace synthetic completeness evidence with measured real-source article and quote recall.

## 2. FR-2.3 — live collection and backfill

- [x] Implement bounded historical archive discovery for 2026-10-01 through the current date
      (RSS/sitemap/sitemap-index recursion, date filtering, resumable atomic manifests,
      `--source-id` filtering, 365-day cap, paid-mode fail-closed).
- [ ] Execute and audit the initial live backfill; the command supports fixtures, blobs and `--live`,
      but no full 2026-10-01 production run has been completed.
- [ ] Confirm backfill estimates stay within ±20% of actual spend across three real runs.
- [ ] Confirm zero duplicate billed calls and at least 60% cache hits on a repeat backfill.
- [x] Complete automated source admission probing for robots/terms, archive depth and first-hand reporting.
- [x] Provide an operator-triggered 2026-10-01 backfill after rights approval via
      `mq sources --action backfill --source-id ... --max-cost ...`.
- [ ] Obtain human first-hand sign-off for each newly scaffolded source before enabling it; new
      sources start with `original_reporting_flag=unknown`.

## 3. FR-2.4 — obsolescence and digest fidelity

- [x] Connect `propose_obsolescence` to the daily pipeline with same-speaker candidate gating,
      queue merging and atomic persistence.
- [ ] Enable the review SLA sensor and prove every flag meets the 24-hour review SLA.
- [ ] Obtain held-out human obsolete labels and confirm obsolete precision at least 0.90.
- [ ] Preserve quote whitespace and formatting instead of collapsing it in the Markdown renderer.
- [ ] Reconcile the 100% exact-span target with the current 0.95 automated evaluation threshold.
- [ ] Collect held-out human labels and calibrate judge agreement before cutting evaluation over
      to production thresholds.

## 4. OUT-3 — dashboard and repository

- [x] Add read-only quote/article metadata browsing to the dashboard Quotes tab.
- [x] Verify the GitHub repository is published and accessible at the configured remote.
- [ ] Deploy the dashboard with its password secret and confirm latest MD files, logs and coverage render.

## 5. Q-4 — quality, scale and content types

- [x] Add PDF/document extraction for think-tank reports.
- [x] Fetch policy caps responses at 5 MB and checks `Content-Length` before streaming.
- [ ] Measure cleaning, relevance and metadata accuracy against real mandatory-source articles.
- [ ] Run a live load test with no transaction over 60 seconds.
- [ ] Check cold-partition detachment, tombstone hashes and a quarterly restore drill.
- [x] Agree a numeric index-bloat threshold and monitor it in the Dagster asset check.
- [x] Decide licence territory is metadata-only (decision log); it is not enforced at render time.

## 6. Production operations

- [x] Run migrations `0007_widen_version`, `0007_audit_events_monthly_partition` and `0008_enable_tier4` with backup and rollback verification.
- [ ] Run migrations `0009_source_availability` and `0010_article_provenance` with backup and rollback verification.
- [ ] Rehearse real Postgres migrations and downgrades with backup/rollback verification; current
      checks are offline and do not prove a production rehearsal.
- [ ] Provision the production OTLP collector and enable `OTEL_ENABLED=true`.
- [ ] Schedule periodic `mq blob-prune --execute` runs backed by operator-approved tombstones.
- [x] Persist the held-out precision and drift guard, then cut `mq eval --full` over to production thresholds.
- [ ] Update stale source comments that still cite deleted documentation or blocked-source states.

## 7. Conditional work

- [x] Rendered JavaScript fallback exists behind `EXTRACTION_FALLBACK=rendered` and
      `extractor_hints.render_js=true`; it is disabled by default and rights-gated.
- [x] Optional Neo4j projection and bounded GraphRAG context packs exist; Postgres remains the
      system of record.

## 8. Review remediations

- [x] Scope partial artifacts: canonical daily files are reserved for a complete `live_feed` run;
      tier/source-filtered, archive, blob, `live_bing` and `manual_sample` runs write under
      `var/output/runs/<scope>/` and create no canonical Postgres digest manifest.
- [x] Fail closed on missing mandatory-source coverage in full live/archive runs, treating missing
      coverage as zero quotes and filters that exclude mandatory sources as scoped partial runs.
- [x] Add `--provenance` to `audit`, `digest`, `export` and `restore-drill`, and label reconstructed
      audits `reconstructed=true`.
- [x] Reject unknown backfill source IDs and spans over 365 days; let explicit IDs select Tier 4,
      exclude Tier 4 when no IDs are given, and share one resolved scope between estimate and
      execution.
- [x] Reject redirects unless safe following is explicitly enabled, fail closed on admission
      robots/feed errors, DNS-check archive discovery, and disclose malformed feed XML and
      per-article failures without aborting other articles.
- [x] Fail closed on PDFs missing title, URL, byline or date without inventing metadata, and emit a
      fixed unavailable marker plus audit event on digest inference failure while keeping quotes,
      logs and audit.
- [x] Default legacy approved `original_reporting_flag` to `first-hand`, scaffold new sources as
      `unknown` until manual sign-off, and honour the flag in quote audit and evaluation.
- [x] Apply rights word caps to original text rather than translations; require `--max-cost` or a
      narrowed scope for overruns (no `--force`).
- [x] Ignore legacy provenance-less root artifacts in the dashboard, enforce an 8-hour session TTL
      and a server-wide 5-attempt/15-minute counter-only lockout, cache DB counts for 30 seconds,
      and remove dead graph-insight helpers.
- [x] Base retention plans on observed Postgres audit months and fabricate no detach plan offline
      or in dry-run mode.
- [x] Prefilter live quote-audit blobs by sidecar date before fetching bodies, and build the topic
      gate and judge once per collection.

## Completed

- [x] 2026-11-24 real Tier-1 canary: `mq run --live` collected **two BBC `live_feed` quotes** from
      official-feed discovery, with no fixture fallback. Reuters returned HTTP 401 and was
      disclosed as a coverage gap. This is point-in-time evidence that live collection works, not
      proof of completeness or of any production acceptance target.
