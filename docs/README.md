# Media Quotes Collection System — Plan

Planning documents for the system described in
[`Test_News.md`](Test_News.md). Adopted improvements are built into 01–03; every
decision and risk is a register row in 04 with a stable ID; requirement → decision → task →
check mappings are recorded once, in 08. There are **no open questions** left — each was
closed by an explicit decision; the residual licence constraint is accepted, not untracked.

**Document status** (all are *integrated / reviewed*, owner **Balgove Innovations**, last
reviewed **2026-11-22** against the brief, the scaffold and the implemented code):

| Doc | Status | Last reviewed | Review trigger |
|---|---|---|---|
| 01–04, 06–18, root & data README | integrated / reviewed | 2026-11-22 | Brief change, decision change, or implementation drift |

> **Status: integrated, reviewed.** Reuters/BBC stay mandatory but `blocked` under the T0
> rights gate — see [04](04-risks-and-open-questions.md) DEC-01/DEC-14. The minimum
> deliverable is the brief's §3–§4, reachable through one command (`mq run`, `mq audit`,
> `mq logs`).
>
> **Notation:** "T0 gate" = rights gate (brief §1); task `T0` = first task in
> [03](03-work-breakdown.md). Daily MD path is canonical: `digests/YYYY-MM-DD.md`.
> Owner unless stated: **Balgove Innovations**. Former doc 05 (critique/deferred options)
> is retired; its provenance is in 06 and its decisions in 04.

| Doc | Contents |
|---|---|
| [01-requirements-analysis.md](01-requirements-analysis.md) | Requirements with stable IDs (`REQ-xx`, `OUT-xx`): source tiers + enums, functional requirements, minimum §3–§4, interpretation decisions (register refs), acceptance thresholds, two-mode definition of done |
| [02-stack-and-architecture.md](02-stack-and-architecture.md) | Stack + package extras (uv, Alembic), architecture, append-only data model, coverage data contract, retention/cost/registry-sync, canonical tool parameters (§4.1), `mq` command contract, not-adopted options (§6), **Python engineering standards (§7)** |
| [03-work-breakdown.md](03-work-breakdown.md) | T0–T10 tasks with owners/estimates, Mermaid sequencing + phase table; T8 evaluation and T10 test infrastructure continuous from T1, T9 one-command UX |
| [04-risks-and-open-questions.md](04-risks-and-open-questions.md) | **Active register only**: accepted licence residual and not-adopted options; engineering-risk mitigations are planned in [10](10-risk-mitigation-plan.md); solved items indexed in [08](08-traceability.md) §8 |
| [06-observations.md](06-observations.md) | Observations explaining the mandatory-sources constraint and the choice of Dagster; sources consulted |
| [07-sources.md](07-sources.md) | Descriptive source catalogue: per-tier role, current rights status, what enabling each source requires, DEC-10 licence-limit policy |
| [08-traceability.md](08-traceability.md) | REQ/OUT → DEC → task → check matrix, CHK registry, tracked code changes, **closed-items index (solved DEC/RSK, keeping IDs resolvable)**, change-recording convention |
| [09-neo4j-integration.md](09-neo4j-integration.md) | Planning overview for a possible Neo4j graph integration: graph model, sync from Postgres, rights/security, validation and rollout triggers — **not adopted** |
| [10-risk-mitigation-plan.md](10-risk-mitigation-plan.md) | How RSK-07/08/11 will be closed: evidence bundle + review SLA, backfill budget/cache guard, partitioning/retention; new checks CHK-15…17 |
| [11-repository-structure.md](11-repository-structure.md) | Canonical whole-monorepo tree: source, data, tests, docs, migrations, tooling and generated artifacts, with placement rules |
| [12-implementation-plan.md](12-implementation-plan.md) | Bridge from plan to code: phases A–F, vertical slices, exit checks and the cutover from file-based offline handlers to Postgres/Dagster |
| [14-command-reference.md](14-command-reference.md) | Every command in the repo: CLI flags, scripts, Make/PowerShell, Docker, Dagster, `.env`, recommended order |
| [15-source-topic-study.md](15-source-topic-study.md) | How each outlet organises the Russia–Ukraine war topic (hubs, tags, feeds, sitemaps, APIs) and the resulting per-source topic config recommendation |
| [16-topic-language-ai-judge-plan.md](16-topic-language-ai-judge-plan.md) | Consolidated plan for topic validation, language classification, opt-in translation and environment-controlled AI review |
| [17-connection-and-fetching-reference.md](17-connection-and-fetching-reference.md) | Operational reference for connecting sources, fetching articles/quotes, and every CLI/env switch that controls it |
| [18-connection-and-fetching-improvements.md](18-connection-and-fetching-improvements.md) | Prioritised improvement plan derived from 17: misleading flags, doc drift, live-fetch command, rights and policy tuning |

