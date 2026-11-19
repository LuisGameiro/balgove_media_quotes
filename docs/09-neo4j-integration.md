# 09 — Neo4j integration (planning overview)

**Status:** planning overview only — Neo4j is **not adopted** in the initial build (DEC-09 in
[04](04-risks-and-open-questions.md)). This document describes *how the integration could be
done* if the revisit trigger fires: real graph queries, or a measured JSONL/HTTP bottleneck.
It does not change the system of record or the current stack.

**Notation:** "T0 gate" = the rights gate in brief §1; task `T0` = the first task in
[03](03-work-breakdown.md). All IDs below use the register/traceability conventions in
[08](08-traceability.md).

## 1. Why a graph at all

Today the system uses Postgres as the system of record and a derived JSONL context pack for
LLM contextualization ([01](01-requirements-analysis.md) §3.1). That is sufficient for the
daily 14-day digest. A graph would only earn its keep for queries that are awkward in
relational form:

- **Supersession chains** — which claim replaced which, across articles, dates and sources.
- **Speaker/topic networks** — which named people say what about a theme, and how that shifts.
- **Source coverage paths** — first-hand outlet → relayed copy → digest section, including
  `claim_only` perspective material.
- **Cross-article claim lineage** — the same assertion appearing in multiple outlets with
  different attribution.

None of these are in the minimum deliverable, so the graph stays a projection, not a dependency.

## 2. Guiding constraints (non-negotiable)

1. **Postgres remains the system of record.** Neo4j is a **derived, rebuildable projection**
   from Postgres tables; the JSONL/context-pack generation is unchanged.
2. **No new ingestion or rights path.** Only data that already passed the T0 gate and the
   deterministic verifier enters the graph; T0 gate, `license_reference` and `claim_only`
   travel with each node.
3. **Source of truth for deletion/correction** stays in Postgres append-only rows; a
   correction creates a new versioned row, and the projection reconciles.
4. **Provenance survives:** `run_id`, `content_hash`, `trace_id`, all version fields and
   `gold_set_version` follow each node/relationship.
5. **Fail-closed from the digest's perspective:** if the projection is stale, the digest
   continues from Postgres; the graph never gates publishing.

## 3. Proposed graph model

| Node | Key properties (from Postgres) | Purpose |
|---|---|---|
| `Source` | `source_id`, tier, `rights_status`, `access_mode`, `license_reference`, `claim_only`, country/type | rights-aware traversal; filter disabled/blocked sources |
| `Article` | `article_id`, canonical_url, publication_date, byline, `content_hash`, `trace_id` | anchors quotes and coverage |
| `Quote` | `quote_id`, original + normalised text, char_start/end, DOM path, `content_hash`, `is_obsolete`, `obsolete_reason` | the verified quote; verbatim text never rewritten |
| `Person` | normalised name, role, first-seen date | attribution network |
| `Claim` | claim key, text, first-seen `article_id` | supersession/cross-article lineage |
| `Section` | `section_id` from `sections.csv` | digest grouping |
| `Digest` | `digest_date` (`REPORT_DATE`), window start/end, md path | which quotes were shown when |

| Relationship | From → To | Notes |
|---|---|---|
| `PUBLISHED_BY` | `Article` → `Source` | carry `rights_status` snapshot |
| `CONTAINS` | `Article` → `Quote` | carry char offsets |
| `ATTRIBUTED_TO` | `Quote` → `Person` | carry attribution span |
| `IN_SECTION` | `Quote` → `Section` | carry `decision_id` |
| `SHOWN_IN` | `Quote` → `Digest` | deterministic load evidence |
| `SUPERSEDES` | `Claim`/`Quote` → `Claim`/`Quote` | carry rationale + `decision_id`; mirrors `superseded_by` |
| `SIMILAR_TO` | `Claim` → `Claim` | optional, lexical first; vector only with measured need |

Constraints: unique IDs, `claim_only` required on `Quote`/`Claim`, no relationship without a
`run_id` and `content_hash`. Blocked and state-controlled nodes are labelled, never silently
dropped — they simply do not appear in `claim`-as-fact queries.

## 4. Sync architecture

- **Trigger:** a Dagster asset check after each successful Postgres commit (run step), plus a
  nightly reconciliation asset. Same partition key as the digest (`REPORT_DATE`).
