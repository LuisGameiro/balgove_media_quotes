# 06 — What is left

Open work grouped by the original requirement it limits. Items already verified are recorded in
the dated Completed section at the bottom.

## 1. FR-2.2 — automatic daily source checks

- [ ] Enable the daily and six-hour Tier-1 Dagster schedules in the target environment.
- [x] Scheduled live discovery/fetch is wired: both Dagster schedules pass `source_mode=live`.
- [ ] Prove Reuters and BBC are checked on every required day, with failures recorded as coverage gaps.
- [ ] Supply a working approved Reuters licensed endpoint or authorised article samples and clear its HTTP 401 gap.
- [ ] Provide licensed full-text routes for Bloomberg, FT, SCMP, Economist and NYT, or keep them explicitly partial/blocked.
- [ ] Resolve RT's operator-reported sanctions restriction and network timeout.
- [ ] Improve quote yield for Suspilne or retain `accessible_no_quotes`.
- [ ] Resolve Anadolu's transfer-encoding/feed failure or retain an explicit coverage gap.
- [ ] Replace synthetic completeness evidence with measured real-source article and quote recall.

## 2. FR-2.3 — live collection and backfill

- [x] Bounded archive discovery for 2026-10-01 onward via `mq backfill --execute --fetch-archive`
      (RSS and sitemap indexes, capped by `--max-archive-articles`).
- [x] Execute the initial live backfill against real published sources.
- [ ] Audit the executed initial backfill: coverage, quoted counts and estimate accuracy.
- [ ] Confirm backfill estimates stay within ±20% of actual spend across three real runs.
- [ ] Confirm zero duplicate billed calls and at least 60% cache hits on a repeat backfill.
- [ ] Make source onboarding optionally trigger the 2026-10-01 backfill after rights approval.

## 3. FR-2.4 — obsolescence and digest fidelity

- [x] Wire `propose_obsolescence` into `run_daily_pipeline` and merge proposals into the review queue.
- [ ] Enable the review SLA sensor and prove every flag meets the 24-hour review SLA.
- [ ] Obtain held-out human obsolete labels, run a live judge and confirm obsolete precision at least 0.90.
- [ ] Preserve quote whitespace and formatting instead of collapsing it in the Markdown renderer.
- [ ] Reconcile the 100% exact-span target with the current 0.95 automated evaluation threshold.

## 4. OUT-3 — dashboard and repository

- [ ] Add read-only quote/article browsing or a governed database export to the dashboard.
- [ ] Deploy the dashboard with its password secret and confirm latest MD files, logs and coverage render.

## 5. Q-4 — quality, scale and content types

- [ ] Measure cleaning, relevance and metadata accuracy against real mandatory-source articles.
- [ ] Run a live load test with no transaction over 60 seconds.
- [ ] Check cold-partition detachment, tombstone hashes and a quarterly restore drill.
- [ ] Enforce licence territory in the render path or remove the unsupported enforcement claim.

## 6. Production operations

- [ ] Provision the production OTLP collector and enable `OTEL_ENABLED=true`.
- [ ] Schedule periodic `mq blob-prune --execute` runs backed by operator-approved tombstones.
- [ ] Cut `mq eval --full` over to production thresholds once held-out precision and drift-guard
      evidence exists.

## Completed (verified 2026-11-24)

- [x] Shared `collection.py` service for feed, URL and Bing acquisition with structured errors.
- [x] `tier_block` enforced before network access in CLI, diagnostics and proof runs.
- [x] Bing News URL discovery fallback and quote-aware multi-article sampling.
- [x] `tier_block`, `blocker_reason` and `availability_status` stored in JSON, CSV and Postgres.
- [x] Fixture-only proof excluded from collected-data and live-quote counts.
- [x] Approved `eurointegration.com.ua` redirects for Ukrainska Pravda with original/canonical URLs retained.
- [x] The authoritative 2026-11-24 proof report covers 27 sources: 15 collected data and verified
      76 live quotes, one was `accessible_no_quotes`, 11 were blocked, none were fixture-only and
      Anadolu is absent after its live-route failure.
- [x] AP, Le Monde, Indian Express, TASS, Ukrainska Pravda and Xinhua verified through live original-page collection.
- [x] GDELT and Guardian API marked `tier_block=true` for quote acquisition because they are discovery-only.
- [x] Kyodo and RIA marked `tier_block=true` because sampled articles did not yield verified quotes.
- [x] GitHub repository published and accessible at the configured remote.
- [x] PDF/document extraction for think-tank reports.
- [x] Fetch policy caps responses at 5 MB and checks `Content-Length` before streaming.
- [x] Migrations `0007_widen_alembic_version`, `0007_audit_events_monthly_partition`,
      `0008_enable_tier4` and `0009_source_availability` pass an optional Alembic downgrade/upgrade
      round-trip in the integration tests; that proves reversible migration, not a database backup
      or restore drill.
- [x] Rendered JavaScript fallback behind `EXTRACTION_FALLBACK=rendered` and
      `extractor_hints.render_js=true`, disabled by default and rights-gated.
- [x] Optional Neo4j projection and bounded GraphRAG context packs; Postgres remains the system of
      record.
- [x] Stale no-live-probe wording narrowed on approved source routes and the CSV mirrors
      regenerated; documented `blocker_reason` notes on blocked sources (Kyodo, RIA and others)
      remain.
- [x] Synthetic fixture and offline-proof artifacts isolated under `var/test-output/`; configured
      Postgres persistence is skipped for fixture runs unless explicitly requested.
- [x] `mq run` defaults to rights-gated live collection; `--from-blobs` and `--fixtures` are
      explicit cached-live and synthetic-test modes.
- [x] Manual Dagster partition-range backfill uses the shared budget/archive service and marks
      daily partitions as materialised.
