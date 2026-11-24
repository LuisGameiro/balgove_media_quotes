# 03 — Sources and rights

The registry has 28 sources. The machine state is `data/sources.csv`, mirrored into
`data/sources/*.json` and Postgres at bootstrap. All 28 are licensed for machine retrieval,
storage, LLM grounding, verbatim republication and the backfill from 2026-10-01. 23 are enabled;
the 5 tier-4 state-controlled outlets stay disabled and `claim_only`.

## Tiers

| Tier | Cadence | Role | Sources |
|---|---|---|---|
| 1 | 6h | Mandatory wire and public broadcaster baseline | `reuters`, `bbc` |
| 2 | 24h | Paid wires, broadcasters and discovery APIs | `ap`, `bloomberg`, `ft`, `economist`, `guardian`, `dw`, `lemonde`, `gdelt`, `guardian_api`, `nyt_api` |
| 3 | 24h | National and regional outlets | `aljazeera`, `kyivindependent`, `ukrainskapravda`, `suspilne`, `interfaxukraine`, `anadolu`, `thehindu`, `indianexpress`, `scmp`, `kyodo`, `yonhap` |
| 4 | disabled | State-controlled, `claim_only` perspective only | `rt`, `tass`, `ria`, `xinhua`, `globaltimes` |

## Mandatory sources

- **Reuters** — mandatory wire-service baseline, licensed and enabled. Discovery uses a sitemap;
  live routes require an approved route with a discovery URL.
- **BBC News** — mandatory public broadcaster baseline, licensed and enabled. The RSS feed is
  used for discovery; the article text is verified from the fetched page.

Both are reported as coverage gaps when a run finds no articles, never silently dropped.
All three rights-cleared live routes are in scope for the live backfill runner: Guardian and BBC
via RSS, Reuters via sitemap. Translation rights remain unconfirmed for Reuters and BBC.

## Rights rule

A source row is usable only when it is enabled, has approved rights, a non-empty licence
reference, an approved access mode and an enumerable discovery method that is not `web`. The T0
gate is checked before live fetches and during registry validation. Quote publication is then
gated by verification and rights limits, not by a second live T0 check. A blocked live fetch exits
with a configuration/T0 error; a run with no articles for a source exits with partial success and
reports the gap.

The renderer applies the quote limits: at most 30 words, named attribution and internal
territory. `claim_only` outlets are never treated as fact sources, and tier-4 outlets stay
disabled.

## Offline replay

Every source has local fixtures. Offline runs replay them deterministically; fixture replay is
not a rights workaround now that all sources are licensed. It keeps tests and demos repeatable
without network access.

`mq sources` prints the current gate state; `mq validate` checks that CSV, JSON routes and the
schema agree.
