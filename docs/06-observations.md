# 06 — Observations

Two observations that shape the design. They explain the constraints and why the current
design is what it is; implementable detail lives in [01–04](README.md). Every decision and
risk, including the accepted licence constraint, is in [04](04-risks-and-open-questions.md).

**Status:** integrated / reviewed · **Owner:** Balgove Innovations · **Last reviewed:**
2026-11-20 (rights facts below checked on that date; re-check at licence signing).

**Notation:** "T0 gate" = the rights gate in brief §1; task `T0` = the first task in
[03](03-work-breakdown.md).

## 1. The mandatory sources issue

The brief names Reuters and BBC as mandatory, and keeping them mandatory is the point of the
exercise (decision DEC-01 in [04](04-risks-and-open-questions.md)). Their published access
rules make the original "crawl the public pages" approach unusable:

- Reuters' `robots.txt` sets `User-agent: * / Disallow: /` (apart from `/plus/`) and requires
  prior written consent for automated collection, in any format.
- BBC's terms and `robots.txt` comments forbid scraping/systematic extraction, dataset
  creation, LLM training, RAG, AI grounding and business use without permission, and block
  common AI agents.
- An RSS feed is **not** an exemption: fetching a feed is still automated collection, and
  feeds carry headlines/links/short summaries rather than a backfillable full text. They
  cannot satisfy "all material quotes" or the historical backfill.

The design implication is a gate, not a substitution. A source is enabled only when
`rights_status` is `license_held` or `api_permitted` and a `license_reference` is recorded.
The realistic routes for mandatory content are Factiva, Nexis, LSEG (Reuters) and Reuters
Connect; GDELT/Guardian/NYT APIs are discovery or fallback sources, never replacements for
Reuters/BBC. Each of those routes must separately grant machine retrieval, storage,
LLM/RAG grounding and verbatim republication — receiving a feed does not grant all four.

Because the outlets remain mandatory while blocked, the system keeps tier-1 adapters and
reports them as explicit coverage gaps instead of silently dropping them. Development runs
against VCR fixtures and licensed-route adapters; `mq doctor` and `mq audit` surface the
rights state. Quality requirement 2 ("all material articles from mandatory sources") is
measurable only in licensed/full mode; [01](01-requirements-analysis.md) §8 defines both
modes. This is an engineering risk assessment, not legal advice — counsel must review the
licences and sign off the source mix.

## 2. The use of Dagster

The pipeline is a daily, partition-oriented, auditable workflow with arbitrary historical
backfills, and Dagster matches those shapes directly:

- **Daily partitions** fit the rolling 14-day digest and the 2026-10-01 → current-date
  backfill. Dagster documents one-run-per-partition, `multi_run` and `single_run` backfill
  strategies with explicit fault-isolation trade-offs.
- **Asset `code_version`** lets a change to the prompt, schema or extractor invalidate only
  the downstream assets it affects — essential when every decision is version-pinned and
  replayable.
- **Asset checks / data contracts** carry the coverage contract, schema-valid and verifier
  gates, and freshness monitoring at tier cadence (6h for Tier 1, 24h for Tiers 2–3).
- **Dynamic fan-out** (`DynamicOut`/`DynamicCollect` with a fan-in barrier) maps to
  new-source onboarding: each candidate source can be checked and dry-run independently
  before the approval gate.

Prefect is viable but the documentation reviewed did not show a first-class
partition/backfill abstraction; Airflow would require dynamic-DAG generation with stable
ordering and forbids metadata-DB reads in top-level code — heavy for a pipeline of this
size. The choice is reflected in the stack and foundation task:
[02](02-stack-and-architecture.md) §1 and [03](03-work-breakdown.md) T1/T8.

## Sources consulted

Reuters and BBC `robots.txt` (fetched directly); Dagster docs (partition backfill strategies,
dynamic fanout, assets); OpenAI Structured Outputs, prompt caching, Batch API and evaluation
best practices; Anthropic Structured Outputs; PostgreSQL JSON docs; trafilatura Python usage;
jsonlines.org; OpenTelemetry Python instrumentation; pytest, VCR.py, syrupy and Streamlit
caching docs; pgvector README; PydanticAI and msgspec docs; GDELT terms; Factiva/Nexis/LSEG/
Reuters Connect official pages. Items marked unavailable in the research (e.g. Reuters legal
pages behind a bot check, BBC full Terms body, Guardian/NYT subpages) were not relied upon
beyond what was actually retrieved.