- **Method:** incremental upsert keyed by `(run_id, content_hash, version manifest)` from
  append-only Postgres rows; full rebuild available via `mq` operator command (planned as
  `mq graph rebuild` if the integration is adopted).
- **Idempotency:** re-running a sync with the same keys is a no-op; out-of-order runs are
  rejected by version comparison; deletions/corrections are new versions, never in-place edits.
- **Ordering:** sources → articles → quotes/people → sections/digests → relationships;
  transactions per run so a partial graph is never visible to readers.
- **Failure handling:** sync failures write an audit row and alert; the digest continues from
  Postgres; a stale projection is reported, not silently trusted.
- **Fixtures:** VCR-cassette-derived quote/article fixtures drive parity tests; no live source
  contact for graph tests.

## 5. Access, security and audit

- Internal-only, same rights envelope as the digest (DEC-02/DEC-10); external exposure would
  need a fresh rights review.
- Parameterised Cypher only — no string interpolation of quote text or URLs; quote text is
  data, never query structure.
- Redaction rules from [02](02-stack-and-architecture.md) §4.1 apply to graph logs and
  metrics; `license_reference` stays as audit evidence.
- Every read used by a digest records query, result IDs, graph schema version and `trace_id`
  in `audit_events`.

### 5a. Rights revocation and takedown (RSK-16)

If a source's `rights_status` changes to `blocked`, or a licence is declined, the projection
**must not** keep presenting its material: a sync step labelled the source node `revoked`, removes
it from fact-query result sets (while retaining evidence nodes for audit), and records the
takedown with `run_id`, `trace_id` and reason in `audit_events`. Because Neo4j is a derived
projection, the authoritative revocation always happens in Postgres/`sources.csv` first; the
graph mirrors it at the next sync. A stale graph never gates or produces a digest. Verify with
a fixture revocation test (CHK-14 proposed if the option is adopted).

### 5b. Backup and restore

The graph is rebuildable, so backups are convenience not compliance: export node/relationship
CSV plus schema constraints after each successful sync; store with the same retention as derived
JSONL. A restore drill recreates the graph from Postgres (authoritative) and compares node counts
and `content_hash` sets to the export. Never treat a Neo4j backup as the system of record.

## 6. Illustrative query (not an API contract)

```cypher
MATCH (q:Quote)-[:IN_SECTION]->(s:Section {section_id: $section_id})
MATCH (q)-[:SHOWN_IN]->(d:Digest {digest_date: $report_date})
WHERE q.is_obsolete = false AND q.claim_only = false
RETURN q.quote_id, q.quote_text, q.content_hash, s.section_id
ORDER BY q.quote_id;
```

`$section_id` and `$report_date` are bound parameters. Claim-only material is queried
separately and always labelled as perspective.

## 7. Validation if adopted (proposed CHK-14)

| Check | Assertion |
|---|---|
| Parity | counts of active `Quote`/`Article` nodes equal Postgres `verified` rows for the run |
| Provenance | every node/relationship has `run_id`, `content_hash` and version fields |
| Rights | no blocked/disabled source contributes to fact queries; `claim_only` preserved |
| Idempotency | two syncs with the same keys produce identical graph state |
| Freshness | last successful sync within tier cadence; stale projection reported |
| Performance | p95 of the six planned graph queries below an agreed threshold (set at prototype) |

These become a real CHK row in [08](08-traceability.md) and a Dagster asset check only when
the integration is adopted; they are **planned**, not active.

## 8. Rollout path and triggers

| Phase | Work | Exit criterion |
|---|---|---|
| 0 — trigger | Confirm a real graph query need or measured JSONL bottleneck; revisit DEC-09 and record the change in [08](08-traceability.md) | Decision recorded |
| 1 — prototype | Docker Neo4j, fixture-derived graph from VCR cassettes, one supersession query, Parity + Provenance checks | Prototype demo, no live data |
| 2 — shadow sync | Nightly projection from Postgres beside the normal digest; Freshness/Idempotency checks | 7 clean days, zero digest impact |
| 3 — read-only use | Answer one real analyst question from the graph; keep Postgres authoritative | Analyst confirms value |
| 4 — optional UI | Streamlit graph view; read-only, same caching rules | Dashboard check passes |

Costs to weigh at phase 0: Neo4j hosting/ops, an extra sync pipeline, dual-store debugging,
and licence review for any graph-derived redistribution. Until then the curated JSONL context
pack remains the LLM context artifact.