## Reading order

1. [06](06-observations.md) — why the design has its two defining constraints.
2. [01](01-requirements-analysis.md) — what must be delivered (minimum = §3–§4; thresholds §7).
3. [04](04-risks-and-open-questions.md) — the register: every decision, risk and accepted constraint.
4. [08](08-traceability.md) — how each requirement is decided, built and checked.
5. [02](02-stack-and-architecture.md) — how it is built.
6. [03](03-work-breakdown.md) — task order, owners, estimates and phases.
7. [07](07-sources.md) — per-source detail and the licence-limit policy.
8. [09](09-neo4j-integration.md) — optional graph integration, only if a graph need appears.
9. [10](10-risk-mitigation-plan.md) — how the engineering risks will be closed.
10. [11](11-repository-structure.md) — where every file in the monorepo belongs.
11. [12](12-implementation-plan.md) — how to cut over to live Postgres/Dagster implementation, phase by phase.
12. [14](14-command-reference.md) — exact commands and operational order (Dagster after local Postgres).
13. [15](15-source-topic-study.md) — how sources organise the war topic and what to configure per source.
14. [16](16-topic-language-ai-judge-plan.md) — topic gate, language/translation and AI-judge implementation order.
15. [17](17-connection-and-fetching-reference.md) — how sources connect, how fetching works and which flags control it.
16. [18](18-connection-and-fetching-improvements.md) — the smallest fixes for the gaps 17 identifies.

The implementation scaffold lives in [`../src`](../src); the registry and frozen schema in
[`../data`](../data); operator commands in the [root README](../README.md).

## Glossary (single source; root README links here)

- **T0 gate**: rights gate (brief §1); task `T0`: requirements/acceptance task in [03](03-work-breakdown.md).
- **run_id / trace_id**: one orchestration run / one OTel trace per article; both in logs.
- **content_hash**: SHA-256 of raw article bytes (`raw_blobs`) or canonical quote tuple `(source_id, canonical_url, quote_text_original, char_start, raw_blob_hash)` (`quotes`); dedupe, cache key and tamper check.
- **gold_set_version**: pinned labelled set used for eval; never tuned on the test split.
- **claim_only**: quote shown as attributed perspective (state-controlled outlets), never as fact.
- **rights_status / access_mode**: T0 approval state / approved retrieval route (enums in [01](01-requirements-analysis.md) §2).
- **DEC / RSK / REQ / OUT / CHK**: register IDs in [04](04-risks-and-open-questions.md) and [08](08-traceability.md).

## Docs QA checklist (CHK-08 — `scripts/docs_qa.py`, also wrapped by `tests/contract/test_docs_qa.py` in CI)

- [ ] All relative links resolve (no links to retired doc 05).
- [ ] One canonical MD path everywhere: `digests/YYYY-MM-DD.md`; no `data/reports/` as output.
- [ ] `mq` command names and flags match [02](02-stack-and-architecture.md) §5 and the root README.
- [ ] DEC/RSK IDs referenced anywhere are defined in [04](04-risks-and-open-questions.md); REQ/OUT IDs are defined in [01](01-requirements-analysis.md) and covered by the [08](08-traceability.md) matrix.
- [ ] Thresholds match [01](01-requirements-analysis.md) §7 across 01/02/03/08.
- [ ] Engineering standards in [02](02-stack-and-architecture.md) §7 are enforced by CHK-10…13 (`make check`, uv lock, mypy --strict, 85% coverage, secrets redaction).
- [ ] Every task ID referenced in 04/08 exists in the [03](03-work-breakdown.md) table (T0–T10).
- [ ] Enumerations match [01](01-requirements-analysis.md) §2 / `data/quotes.schema.json`.
- [ ] "T0 gate" vs task `T0` used consistently (notation note in 01/03/04/06/07/08).
- [ ] Evals dates read "went read-only 2026-10-31; shuts down 2026-11-30" (canonical: 02 §1 and 04 DEC-06).
- [ ] `data/README.md` and the schema call `quotes.jsonl` a **derived export**, not the store.
- [ ] No open-question IDs or sections, no deferred section: every item is decided in 04.
- [ ] Register rows carry a status from `accepted / not adopted`; no solved rows in 04; CHK-01…19 are all defined; solved IDs remain indexed in 08 §8.
- [ ] `sources.csv` and `quotes.schema.json` unchanged by documentation edits.
- [ ] Tables render (header separator present) and code spans are not split across lines.
