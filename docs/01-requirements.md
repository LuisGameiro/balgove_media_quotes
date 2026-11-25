# 01 — Requirements and current status

## Purpose

[`Test_News.md`](Test_News.md) is the immutable assignment. This document maps that brief to the
current implementation; it does not amend the brief. Design choices and their rationale are
recorded in [07-decision-log.md](07-decision-log.md).

Status meanings: **Implemented** is available in the stated mode; **Partial** means a material
part is missing or disabled; **Not verified** means the checkout cannot prove the requirement.
Source acquisition also records `availability_status`, `tier_block` and `blocker_reason`.
Fixture-only proof is software validation and never counts as collected source data.

## Requirement traceability

| ID | Brief requirement | Current implementation | Status |
|---|---|---|---|
| FR-2.1 | Auditable quote database with required metadata and LLM-friendly storage | Postgres schema, strict JSONL export, content hashes, offsets, audit events and optional Neo4j projection | Implemented in persistent mode; offline JSONL is a local replay store |
| FR-2.2 | Deterministic daily checks of Reuters and BBC, article detection and quote reasoning | Versioned topic rules, shared rights-gated collection service, official feeds plus Bing News URL discovery (proof/discovery only; Bing is never a quote source), extraction and two-stage verification | Partial: `mq run` defaults live, schedules are stopped, Reuters is blocked by HTTP 401, and `tier_block` is enforced before collection |
| FR-2.3a | Extract and deterministically verify quote text and metadata | Exact-span verifier, attribution and junk checks, first-hand evidence and rights guard | Implemented for fixture and fetched-blob inputs |
| FR-2.3b | Collect 2026-10-01 through the current date | `BACKFILL_START=2026-10-01`, bounded backfill command, live archive discovery and idempotent partitions | Partial: the initial production backfill has been executed; its audit is still pending |
| FR-2.3c | Optional historical period selection | `mq backfill --from ... --to ...` with a 365-day cap and budget gate | Implemented for fixtures, prefetched blobs and bounded archive discovery |
| FR-2.3d | Optional automatic onboarding of another source | `mq sources --action add` scaffolds a disabled, pending-review JSON config | Partial: rights, archive and first-hand admission remain manual |
| FR-2.4 | Daily 14-day Markdown digest with sections, verbatim metadata, non-obsolete quotes and LLM inference | Deterministic renderer, six canonical sections, daily obsolescence proposals, review flags and injectable inference provider | Partial: proposals are wired, but live-judge evidence is pending and quote whitespace is normalised |
| OUT-3 | Dashboard for latest MD files, logs and database access; GitHub repository | Streamlit dashboard, structured logs, read-only Postgres counts and configured Git remote | Partial: no quote-table browser; repository is public at the configured remote |
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
| Extraction date | `article_metadata.extraction_date`; `extraction_date` export field (`articles.processed_at` exists in the schema but is not used for this mapping) |
| Additional audit data | Byline, publication name, language, source type, rights status, licence reference, content hash, character offsets, verification details and obsolescence state |

Postgres is the system of record when `PERSIST_TO_POSTGRES=true`. The dated JSONL file is
derived from verified records: it is a derived export in persistent mode and the only local quote
store in offline replay mode. It is rebuilt from Postgres only when `mq export --from-db` runs.

## Deterministic and LLM boundaries

- Discovery, topic relevance, extraction, exact-span verification, section assignment,
  deduplication, rights limits and Markdown selection are deterministic. Even with
  `AI_JUDGE_MODE=decision`, relevance and verification stay deterministic: the judge only adds
  obsolescence flags subject to human review and hold, and never admits or writes quote text.
- Quote text and metadata never come from an LLM. A quote must match the stored article text.
- The LLM boundary supplies section inferences and optional translation, judging or
  obsolescence suggestions. The default `local` provider is a labelled deterministic stand-in.
- OpenRouter inference uses a pinned model, strict output validation and a content-addressed
  cache; live provider use requires Postgres persistence.

## Digest contract

The renderer writes `var/output/digests/YYYY-MM-DD.md` for the rolling 14-day window ending on
the report date. The title contains the period. Quotes are grouped under War Intensity,
International Support, Peace Initiatives, Sanctions, Conflicting Parties’ Resources and Other.
Each quote renders its verbatim source text as a Markdown blockquote, followed by a metadata
block carrying the quote ID, detected quote language, claim-only flag, source and publication
names, article name, publication date and canonical link, plus speaker and byline when present
and a provisional-hold status when the quote is under review. Only non-empty sections receive a
one- or two-sentence inference; empty sections print a no-verified-quotes note. A provenance and
coverage footer records the report window, topic, source and quote counts, disclosed gaps,
obsolete and under-review counts, and the inference provider label.

Current limitations are explicit: the daily pipeline proposes new supersessions, but the default
offline judge produces no new flags and live-judge precision remains unproven; the renderer
collapses whitespace and escapes Markdown syntax; the default inference is not a live LLM. See
[06-whats-left.md](06-whats-left.md).

## Outputs

| Output | Path | Purpose |
|---|---|---|
| Daily digest | `var/output/digests/YYYY-MM-DD.md` | Human-readable rolling report |
| Derived export | `var/output/quotes/quotes-YYYY-MM-DD.jsonl` | Strict machine-readable quote records |
| Audit report | `var/output/audit/audit-YYYY-MM-DD.json` and `.md` | Coverage, gaps, gates and review queue |
| Run log | `var/output/logs/<run-id>.jsonl` | Structured events with trace IDs |
| Source proof | `var/output/proof/source-proof-YYYY-MM-DD.json` and `.md` | Per-source live/sample evidence; offline fixture proof defaults to `var/test-output/proof/` |
| Fixture replay | `var/test-output/` | Synthetic digests, quotes, audits and logs; never shown as operational output |
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
| Judge agreement | κ at least 0.70 | Synthetic placeholder only: `mq eval` reports `judge_kappa=1.0` with no judge model called; held-out calibration pending |
| Topic recall | At least 0.75 | Enforced by `mq eval` when a gold set measures it |
| Topic precision | At least 0.90 | Enforced by `mq eval` when a gold set measures it |
| Topic false-positive rate | 0 | Enforced by `mq eval` when measured; maximum metric |
| Language accuracy | At least 0.80 | Enforced by `mq eval` when a gold set measures it |
| Category coverage | At least 0.80 | Enforced by `mq eval` when a gold set measures it |
| Branch coverage | At least 85% | Enforced by the full coverage gate |

“All material articles and quotes” is an acceptance objective, not a claim that the current
synthetic gold set proves production completeness.

## Operating modes

- **Live run:** `mq run` defaults to rights-gated live fetching and writes `var/output/`; failures become coverage gaps without fixture fallback.
- **Blob ingest:** `mq run --from-blobs` processes previously fetched HTML without network access.
- **Fixture replay:** `mq run --fixtures` is the explicit synthetic test mode; artifacts go to `var/test-output/`, with no configured Postgres writes unless explicitly requested.
- **Single-source fetch:** `mq fetch --source-id <id> --live` downloads one source separately.
- **Persistent:** `PERSIST_TO_POSTGRES=true` writes business and audit rows.
- **Live inference:** `LLM_PROVIDER=openrouter` with a pinned model, key and persistence.
- **Source proof:** `python -m scripts.source_proof --live --limit 5` tests official feeds and
  Bing-discovered original pages; fixture-only evidence is reported separately as not collected.

Commands are in [05-command-reference.md](05-command-reference.md); source policy is in
[03-sources.md](03-sources.md); architecture is in [02-architecture.md](02-architecture.md).
