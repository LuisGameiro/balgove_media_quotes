# 01 — Requirements and current status

## Purpose

[`Test_News.md`](Test_News.md) is the immutable assignment. This document maps that brief to the
current implementation; it does not amend the brief. Design choices and their rationale are
recorded in [07-decision-log.md](07-decision-log.md).

Status meanings: **Implemented** is available in the stated mode; **Partial** means a material
part is missing or disabled; **Not verified** means the checkout cannot prove the requirement.

## Requirement traceability

| ID | Brief requirement | Current implementation | Status |
|---|---|---|---|
| FR-2.1 | Auditable quote database with required metadata and LLM-friendly storage | Postgres schema, strict JSONL export, content hashes, offsets, audit events and optional Neo4j projection | Implemented in persistent mode; offline JSONL is a local replay store |
| FR-2.2 | Deterministic daily checks of Reuters and BBC, article detection and quote reasoning | Versioned topic rules, rights-gated adapters, extraction, two-stage verification and `mq run --live` (official feed first, Bing News only as opt-in discovery fallback) | Partial: live collection works in run/backfill modes but never falls back to fixtures; schedules are stopped and the enabled Reuters route returns HTTP 401 |
| FR-2.3a | Extract and deterministically verify quote text and metadata | Exact-span verifier, attribution and junk checks, first-hand evidence and rights guard | Implemented for fixture, fetched-blob and live-collected inputs |
| FR-2.3b | Collect 2026-10-01 through the current date | `BACKFILL_START=2026-10-01`, bounded archive discovery (RSS/sitemap/sitemap-index) and resumable partitions | Partial: live historical discovery implemented; no completed production backfill |
| FR-2.3c | Optional historical period selection | `mq backfill --from ... --to ...` with a 365-day cap, `--source-id` filter and budget gate | Implemented for fixtures, prefetched blobs or `--live` discovery |
| FR-2.3d | Optional automatic onboarding of another source | `mq sources --action add --probe --live` scaffolds a blocked config and runs a bounded admission probe | Partial: probes are admission-only; rights grants and first-hand sign-off remain manual |
| FR-2.4 | Daily 14-day Markdown digest with sections, verbatim metadata, non-obsolete quotes and LLM inference | Deterministic renderer, six canonical sections, daily same-speaker obsolescence proposals, review flags and injectable inference provider | Partial: quote whitespace is normalised and production review evidence is pending |
| OUT-3 | Dashboard for latest MD files, logs and database access; GitHub repository | Tabbed Streamlit dashboard, read-only JSONL quote/article browser, structured logs, read-only Postgres counts and configured Git remote | Partial: browser implemented; dashboard deployment is pending |
| Q-4 | Complete, relevant, clean, accurate, auditable and fault-tolerant processing | Verification, coverage gaps, retries, audit reports, gold-set evaluation and CI gates | Partial: real-source completeness and several content-type limits remain unproven |

## Required quote metadata

| Brief field | Storage or export mapping |
|---|---|
| Quote ID | `quotes.canonical_quote_id`; `ExportQuoteRecord.quote_id` |
| Quote text | `quotes.quote_text_raw`; `quote_text_original`, with optional `quote_text_en` |
| Source ID | `sources.source_id`, joined through `articles.source_id` |
| Source name | `sources.source_name`; `source_name` |
| Publication date | `articles.publication_date`; `publication_date` |
| Article name | `articles.title`; `article_name` |
| Article weblink | `articles.canonical_url`; `article_url` |
| Extraction date | `articles.processed_at` and `quotes.created_at`; `extraction_date` |
| Additional audit data | Byline, publication name, language, source type, rights status, licence reference, content hash, character offsets, verification details and obsolescence state |

Postgres is the system of record when `PERSIST_TO_POSTGRES=true`. The dated JSONL file is a
derived export in persistent mode and the only local quote store in offline replay mode.

## Deterministic and LLM boundaries

- Discovery, topic relevance, extraction, exact-span verification, section assignment,
  deduplication, rights limits and Markdown selection are deterministic.
