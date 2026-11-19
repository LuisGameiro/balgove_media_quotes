# 01 — Requirements analysis

Source: [`Test_News.md`](Test_News.md). Integrated requirements baseline: the brief
plus the adopted improvements. Every requirement carries a stable ID (`REQ-…`/`OUT-…`);
every interpretation decision references a register row (`DEC-…`) in
[04](04-risks-and-open-questions.md); requirement → decision → task → check mappings live in
[08-traceability.md](08-traceability.md), not in this document. The **minimum deliverable**
is the brief's §3–§4. Items marked **(plus)** are optional.

> **Notation:** \"T0 gate\" = the rights gate in brief §1; task `T0` = the first task in
> [03](03-work-breakdown.md). Design constraints: [06](06-observations.md); decisions and
> accepted risks: [04](04-risks-and-open-questions.md); per-source detail: [07](07-sources.md).

## 1. Objective

An automated system that detects, collects, verifies and presents media quotes that help
develop a **substantial understanding of current trends and expectations about Russia's war
on Ukraine**. Mandatory sources: Reuters, BBC News (DEC-01). Tier 2/3 sources and new
sources are plus-scope, all subject to the same T0 gate.

## 2. Sources, tiers and T0 gate

| Tier | Role | Cadence | Status |
|---|---|---|---|
| 1 | Reuters, BBC News — **mandatory** | at least every 6 hours | `blocked` until written licence (DEC-01) |
| 2 | AP, Bloomberg, FT, Economist, Guardian, DW, Le Monde (plus) | at least daily | per-source rights record |
| 3 | Non-Western original reporting (11 outlets, plus) | at least daily | per-source rights record |
| 4 | RT, TASS, RIA Novosti, Xinhua, Global Times | not monitored by default | excluded; opt-in as labelled *claim/perspective* only |

A source is enabled only when `rights_status` ∈ {`license_held`, `api_permitted`} **and** a
`license_reference` is recorded; `pending_counsel_review` and `blocked` keep it disabled.
Canonical enums (shared with `data/quotes.schema.json` and the Postgres `sources` table):

- `rights_status`: `license_held | api_permitted | pending_counsel_review | blocked`
- `access_mode`: `licensed_database | official_api | public_rss_permitted | pending_review | scraped_prohibited`
- `source_type`: `independent | publicly funded | state-affiliated | state-controlled | aggregator`
- `discovery_method`: `sitemap | rss | api | gdelt | web`
- `original_reporting_flag`: `first-hand | relayed wire copy | unknown`
- `claim_only`: boolean; state-controlled/aggregator output is labelled perspective, never fact.

RSS is not an exemption; feeds alone cannot satisfy \"all material quotes\" or the backfill.
Admission also requires first-hand reporting with stored evidence, stable canonical
URL/date/byline, an archive back to `BACKFILL_START` (2026-10-01), named attribution and
recorded type/country/language. GDELT/Guardian/NYT are discovery or fallback, **never a
substitution for the mandatory sources** (DEC-01). Per-source status and enabling
requirements: [07](07-sources.md).

## 3. Functional requirements

### 3.1 Database creation

Postgres is the **system of record**: per quote — `Q-YYYYMMDD-NNNN`, original + normalised
text, source ID/name/country/type, original-reporting flag, language, publication date,
article name/author/URL, extraction date, rights status, access mode, license reference,
discovery method, section, obsolescence status, and provenance (`run_id`, `content_hash`,
versions, `gold_set_version`, `trace_id`). Immutable raw bytes, extracted text with offsets,
LLM decisions, verifier results and audit events live in **separate append-only** tables;
corrections are new versioned rows. The JSONL export and all context packs are **derived**
from Postgres (UTF-8, no BOM, one JSON value per line, `.jsonl`), never hand-maintained.

### 3.2 Relevant articles and quotes detection

- Check each **enabled, T0-gate-approved** source automatically and **deterministically** at
  tier cadence; log every check with `trace_id`.
- Feeds/sitemaps for discovery only where permitted; full text only from the approved
  `access_mode`.
- \"Read\" selected articles, then reason (guarded LLM) about quote relevance using the
  rubric in §6 #12.

### 3.3 Quotes collection

- Extract quote + metadata; preserve original formatting and DOM offsets.
- Candidate extraction selects self-contained, contiguous verbatim sub-spans of 20 chars ≤ length ≤ 30 words
  (DEC-10) so exact-span verification and licence limits are strictly harmonized without frankenquotes or truncation.
- For non-English sources (`lemonde`, `ukrainskapravda`, `suspilne`, `ria`), generate an English
  translation in `quote_text_en` without altering `quote_text_original`.
- Verify with **deterministic tools**: NFC + whitespace/smart-quote normalised exact-span
  match, attributed speaker, non-empty metadata, junk filters, dedupe, source match, and
  first-hand evidence; fail closed to `unverified`. No frankenquotes, hallucinated sources
  or empty metadata.
