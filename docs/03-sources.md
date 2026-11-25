# 03 — Sources and rights

## Registry authority and current state

The authoritative source definitions are `data/sources/*.json`. `data/sources.csv` and
`data/source_topic_routes.csv` are generated tabular mirrors; bootstrap validates the configs and
syncs them into Postgres.

The registry contains **28 sources**: all 28 rows record `rights_status=license_held` with a
non-empty `license_reference`, and all 28 are enabled for validation. 11 are `tier_block=true`;
17 are not blocked. The recorded licence scope is operator-attested. The checkout cannot
independently prove the legal grants. All 29 route rows are approved, and all 29 have
`translation_rights_confirmed=false`; runtime health is reported separately.

| Tier | Count | Enabled | Role |
|---|---|---|---|
| 1 | 2 | 2 | Mandatory Reuters and BBC baseline |
| 2 | 10 | 10 | Additional authoritative outlets and discovery APIs |
| 3 | 11 | 11 | National and regional outlets |
| 4 | 5 | 5 | State-controlled perspective sources, enabled as claim-only |

## Tiers and declared cadence

| Tier | Declared cadence | Sources |
|---|---|---|
| 1 | 6 hours | `reuters`, `bbc` |
| 2 | 24 hours | `ap`, `bloomberg`, `ft`, `economist`, `guardian`, `dw`, `lemonde`, `gdelt`, `guardian_api`, `nyt_api` |
| 3 | 24 hours | `aljazeera`, `kyivindependent`, `ukrainskapravda`, `suspilne`, `interfaxukraine`, `anadolu`, `thehindu`, `indianexpress`, `scmp`, `kyodo`, `yonhap` |
| 4 | 24 hours | `rt`, `tass`, `ria`, `xinhua`, `globaltimes` |

`cadence_hours` documents the intended maximum interval. It does not start collection. Dagster
schedules are defined but stopped; both pass `source_mode=live` explicitly.

## Mandatory sources

- **Reuters** — mandatory wire-service baseline. It is enabled for all-source validation, but
  its public sitemap and direct article pages returned HTTP 401 on 2026-11-24. Reuters Connect
  allows free browsing, but text content requires a subscription. The source remains a visible
  `tier_block` coverage gap until licensed text/API access or authorised samples are supplied.
- **BBC News** — mandatory public broadcaster baseline. RSS is used for discovery; article text
  is verified from the fetched page. The feed was reachable during the same validation.

Both remain mandatory. Reuters is reported as a live-route coverage gap and is never silently
replaced by fallback outlets.

## T0 rights gate

T0 is the implementation’s fail-closed rights gate, not a clause in the original assignment. A
source is usable only when all of the following hold:

- `enabled=true`.
- `rights_status` is `license_held` or `api_permitted`.
- `license_reference` is non-empty.
- `access_mode` is `licensed_database`, `official_api` or `public_rss_permitted`.
- `discovery_method` is enumerable and is not `web`.

The database adds three policy constraints: state-controlled sources must be `claim_only`,
tier-4 sources must also be `claim_only`, and `tier_block=true` requires a non-empty
`blocker_reason`. Tier-4 rows may be enabled with a positive cadence.
Quote publication is then gated by verification and rights limits. A blocked fetch exits with a
configuration/T0 error; a source with no articles produces a disclosed coverage gap.

## Routes and live access

The proof runner uses the shared collection service, configures official public feeds/sitemaps
where available, uses Bing News discovery as a fallback and enforces `tier_block` before any
network request. The authoritative 2026-11-24 proof run covers 27 sources: 15 collected data and
verified 76 live quotes (`live_verified`), one was `accessible_no_quotes`, 11 were blocked and none
were fixture-only; Anadolu is absent from the report after its live-route failure. Counts change
between runs, so the generated report at `var/output/proof/source-proof-YYYY-MM-DD.json` and `.md`
is authoritative. Fixture quotes are reported separately and do not count as acquired source data.

Current blockers are Reuters HTTP 401; Bloomberg, FT and SCMP partial bodies; Economist and NYT
HTTP 403; RT's operator-reported sanctions restriction and network timeout; GDELT/Guardian API
being discovery-only; and Kyodo/RIA being blocked because sampled articles did not yield
verified quotes. In the latest run, Suspilne was accessible without verified quotes and Anadolu
had a live-route failure. The proof report is written under `var/output/proof/`.

