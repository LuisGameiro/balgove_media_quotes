# 07 — Sources

Descriptive catalogue of every source in the registry. The authoritative machine state is
[`data/sources.csv`](../data/sources.csv) (mirrored into Postgres at bootstrap; see
[01](01-requirements-analysis.md) §6 #14); this document explains *why each row is what
it is* and *what enabling it requires*. It does not change the registry or the schema.

**Status:** integrated / reviewed · **Owner:** Balgove Innovations · **Last verified against
the JSON source configs:** 2026-11-22. The registry contains **28 source rows**: 10 licensed
sources are `enabled=true`, 16 are pending counsel review, and 2 (Reuters/BBC) are `blocked`
(re-verify at every enablement, T2/T7).

> **Notation:** "T0 gate" = the rights gate in brief §1; task `T0` = the first task in
> [03](03-work-breakdown.md). All outlets below are subject to the T0 gate — a licence to
> *receive* content never automatically grants LLM/RAG grounding or verbatim republication.
> The enforceable licence-limit policy is DEC-10 in
> [04](04-risks-and-open-questions.md): internal-only digest, **≤ 30 words per quote**,
> attribution `Outlet — Named person (role), Article, YYYY-MM-DD, URL`, territory =
> internal worldwide. Candidate extraction (T3/T4) selects a self-contained, verbatim
> contiguous span of ≤ 30 words (exact DOM substring, never middle-truncated with ellipses);
> the renderer (T6, CHK-19) enforces this limit as a defence-in-depth publication gate. If
> a signed licence is stricter, the stricter values win and DEC-10 is updated.

## Tier 1 — mandatory (monitor every 6 hours once enabled)

### Reuters — `blocked`, `scraped_prohibited`

- Role: mandatory wire-service baseline; the exercise's named source (DEC-01).
- Constraint: `robots.txt` sets `User-agent: * / Disallow: /` (except `/plus/`) and requires
  prior written consent for automated collection in any format — including feeds.
- Enabling requires: a written licence covering machine retrieval, storage, LLM/RAG
  grounding, verbatim republication and the 2026-10-01 backfill. Realistic routes: Factiva
  (+ AI Research Solutions), LexisNexis Nexis Data+ / Protégé, LSEG Machine Readable News,
  Reuters Connect. Each route must be checked for those five grants individually.
- Until then: never contacted; reported as a coverage gap; exercised via VCR fixtures.

### BBC News — `blocked`, `scraped_prohibited`

- Role: mandatory publicly funded baseline (DEC-01).
- Constraint: terms and `robots.txt` comments forbid scraping/systematic extraction, dataset
  creation, LLM training, RAG, AI grounding and business use; the RSS feed host permitting
  a fetch does **not** waive those terms, and a feed cannot supply full text or a
  backfillable archive.
- Enabling requires: written permission with the same five grants as Reuters; the RSS feed
  may then be used for discovery only.

## Discovery / fallback APIs (never substitutes for Tier 1 — DEC-01)

| Source | Status | Role and caveat |
|---|---|---|
| GDELT | `pending_counsel_review`, `official_api` | Discovery metadata + links only; the licence covers GDELT outputs, not publisher text. Quotation graph useful for discovery of Tier 3 coverage. |
| Guardian Open Platform | `pending_counsel_review`, `official_api` | Official API; review developer terms for storage, RAG and verbatim republication before enabling. Preferred over scraping `theguardian.com`. |
| NYT Article Search / Archive APIs | `pending_counsel_review`, `official_api` | Metadata and abstracts by default; full-text rights are separate. |

## Tier 2 — additional authoritative outlets (daily)

All Tier 2 rows are now operator-attested as written-rights approved on 2026-11-20. The registry
uses the tier-level token `TIER2-WRITTEN-RIGHTS-ATTESTED-2026-11-20`; the owner withheld
source-specific contract references from chat and the repository. The T0 gate is therefore
approved, but **no live adapter exists yet**, so runs report `no_coverage` for these outlets
until licensed retrieval is implemented. `archive_start_date` remains 2026-10-01; each source
still needs a stable, backfillable archive before production use.

| Source | Notes |
|---|---|
| AP | Wire original; verify archive/API route at onboarding. |
| Bloomberg | Paywalled; rights review required. |
| Financial Times | Paywalled; rights review required. |
| The Economist | Paywalled, low article volume; rights review required. |
| The Guardian (site) | Use the official API instead once terms are cleared. |
| Deutsche Welle | Publicly funded; rights review required. |
| Le Monde | French; store original plus `quote_text_en` translation (never replace the original). |

## Tier 3 — non-Western original reporting (daily)

Included to avoid an all-Western, wire-copy-only picture. Each is `pending_counsel_review`,
disabled, until the T0 gate and §1.1 admission checks pass — including the **per-article
first-hand check against wire copy** (`original_reporting_flag` must be `first-hand`,
with stored evidence).

| Source | Country | Type | Notes |
|---|---|---|---|
| Al Jazeera | Qatar | publicly funded | Check bylines per article. |
| Kyiv Independent | Ukraine | independent | Original on-the-ground reporting. |
| Ukrainska Pravda | Ukraine | independent | Ukrainian; translation field required. |
| Suspilne | Ukraine | publicly funded | Ukrainian; translation field required. |
| Interfax-Ukraine | Ukraine | independent | English edition; verify agency terms. |
| Anadolu Agency | Turkey | **state-controlled** | `claim_only=true` even when enabled: shown as attributed perspective, never as established fact. |
| The Hindu | India | independent | Partial paywall. |
| Indian Express | India | independent | Partial paywall. |
| South China Morning Post | Hong Kong | independent | Paywalled. |
| Kyodo News | Japan | independent | Confirm archive depth and access (`paywalled=unknown`). |
| Yonhap | South Korea | independent | Rights review required. |

## Tier 4 — excluded by default

| Source | Status | Policy |
|---|---|---|
| RT, TASS, RIA Novosti, Xinhua, Global Times | disabled, `claim_only=true`, `cadence_hours=0` | State-controlled. Per DEC-13 they stay disabled throughout the initial build — no perspective opt-in; never let their claims establish or overwrite a fact. RIA Novosti is Russian-language; translation field applies if ever enabled. |

## Registry mapping (machine view)

Descriptive names above map to these `source_id` / rights / access-mode values in
`data/sources/*.json` (mirrored to `data/sources.csv`). As verified 2026-11-22: 10 sources are
`license_held` and `enabled=true`, 16 are `pending_counsel_review` and disabled, and Tier 1
Reuters/BBC are `blocked`:

| Tier | `source_id` → outlet | `access_mode` | Notes |
|---|---|---|---|
| 1 | `reuters` → Reuters; `bbc` → BBC News | `scraped_prohibited` | `blocked`; BBC has a `feed_url` for discovery only |
| 2 | `ap`, `bloomberg`, `ft`, `economist`, `guardian`, `dw`, `lemonde` | `pending_review` | `web` discovery; `lemonde` requires translation |
| 2 (APIs) | `gdelt`, `guardian_api`, `nyt_api` | `official_api` | `gdelt` discovery; `nyt_api` metadata/abstracts by default |
| 3 | `aljazeera`, `kyivindependent`, `ukrainskapravda`, `suspilne`, `interfaxukraine`, `anadolu`, `thehindu`, `indianexpress`, `scmp`, `kyodo`, `yonhap` | `pending_review` | `anadolu` is `claim_only=true`; UA-language rows require translation |
| 4 | `rt`, `tass`, `ria`, `xinhua`, `globaltimes` | `pending_review` | disabled, `claim_only=true`, `cadence_hours=0` |

## Enabling checklist (any tier)

To avoid a complete live-data deadlock while Tier 1 enterprise syndication licences
(Reuters, BBC) are negotiated, counsel review is sequenced in two operational tracks:
- **Track A (Fast-Track API & Open Sources — Target: Phase 1):** `guardian_api`,
  `gdelt` (discovery only), `suspilne`, and `kyivindependent`. Standard developer API
  terms and public broadcaster rights are reviewed first so live ingestion runs in
  degraded mode with real Tier 2/3 coverage.
- **Track B (Commercial Syndication Licences — Target: Upon Contract):** `reuters`,
  `bbc`, `ap`, `ft`, `bloomberg`, `economist`.

Enabling checklist for any source:

1. Rights: `rights_status` → `license_held`/`api_permitted` with `license_reference`; record
   licence limits (max quote length, attribution format, territory).
2. Admission (brief §1.1): first-hand sample with stored evidence; stable canonical URL,
   date, byline; archive depth to 2026-10-01; named attribution; type/country/language
   recorded.
3. Access: approved `access_mode` only; robots/ToS checked for the exact path.
4. Set `enabled=true`, backfill from `archive_start_date`, monitor at tier cadence.
5. Update this document and the register ([04](04-risks-and-open-questions.md)) when status
   changes; record the change in [08-traceability.md](08-traceability.md).