- Backfill `BACKFILL_START` → `REPORT_DATE`. **(plus)** arbitrary past period on request.
  **(additional plus)** user-named new source: T0 gate + §2 admission, registry entry,
  backfill from `BACKFILL_START`.

### 3.4 Reasoning and presentation

- Daily MD at `digests/REPORT_DATE.md`, titled with its period, covering the rolling
  `REPORT_WINDOW_DAYS` (default 14) ending on `REPORT_DATE`.
- Sections (War Intensity, International Support, Peace Initiatives, Sanctions, Conflicting
  Parties' Resources, etc.); deterministic insertion — no text or metadata changes.
- Obsolete facts excluded from the MD, retained in the database with rationale and
  `superseded_by` (rule in §6 #13).
- Under each section, a brief (1–2 sentence) LLM inference — the only non-deterministic part.

### 3.5 Out of scope (initial build)

- External publication of the digest or redistribution of verbatim quotes (DEC-02).
- Redistribution of full text from GDELT/Guardian/NYT or any source beyond its licence.
- Enabling state-controlled outlets (RT, TASS, RIA Novosti, Xinhua, Global Times) as fact sources (DEC-13).
- Neo4j/pgvector-backed RAG or graph queries (DEC-09; see [09](09-neo4j-integration.md)).
- In-place edits/deletes of audit rows, blob/lifecycle automation beyond retention defaults, or live API-key handling (T1/T9).

## 4. Output requirements (minimum)

| ID | Requirement | Brief anchor |
|---|---|---|
| OUT-01 | Auditable database with quotes and relevant metadata | §3 item 1; §2.1 |
| OUT-02 | Dashboard: latest MD files, logs, database access | §3 item 2 |
| OUT-03 | Dashboard view of per-source coverage (data contract in [02](02-stack-and-architecture.md) §4) so under-covered or failed sources are visible at a glance | §3 item 3 |
| OUT-04 | GitHub repository with relevant code | §3 item 4 |
| OUT-05 | All of the above reachable through one command interface (`mq run`, `mq audit`, `mq logs`; contract in [02](02-stack-and-architecture.md) §5) | derived from §3/§4 auditability |

## 5. Quality requirements (minimum)

| ID | Requirement | Mode | Brief anchor |
|---|---|---|---|
| REQ-01 | No source is ingested without recorded T0-gate approval; logs include `license_reference` | both | §4 item 1; §1 T0 gate |
| REQ-02 | All material articles from mandatory sources are detected | full (see §8) | §4 item 2 |
| REQ-03 | Tier 3 outlets represented; each quote verified first-hand, not relayed wire copy | both | §4 item 3; §1 Tier 3 |
| REQ-04 | Source type and attribution stored; state-controlled material labelled, never treated as fact | both | §4 item 4; §2.1 |
| REQ-05 | All material quotes from those articles are detected | both (measured) | §4 item 5 |
| REQ-06 | Quotes relevant, substantial, self-explanatory and cleansed (no ads/page numbers/table symbols) — sub-checks: (a) rubric §6 #12 passes, (b) first-hand + named attribution present, (c) isolated meaning clear to the judge, (d) junk/boilerplate filters clean | both | §4 item 6 |
| REQ-07 | All material quotes extracted and saved — nothing important is left out | both (measured) | §4 item 7 |
| REQ-08 | Extraction of quotes and metadata accurate against the primary source | both | §4 item 8 |
| REQ-09 | MD load accurate: verbatim text, correct categorisation, no obsolete quotes | both | §4 item 9 |
| REQ-10 | Section interpretations accurately describe the selected quotes — sub-checks: (a) grounded in the section's selected quote IDs, (b) no unsupported facts, (c) 1–2 sentences, (d) judge agreement on the gold set | both | §4 item 10 |
| REQ-11 | Database auditable and easily integrable with frontier-lab LLMs | both | §4 item 11; §2.1 |
| REQ-12 | Logs saved for auditability and traceability | both | §4 item 12 |
| REQ-13 | Processing handles errors (network, unavailable content, etc.) | both | §4 item 13 |
| REQ-14 | Articles of any size or content type processable (rendering fallback for JS/PDF) | both | §4 item 14; §2.2 |

`source_type` uses the superset in §2 (five values) so the schema can represent
`state-affiliated` and `aggregator`; the brief's §2.1 lists three of these.

\"All material\" (REQ-05/REQ-07) is made measurable by the gold set and §7 thresholds.

## 6. Interpretation decisions

Concise by design; canonical tool parameters live in [02](02-stack-and-architecture.md) §4.
Register rows in [04](04-risks-and-open-questions.md) §1.

| # | Issue | Decision (register ref) |
|---|---|---|
| 1 | \"Deterministic (not probabilistic)\" | Deterministic coverage, scheduling, extraction, verification, rendering; LLM only for semantic judgement under pinned versions, cache, strict validation, replayable logs. `temperature=0` is not determinism. (DEC-05) |
| 2 | \"All material articles/quotes\" | Measured via the gold set and thresholds §7. (DEC-03) |
| 3 | Quote \"verbatim\" | Original characters + DOM offsets, normalised copy for matching; render only the original. Contiguous self-contained span ≤ 30 words per DEC-10. |
| 4 | Dates | Parameterised: `BACKFILL_START=2026-10-01`, `REPORT_DATE` (default today), `REPORT_WINDOW_DAYS=14`; snapshots freeze the clock. |
| 5 | Paywalls / access rights / mandatory sources | T0 gate; Reuters and BBC stay mandatory but rights-cleared only, no substitution; gaps logged. (DEC-01; [06](06-observations.md) §1) |
| 6 | Digest audience | Internal-only. (DEC-02) |
| 7 | Eval harness | Built early; deterministic checks + κ-calibrated judge; never OpenAI Evals. (DEC-06) |
| 8 | Version pinning | Full manifest in every decision/audit row — see [02](02-stack-and-architecture.md) §3. |
| 9 | Extraction, observability, testing, storage | Canonical parameters in [02](02-stack-and-architecture.md) §4; choices registered as DEC-04/07/08/09. |
| 10 | Schema & prompt freeze ownership | The frozen JSON Schema (`data/quotes.schema.json`) and prompt templates are owned by Balgove Innovations; any change updates the version manifest and is recorded in [08](08-traceability.md) (snapshot tests CHK-04). |
| 11 | Section taxonomy | Canonical allow-list from `data/sections.csv` (`war_intensity`, `international_support`, `peace_initiatives`, `sanctions`, `resources`, `other`); only these labels may be assigned, after relevance selection (#12). |
| 12 | Relevance rubric | A quote is *relevant/substantial* when first-hand, attributed to a named person and role, self-explanatory in isolation, and adding a fact, trend, expectation or judgement about the war. Drives human labels, LLM prompts and the judge; section assignment (#11) happens only after this rubric passes. |
| 13 | Obsolescence rule | *Superseded* = a later, rights-cleared, first-hand source contradicts or replaces the fact. Store `obsolete_reason` + `superseded_by` + decision id; ambiguous cases go to the review queue (1 working day); precision measured on a held-out set. |
| 14 | Source registry model | `sources.csv` is authoritative at commit; Postgres mirrors it at bootstrap; `mq doctor` verifies equality. |
| 15 | Gold set | ~60–100 stratified articles (mandatory via fixtures, Tier 3, Tier 2); 60/20/20 split; double-label ~20%; `gold_set_version` pinned. (DEC-03) |
| 16 | Toolchain pinning | Alembic migrations + `uv.lock`, ruff/`mypy --strict`, 85% coverage (DEC-15/DEC-16, [02](02-stack-and-architecture.md) §7) |

## 7. Acceptance thresholds (gold-set test split; fail-closed below)

- Published quotes: exact-match **100%**, false-quote rate **0**, schema-valid **100%**;
  refusal/incomplete logged and excluded.
- Article recall **≥ 0.90**, quote recall **≥ 0.90**, span exact-match **≥ 0.95**, metadata
  accuracy **≥ 0.95**, obsolete precision **≥ 0.90**.
- Judge calibration: Cohen's κ **≥ 0.70** against the double-labelled slice; no tuning on
  the test split.
- On any breach below: fail closed — the run exits `1` (validation/threshold) or `2`
  (coverage gap), publishes no unverified quote, and records the failing check in the audit
  report (CHK-02/CHK-03; exit codes in [02](02-stack-and-architecture.md) §5).

## 8. Definition of done (two modes)

**Blocked/degraded mode** (current, DEC-01): `mq doctor validate bootstrap` are the baseline commands
available in the T1 scaffold, and all 12 operator subcommands (`mq run`, `mq audit`, `mq logs`, `mq eval`, …)
are implemented in deterministic offline fixture-replay mode; T9 wires them to live Dagster/Postgres
partitions; `mq run --date …` processes fixtures/permitted sources and **explicitly reports blocked
mandatory sources as coverage gaps**, deterministic gates pass, and the dashboard shows the gaps.
To avoid a complete standstill while Tier 1 enterprise licences are negotiated, rights review is
sequenced via a Two-Track plan (Track A fast-tracks open/API sources like Guardian and Suspilne;
Track B negotiates commercial syndication). REQ-02 is evaluated as \"all articles from legally
retrievable sources, with the gap recorded\" — not a silent pass.

**Licensed/full mode** (after written rights): all §5 requirements including REQ-02 are
measurable against the gold set and coverage contract; §7 thresholds must be met; `mq run`
still completes in one command with audit + log summary.