## Can every non-blocked source provide live data?

- **17 sources** have `tier_block=false` and are eligible for live collection.
- **15** produced verified quotes in the 2026-11-24 run: BBC, AP, Guardian, DW, Le Monde, Al
  Jazeera, Kyiv Independent, Ukrainska Pravda, Interfax-Ukraine, The Hindu, Indian Express,
  Yonhap, TASS, Xinhua and Global Times.
- **Suspilne** returned live pages but no verified quotes in the sample.
- **Anadolu** encountered a live-route failure and is absent from the authoritative proof report;
  no fixture-only fallback is counted.

So blocked sources are never contacted, while non-blocked sources can still have transient route
or quote-yield failures.

The validator probes the registered feed URL instead of an approved homepage, classifies XML,
JSON and HTML separately, follows same-host redirects in proof mode and permits cross-host
redirects only for explicitly configured partners such as `eurointegration.com.ua`. It only makes
live network probes with `--live-routes`; otherwise routes are reported as `not_probed` (blocked
ones as `blocked_not_probed`) and no blobs are written. Route approval remains distinct from
runtime health. Translation remains a separate right: all 29 routes have
`translation_rights_confirmed=false`, so translation is unavailable for every source. Non-English
output must preserve the original quote and use the optional pinned translation boundary only
after a route is explicitly approved.

## Availability and `tier_block`

Editorial tiers remain 1–4. Operational availability is stored separately:

- `availability_status`: `live`, `partial_body`, `accessible_no_quotes`, `blocked`,
  `discovery_only` or `fixture_only`.
- `tier_block`: true when a source cannot currently provide compliant full text.
- `blocker_reason`: required whenever `tier_block=true`.

The table records configured operational status. The generated proof report records the
observed status for each run, which can differ when a live route fails transiently.

| Source | Configured availability | `tier_block` | Reason |
|---|---|---:|---|
| Reuters | `blocked` | Yes | Public sitemap and direct pages return 401; licensed text/API or authorised samples required |
| Bloomberg | `partial_body` | Yes | Public pages expose only a short snippet |
| FT | `partial_body` | Yes | Public pages expose only a short snippet |
| SCMP | `partial_body` | Yes | Public pages expose only a short snippet |
| Economist | `blocked` | Yes | Direct pages return 403; licensed full text required |
| NYT | `blocked` | Yes | Metadata-only public API and 403 article pages; licensed full text required |
| RT | `blocked` | Yes | Operator reports sanctions restriction; probes also time out |
| Kyodo | `blocked` | Yes | Live pages are reachable, but sampled articles did not yield short attributable quotes |
| RIA Novosti | `blocked` | Yes | Archive feed and pages are reachable, but sampled wire briefs had no verified quotes |
| GDELT | `discovery_only` | Yes | Discovery-only metadata and URLs; no publisher article bodies or quotes |
| Guardian API | `discovery_only` | Yes | API route is not configured for quote acquisition; Guardian RSS supplies live proof |

`tier_block` does not erase the source or its rights record. It makes the failure visible and
prevents fixture evidence from being counted as collected data.

## State-controlled and claim-only material

The five tier-4 outlets are enabled as `claim_only` perspective sources. Anadolu is tier 3 and
also state-controlled, so it is likewise `claim_only`: their output may be shown only as
attributed perspective and never used to establish or overwrite a fact. Source type, country,
language and claim-only flags travel with every quote.

## Admission and onboarding

A new source is admitted only after manual review of rights, access mode, robots/terms, stable
canonical URLs, publication date, byline, archive depth to 2026-10-01, named attribution and
first-hand reporting. `mq sources --action add` creates a disabled, pending-review JSON config;
it does not perform those checks or start a backfill. Admission probing is not wired into the
add command.

## Offline replay

Every source has local fixtures. Offline runs replay them deterministically. Fixture replay is
not a rights workaround and is not evidence of live-source completeness.

`mq sources` prints the current gate state; `mq validate` checks JSON, CSV and schema agreement.