- Quote text and metadata never come from an LLM. A quote must match the stored article text.
- The LLM boundary supplies section inferences and optional translation, judging or
  obsolescence suggestions. The default `local` provider is a labelled deterministic stand-in.
- OpenRouter inference uses a pinned model, strict output validation and a content-addressed
  cache; live provider use requires Postgres persistence.

## Digest contract

The renderer writes `var/output/digests/YYYY-MM-DD.md` for the rolling 14-day window ending on
the report date. The title contains the period. Quotes are grouped under War Intensity,
International Support, Peace Initiatives, Sanctions, Conflicting Parties’ Resources and Other.
Each quote carries verbatim source text, source name, publication name, article name,
publication date, byline when present and the canonical link. Each non-empty section receives
a one- or two-sentence inference.

Current limitations are explicit: the daily pipeline proposes same-speaker supersessions, but a
human still reviews every flag before a quote can be hidden; the renderer collapses whitespace
and escapes Markdown syntax; the default inference is not a live LLM. Licence territory is
metadata only under the decision log, not a renderer control. See [06-whats-left.md](06-whats-left.md).

## Outputs

| Output | Path | Purpose |
|---|---|---|
| Daily digest | `var/output/digests/YYYY-MM-DD.md` | Human-readable rolling report |
| Derived export | `var/output/quotes/quotes-YYYY-MM-DD.jsonl` | Strict machine-readable quote records |
| Audit report | `var/output/audit/audit-YYYY-MM-DD.json` and `.md` | Coverage, gaps, gates and review queue |
| Run log | `var/output/logs/<run-id>.jsonl` | Structured events with trace IDs |
| Source proof | `var/output/proof/source-proof-YYYY-MM-DD.json` and `.md` | Per-source live, sample and fixture evidence with blockers |
| Postgres tables | `src/media_quotes/db/schema.sql` | Authoritative persistent records when enabled |
| Neo4j projection | `src/media_quotes/graph/` | Optional derived graph and bounded context packs |

## Acceptance targets

| Measure | Target | Current gate or evidence |
|---|---|---|
| Article recall | At least 0.90 | Gold-set metric; real-source completeness not yet verified |
| Quote recall | At least 0.90 | Gold-set metric; real-source completeness not yet verified |
| Exact span match | 100% target | Automated default is 0.95; reconciliation is open |
| Metadata accuracy | At least 0.95 | Gold-set metric |
| False-quote rate | 0 | Fail-closed verifier and evaluation contract |
| Section accuracy | At least 0.90 | Evaluation contract |
| Obsolete precision | At least 0.90 | Human-labelled production evidence pending |
| Judge agreement | κ at least 0.70 | Gold-set calibration |
| Branch coverage | At least 85% | Enforced by the full coverage gate |

“All material articles and quotes” is an acceptance objective, not a claim that the current
synthetic gold set proves production completeness. A real Tier-1 canary on 2026-11-24 collected
two BBC `live_feed` quotes; Reuters returned HTTP 401 and was disclosed as a coverage gap. That
run is point-in-time evidence that live collection works, not proof of completeness.

## Operating modes

- **Fixture replay:** default; no network or database writes. Fixture artifacts are isolated.
- **Blob ingest:** `mq run --from-blobs` processes previously fetched HTML.
- **Live fetch:** `mq fetch --source-id <id> --live` performs a separate rights-gated download.
- **Live collection:** `mq run --live` and `mq backfill ... --execute --live` collect from official
  feeds first with Bing News only as an opt-in discovery fallback; live mode never falls back to
  fixtures. Acquisition provenance (`fixture`, `live_feed`, `live_bing`, `manual_sample`) is stored
  on articles, quotes, exports, digests and review queues.
- **Persistent:** `PERSIST_TO_POSTGRES=true` writes business and audit rows.
- **Live inference:** `LLM_PROVIDER=openrouter` with a pinned model, key and persistence.

Commands are in [05-command-reference.md](05-command-reference.md); source policy is in
[03-sources.md](03-sources.md); architecture is in [02-architecture.md](02-architecture.md).
