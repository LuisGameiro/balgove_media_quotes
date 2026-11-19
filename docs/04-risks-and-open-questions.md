# 04 — Decision and risk register (active items)

This register holds only items that are still live: **accepted** risks (mitigated, with a
residual) and **not adopted** options (with a revisit trigger). Solved decisions and risks
are not repeated here — their IDs, one-line decisions and implementation locations are in
[08](08-traceability.md) §8 \"Closed items\", so cross-references across the plan remain valid.

- **accepted** — mitigated with a residual (including conditions engineering cannot remove);
  mitigations are fixed and the revisit trigger tells us when to revisit.
- **not adopted** — considered and deliberately excluded for the initial build; has a
  documented revisit trigger and, where applicable, a planning overview.

> Status describes the **design decision**, not runtime implementation. The scaffold is T1;
> proof arrives when tasks run the checks in [08](08-traceability.md).

Changes are recorded here and in [08](08-traceability.md), not scattered as prose in
[01–03](README.md). Per-source detail is in [07](07-sources.md); reasoning for the
mandatory-source constraint is in [06](06-observations.md) §1. Owner unless stated otherwise:
**Balgove Innovations**.

**Notation:** \"T0 gate\" = the rights gate in brief §1; task `T0` = the first task in
[03](03-work-breakdown.md). **Filename note:** the file keeps its historical name
`04-risks-and-open-questions.md`; the canonical title is \"Decision and risk register (active
items)\" and there are no open questions left.

## 1. Decisions (DEC) — active

| ID | Decision | Status | Implemented in | Owner | Revisit trigger |
|---|---|---|---|---|---|
| DEC-09 | pgvector and optional tooling (msgspec, LangGraph) are **not adopted** for the initial build; the curated JSONL context pack is the LLM context artifact. A **Neo4j graph integration is planned as an option** in [09](09-neo4j-integration.md), but is not adopted. | not adopted | [02](02-stack-and-architecture.md) §6; [09](09-neo4j-integration.md) | Balgove Innovations | Real RAG/graph queries, a measured JSONL/HTTP bottleneck, or the phase-0 trigger in [09](09-neo4j-integration.md) §8 |
| DEC-14 | **Residual licence risk acceptance**: the mandatory-source rights constraint cannot be removed by engineering. Mitigations (fixtures, licensed routes, coverage gaps, DEC-10 limits) are fixed; normal work continues while the licence is pending. | accepted | [06](06-observations.md) §1; [07](07-sources.md); DEC-10 in [08](08-traceability.md) §8 | Balgove Innovations + counsel | Licence decision (signed or declined) |

## 2. Risks (RSK) — active

Priority = High/Med/Low from Sev × Likelihood (High×High or High×Certain = High).

| ID | Risk | Sev | Likelihood | Priority | Mitigation (DEC / CHECK) | Status | Owner |
|---|---|---|---|---|---|---|
| RSK-01 | Mandatory sources legally blocked → no live tier-1 data | High | High | High | Fixtures + licensed adapters; CHK-02 gap report; blocks full mode | accepted | Balgove Innovations + counsel |
| RSK-02 | Licence terms (quote length, attribution, territory) not yet known | High | Certain | High | Licence-limit policy (DEC-10 in [08](08-traceability.md) §8) enforced by renderer; `07` records signed limits; CHK-02/CHK-04 | accepted | Balgove Innovations + counsel |
| RSK-13 | Prompt injection via article/feed text reaching the LLM | High | Medium | High | Untrusted source text never enters system prompts; delimit + strict structured outputs; deterministic verifier still gates publication; CHK-18 injection fixtures | accepted | Balgove Innovations |
| RSK-14 | Credential/API-key leak into logs, traces or VCR cassettes | High | Medium | High | env/.env only; structlog redaction (CHK-13); CI secret scan; filtered cassettes (CHK-09, CHK-19) | accepted | Balgove Innovations |
| RSK-15 | Licence-limit breach (quote > 30 words, wrong attribution, wrong territory) | High | Medium | High | DEC-10 enforced in renderer; negative tests for over-length/missing attribution with CHK-19 | accepted | Balgove Innovations + counsel |
| RSK-16 | Graph rights revocation not reflected if Neo4j option adopted | Medium | Low | Med | Takedown path in [09](09-neo4j-integration.md) §5a; derived projection never gates publishing; CHK-14 (proposed, only if adopted) | accepted | Balgove Innovations |

## 3. Rights blocker — documented once

The engineering-addressable risks (RSK-07/08/11) have a concrete mitigation plan in
[10-risk-mitigation-plan.md](10-risk-mitigation-plan.md) with unit-tested policy controls in
`src/media_quotes/risk_controls.py` and checks CHK-15…17; their design decisions (DEC-17/18/19)
and policy closures are indexed in [08](08-traceability.md) §8 while integration exit criteria
are enforced in T1–T10.

The mandatory-source rights blocker is described in [06-observations.md](06-observations.md) §1
(the constraint) and [07-sources.md](07-sources.md) (per-source status and enabling
requirements). Its residual is accepted in DEC-14; RSK-01/RSK-02 track the mitigation.
The licence-limit policy and sign-off owner are closed items in [08](08-traceability.md) §8.
