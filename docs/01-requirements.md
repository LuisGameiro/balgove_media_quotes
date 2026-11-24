# 01 — What the system does

## Purpose

`media-quotes` collects media quotes from rights-cleared sources, verifies them against the
original article text, and writes a daily Markdown digest. It is a deterministic offline-first
system: no live source, database or LLM is contacted unless the operator explicitly enables it.

The two constraints that shape every design choice:

1. **Rights first.** A source is contacted only when its registry row permits the exact use case.
   Reuters and BBC remain mandatory; all 28 sources are now licensed for retrieval, storage,
   LLM grounding, verbatim republication and the historical backfill. The T0 gate stays in code
   as a fail-closed guard for any future unlicensed row.
2. **Nothing unverified is published.** Quotes must match an exact span in the extracted article
   body, carry attribution, pass the junk and word-limit checks, and come from a first-hand
   source with preserved policy flags.

## Outputs

All generated output files live under `OUTPUT_DIR` (`var/output/` by default):

| Output | Path | Contents |
|---|---|---|
| Daily digest | `var/output/digests/YYYY-MM-DD.md` | Verified quotes by section, with the degraded-mode notice when gaps exist |
| Derived export | `var/output/quotes/quotes-YYYY-MM-DD.jsonl` | Strict `ExportQuoteRecord` objects; derived, never the system of record |
| Audit report | `var/output/audit/audit-YYYY-MM-DD.json` and `.md` | Coverage, gaps, gates, checks, review queue, version manifest |
| Run log | `var/output/logs/<run-id>.jsonl` | Structured events with `trace_id` |
| Postgres rows | tables in `src/media_quotes/db/schema.sql` | Append-only articles, quotes, runs, audit events, cache and governance tables |

## Guarantees

- **Exactness.** Quote offsets are realigned against the extracted text, then `verify_quote`
  confirms they are exact spans in the stored article text. Normalised text is used for matching,
  never for rendering.
- **Rights limits.** At most 30 words per quote, named attribution, internal territory. The
  renderer applies these again before publication.
- **Determinism.** Locale/date/version inputs are pinned; the LLM boundary uses strict models,
  content-addressed cache keys and injected clients.
- **Fail closed.** A source must pass the T0 gate before collection; a low-quality extraction,
  a refusal or a failed verification check keeps the quote out of the digest and reports the gap.
- **Idempotence.** Re-running a date reuses canonical ids instead of creating a second business
  record; file writes go through atomic replace.
- **Auditability.** Every run writes a version manifest, source coverage and trace ids; Postgres
  is the system of record when persistence is enabled.

## Modes

- **Local / default:** fixture replay, no network, no database writes, deterministic LLM stand-in.
- **Live fetch:** `mq fetch --source-id <id> --live` for a rights-cleared source; still no database writes.
- **Persistent:** `PERSIST_TO_POSTGRES=true` writes run, article, quote, audit and digests rows.
- **Live inference:** `LLM_PROVIDER=openrouter` with a pinned model and key; changes only section
  inferences, never verification or the rights gate.

## Acceptance targets

The gold set is versioned and split 60/20/20. Targets are exact span match 100%, false-quote
rate 0%, recall at least 0.90, metadata accuracy at least 0.95, and judge agreement at least
0.70. Branch coverage of `src/media_quotes` must stay at or above 85%.

Full command syntax is in [05-command-reference.md](05-command-reference.md); the source
catalogue is in [03-sources.md](03-sources.md).
