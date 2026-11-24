# 03 — Sources and rights

## Registry authority and current state

The authoritative source definitions are `data/sources/*.json`. `data/sources.csv` and
`data/source_topic_routes.csv` are generated tabular mirrors; bootstrap validates the configs and
syncs them into Postgres.

The registry contains **28 sources**: all 28 rows record `rights_status=license_held` with a
non-empty `license_reference`, and all 28 are enabled for validation. The recorded licence scope
is operator-attested. The checkout cannot independently prove the legal grants, and Reuters/BBC
route records still mark translation rights as unconfirmed. All 29 route rows are approved;
runtime health is reported separately.

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
schedules are defined but stopped, and the asset defaults to fixture replay.

## Mandatory sources

- **Reuters** — mandatory wire-service baseline. It is enabled for all-source validation, but
  its public sitemap returned HTTP 401 on 2026-11-24. The route remains approved for validation
  and is reported as a runtime coverage gap until a working licensed endpoint is supplied.
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

The database adds two policy constraints: state-controlled sources must be `claim_only`, and
tier-4 sources must also be `claim_only`. Tier-4 rows may be enabled with a positive cadence.
Quote publication is then gated by verification and rights limits. A blocked fetch exits with a
configuration/T0 error; a source with no articles produces a disclosed coverage gap.

## Routes and live access

The proof runner configured official public feeds/sitemaps where available and tested up to five
articles per source on 2026-11-24. It produced 11 `live_verified` sources, 10 `live_partial`
sources and seven fixture fallbacks; 19 sources yielded verified quotes from live or fixture
content. Live-verified sources were BBC, Guardian, DW, Al Jazeera, Kyiv Independent, Suspilne,
Interfax-Ukraine, Anadolu, The Hindu, Yonhap and Global Times.

Current blockers include Reuters and AP HTTP 401, Bloomberg having no public feed, FT/Economist/
NYT paywall or 403 responses, Kyodo 404, RT timeout, Xinhua's stale HTTP-only feed, GDELT being
discovery-only, Guardian API lacking a key, and several sources whose sampled articles contained
no verifiable quotes. The proof report is written under `var/output/proof/`.

The validator probes the registered feed URL instead of an approved homepage, classifies XML,
JSON and HTML separately, follows same-host redirects in proof mode, rejects cross-host redirects
and treats route approval as distinct from runtime health. Translation remains a separate right:
Reuters and BBC have `translation_rights_confirmed=false`; non-English output must preserve the
original quote and use the optional pinned translation boundary only after route approval.

## State-controlled and claim-only material

The five tier-4 outlets are enabled as `claim_only` perspective sources. Anadolu is tier 3 and
also state-controlled, so it is likewise `claim_only`: their output may be shown only as
attributed perspective and never used to establish or overwrite a fact. Source type, country,
language and claim-only flags travel with every quote.

## Admission and onboarding

A new source is admitted only after manual review of rights, access mode, robots/terms, stable
canonical URLs, publication date, byline, archive depth to 2026-10-01, named attribution and
first-hand reporting. `mq sources --action add` creates a disabled, pending-review JSON config;
it does not perform those checks or start a backfill. Automated admission probing remains work
in [06-whats-left.md](06-whats-left.md).

## Offline replay

Every source has local fixtures. Offline runs replay them deterministically. Fixture replay is
not a rights workaround and is not evidence of live-source completeness.

`mq sources` prints the current gate state; `mq validate` checks JSON, CSV and schema agreement.
