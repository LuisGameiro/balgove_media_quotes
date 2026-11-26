# 01 — Requirements and current status

[`Test_News.md`](Test_News.md) is the immutable assignment. This document maps it to the current
checkout. It does not amend the brief. Design choices are in [07 — Decision log](07-decision-log.md).

**Implemented** means the feature works in the stated mode. **Partial** means a material part is
missing or disabled.

## Traceability

Status comes first so the open items are easy to scan.

| ID | Status | Brief requirement | Current implementation |
| --- | --- | --- | --- |
| FR-2.1 | Implemented in persistent mode | Auditable quote database and LLM-friendly storage | Postgres schema, strict JSONL export, content hashes, offsets, audit events and optional Neo4j. Offline JSONL is a local replay store. |
| FR-2.2 | Partial | Deterministic daily Reuters/BBC checks | Versioned topic rules, rights-gated adapters, exact-span verification and `mq run --live`; full runs fail closed when mandatory sources have zero quotes. Schedules are stopped and Reuters returns HTTP 401. |
| FR-2.3a | Implemented | Extract and verify quote text and metadata | Exact-span verifier, attribution and junk checks, first-hand evidence and rights guard. Covers fixtures, blobs and live pages. |
| FR-2.3b | Partial | Collect from 2026-10-01 onward | `BACKFILL_START=2026-10-01`, bounded archive discovery and resumable partitions. No completed production backfill. |
| FR-2.3c | Implemented | Select a historical period on request | `mq backfill --from ... --to ...` with a 365-day cap and `--source-id` filter. |
| FR-2.3d | Partial | Onboard another source on request | `mq sources --action add --probe --live` scaffolds a blocked config and probes admission. Rights grants and first-hand sign-off stay manual. |
| FR-2.4 | Partial | Daily 14-day digest with sections and inference | Deterministic renderer, six sections, obsolescence proposals and injectable inference. Text fields are capped and human review evidence is pending. |
| OUT-3 | Partial | Dashboard and GitHub repository | Tabbed Streamlit dashboard, read-only quote browser, structured logs, read-only Postgres counts and a configured remote. Deployment pending. |
| Q-4 | Partial | Relevant, clean, accurate, auditable, fault-tolerant processing | Verification, coverage gaps, audit reports, gold-set evaluation and CI gates. Real-source completeness is unproven. |

## Quote metadata

| Brief field | Storage / export |
| --- | --- |
| Quote ID | `quotes.canonical_quote_id`, `ExportQuoteRecord.quote_id` |
| Quote text | `quotes.quote_text_raw`, `quote_text_original`, optional `quote_text_en` |
| Source ID and name | `sources.source_id`, `sources.source_name` |
| Publication date | `articles.publication_date` |
| Article name and link | `articles.title`, `articles.canonical_url` |
| Extraction date | `articles.processed_at`, `quotes.created_at` |
| Audit data | Byline, publication, language, source type, rights and licence reference, content hash, offsets, verification and obsolescence state |

Postgres is the system of record when `PERSIST_TO_POSTGRES=true`. The dated JSONL file is a
derived export in persistent mode and the only local quote store in offline replay mode.

## Deterministic and model boundaries

**Deterministic:** discovery, topic relevance, extraction, exact-span verification, section
assignment, deduplication, rights limits and Markdown selection. Quote text and metadata never come
from a model — a quote must match the stored article.

**Model boundary:** section summaries and optional translation, judging or obsolescence suggestions.
The default `local` provider is a labelled deterministic stand-in; live OpenRouter needs a pinned
model and Postgres caching.

## Digest contract

`var/output/digests/YYYY-MM-DD.md` covers the rolling 14-day window ending on the report date.
The title states the period. Quotes are grouped under War Intensity, International Support, Peace
Initiatives, Sanctions, Conflicting Parties’ Resources and Other. Each quote carries verbatim text,
source, publication, article name, date, byline when present and a canonical link. Each non-empty
section gets one or two sentences of inference.

Current limits: obsolescence flags wait for human review, and the default inference is not a live
model. A failed inference call inserts a fixed marker and an audit event
without dropping quotes or logs. Licence territory is metadata only. See
[06 — What is left](06-whats-left.md).

## Acceptance targets

| Measure | Target | Evidence |
| --- | --- | --- |
| Article and quote recall | at least 0.90 | Gold-set metric; real-source completeness not verified |
| Exact span match | 100% | Enforced floor is 0.95 pending real-source calibration; see [CHK-03](07-decision-log.md#referenced-checks) |
| Metadata accuracy | at least 0.95 | Gold-set metric |
| False quotes | 0 | Fail-closed verifier and evaluation contract |
| Section accuracy | at least 0.90 | Evaluation contract |
| Obsolete precision | at least 0.90 | Held-out human labels pending |
| Judge agreement | κ at least 0.70 | Gold-set calibration |
| Branch coverage | at least 85% | Enforced by the quality gate |

Key decisions: DEC-03 (gold-set targets), DEC-10 (licence limits), DEC-30 (canonical artifacts) and
DEC-35 (opt-in automation), all in [07 — Decision log](07-decision-log.md).

A real Tier-1 canary on 2026-11-24 collected two BBC `live_feed` quotes; Reuters returned HTTP 401
and was disclosed as a gap. That is point-in-time evidence that live collection works, not proof
of completeness.

## Operating modes

- **Fixture replay** — default, no network or database writes, isolated artifacts.
- **Blob ingest** — `mq run --from-blobs` processes pages already downloaded by `mq fetch`.
- **Live collection** — `mq run --live` and `mq backfill ... --execute --live` prefer official feeds;
  Bing News is an opt-in discovery fallback and live mode never replays fixtures.
- **Persistent** — `PERSIST_TO_POSTGRES=true` writes business and audit rows.
- **Live inference** — `LLM_PROVIDER=openrouter` with a pinned model, key and persistence.

Canonical daily paths are reserved for a complete `live_feed` run with no filter. Partial runs write
under `<artifact-root>/runs/<scope>/` and create no canonical Postgres digest manifest.

Commands are in [05 — Command reference](05-command-reference.md); source policy is in
[03 — Sources and rights](03-sources.md).
