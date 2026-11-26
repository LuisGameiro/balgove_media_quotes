# 06 — What is left

Open work first, grouped by the requirement it limits. The completed section records milestones, not
a checklist to maintain.

## Open work

### FR-2.2 — daily source checks

- Enable the daily and six-hour Tier-1 Dagster schedules in the target environment (flag plus
  `-EnableSchedules` switch; deployment still pending). See
  [Dagster automation](05-command-reference.md#dagster-automation).
- Prove Reuters and BBC are checked on every required day; record failures as coverage gaps.
- Supply a working licensed Reuters endpoint and clear its HTTP 401 gap.
- Clear AP's 401; provide a Guardian API key and licensed NYT full-text route.
- Fix RT timeout and Xinhua's stale HTTP-only feed; improve TASS/RIA quote yield.
- Keep GDELT discovery-only and Bloomberg/FT/Economist explicitly partial without full-text routes.
- Replace synthetic completeness evidence with measured real-source recall.

### FR-2.3 — live collection and backfill

- Execute and audit the initial live backfill from 2026-10-01.
- Confirm estimates stay within ±20% of actual spend across three real runs.
- Confirm zero duplicate billed calls and at least 60% cache hits on a repeat backfill.
- Obtain human first-hand sign-off for every newly scaffolded source.

### FR-2.4 — digest fidelity

- Enable the review SLA sensor (flag plus `-EnableSensor` switch;
  [how](05-command-reference.md#dagster-automation)) and prove the 24-hour SLA.
- Obtain held-out human obsolete labels and confirm precision of at least 0.90.
- Calibrate judge agreement before using production thresholds.

### OUT-3 and Q-4 — dashboard and quality

- Deploy the dashboard with its password secret and confirm MD, logs and coverage render.
- Measure cleaning, relevance and metadata accuracy on real mandatory-source articles.
- Run a live load test with no transaction over 60 seconds.
- Check cold-partition detachment, tombstone hashes and a quarterly restore drill.

### Production operations

- Rehearse a production backup/restore; there is no downgrade migration.
- Provision the production OTLP collector and enable `OTEL_ENABLED=true`.
- Register scheduled tombstone-gated `mq blob-prune --tombstones PATH --execute` runs using
  `scripts/ops/blob_prune_scheduled.ps1` (helper added; scheduler registration pending).

## Completed

- Verbatim quote whitespace in the digest renderer, with Markdown heading/link neutralisation.
- Licence word cap applied to translated text as well as the original.
- Exact-span target of 100% documented against a 0.95 automated acceptance floor.
- DNS validation pinned to the connected IP (including shared clients), removing the advisory gap.
- Bounded historical archive discovery with resumable manifests, a 365-day cap and a source filter.
- Automated admission probing for robots/terms and route reachability; probes grant no rights.
- Same-speaker obsolescence proposals with queue merging and atomic persistence.
- Read-only quote/article browsing in the dashboard, plus a configured Git remote.
- PDF extraction and a 5 MB fetch cap, with fail-closed metadata checks.
- Consolidated `migrations/schema.sql`; live rows preserved and `audit_events` partitioned.
- Scoped artifacts, canonical-path rules and provenance tags on stored records.
- Fail-closed mandatory-source coverage, backfill scope checks and PDF metadata checks.
- Pinned model snapshots, decision caching, redacted logs and gold-set evaluation.
- Dashboard session TTL, per-session lockout and observed-month retention plans.
- 2026-11-24 Tier-1 canary: two BBC `live_feed` quotes from official-feed discovery, with Reuters
  HTTP 401 disclosed as a gap. Point-in-time evidence only, not proof of completeness.
