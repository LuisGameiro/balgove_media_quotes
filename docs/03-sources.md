# 03 — Sources and rights

## Registry authority

`data/sources/*.json` holds the authoritative source definitions. `data/sources.csv` and
`data/source_topic_routes.csv` are generated mirrors; `mq sources --action sync-csv` refreshes them
and `mq bootstrap` syncs the configs into Postgres.

The registry has **28 sources**. All 28 record `rights_status=license_held` with a licence reference
and are enabled for validation. The grant scope is operator-attested; this checkout cannot verify
the legal grants. Reuters and BBC still mark translation rights as unconfirmed. All 29 route rows
are approved, which is separate from runtime health. Counts and route health are a snapshot as of
2026-11-24; re-run the proof runner before relying on them.

| Tier | Count | Role |
| --- | --- | --- |
| 1 | 2 | Mandatory Reuters and BBC baseline |
| 2 | 10 | Additional authoritative outlets and discovery APIs |
| 3 | 11 | National and regional outlets |
| 4 | 5 | State-controlled perspective sources, claim-only |

Tier 1 declares a 6-hour cadence; tiers 2–4 declare 24 hours. `cadence_hours` is metadata, not a
scheduler setting.

## Mandatory sources

- **Reuters** — mandatory wire baseline. Enabled for validation, but its public sitemap returned
  HTTP 401 on 2026-11-24. It remains a disclosed coverage gap until a licensed endpoint is supplied.
- **BBC News** — mandatory public broadcaster baseline. RSS is used for discovery and the fetched
  page for verification. The feed was reachable on the same date.

A full `live` or `archive` run fails closed when every expected mandatory source contributes zero
quotes; missing coverage counts as zero. A tier or source filter that excludes the mandatory
baseline is a scoped partial run, not a full run.

## T0 rights gate

T0 is an implementation gate, not a clause in the brief. A source is usable only when:

- `enabled=true`;
- `rights_status` is `license_held` or `api_permitted`;
- `license_reference` is non-empty;
- `access_mode` is `licensed_database`, `official_api` or `public_rss_permitted`;
- `discovery_method` is enumerable and is not `web`.

The schema also requires state-controlled and tier-4 sources to be `claim_only`. Quote publication
then depends on verification and rights limits. A blocked fetch raises a configuration/T0 error; a
source with no articles produces a coverage gap.

## Routes and live access

The proof runner tested official public feeds and up to five articles per source on 2026-11-24. It
found 11 live-verified sources, 10 live-partial sources and seven fixture fallbacks; 19 sources
yielded quotes from live or fixture content. Live-verified sources were BBC, Guardian, DW, Al
Jazeera, Kyiv Independent, Suspilne, Interfax-Ukraine, Anadolu, The Hindu, Yonhap and Global Times.
A Tier-1 canary collected two BBC `live_feed` quotes while Reuters returned HTTP 401. This is
point-in-time evidence, not proof of completeness.

Known blockers include Reuters and AP HTTP 401, Bloomberg with no public feed, FT/Economist/NYT
paywalls or 403, Kyodo 404, RT timeout, Xinhua's stale HTTP-only feed, GDELT being discovery-only,
and Guardian API lacking a key. Some sources yield no verifiable quotes in sampled articles. The
proof report is written under `var/output/proof/`.

Live discovery in `mq run --live` and `mq backfill ... --execute --live` prefers each usable source's
official feed or sitemap. Bing News is an opt-in discovery fallback; live mode never falls back to
fixtures. The validator probes registered feed URLs and classifies XML, JSON and HTML separately.
Redirects are rejected unless safe following is enabled.
Archive and live fetches validate DNS and pin the connection to the validated address while keeping
the original Host and TLS SNI.

Translation is a separate right. Reuters and BBC have `translation_rights_confirmed=false`, so
non-English output must preserve the original and use only the pinned translation boundary after
route approval.

## Claim-only material

The five tier-4 outlets are claim-only perspective sources. Anadolu is tier 3 and also
state-controlled, so it is claim-only too. Their material may be shown only as attributed
perspective, never as an established fact. Source type, country, language and claim-only flags travel
with every quote.

## Admission and onboarding

`mq sources --action add` creates a disabled, pending-review config. `add --probe --live` and
`probe --source-id <id> --live` run bounded admission checks for robots/terms and route reachability;
they grant no rights and enable nothing. `sources --action backfill --source-id <id>` needs a usable
source and a positive `--max-cost`.

Human review still covers rights, access mode, canonical URLs, publication dates, bylines, archive
depth to 2026-10-01 and first-hand reporting. Legacy approved configs default to
`original_reporting_flag=first-hand`; newly scaffolded sources start as `unknown` until signed off.

## Offline replay

Every source has local fixtures. Offline runs replay them deterministically. Fixture replay is not a
rights workaround and is not evidence of live-source completeness. `mq sources` prints the current
gate state; `mq doctor` checks JSON, CSV and schema agreement.
