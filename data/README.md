# Data files

This folder holds the auditable inputs and derived outputs of the Media Quotes Collection
System described in [`../docs/Test_News.md`](../docs/Test_News.md).

**Last verified:** 2026-11-25 against the JSON source configs, topic packs, `sources.csv`, `sections.csv`, `quotes.schema.json` and the fallback topic rules
(owner: Balgove Innovations). `quotes.schema.json` has a placeholder `$id`
(`https://example.com/schemas/quotes.schema.json`); it is a stable identifier, not a live URL.

> **System of record:** PostgreSQL (`src/media_quotes/db/schema.sql`) when
> `PERSIST_TO_POSTGRES=true`. In offline fixture mode the dated JSONL file is the local replay
> store. Never edit a derived file by hand — regenerate it from the authoritative records.

## Files

| File | Role |
| --- | --- |
| `sources.csv` | Mirrored tabular view of the authoritative `sources/*.json` configs. Regenerate with `mq sources --action sync-csv`. The T0 gate uses `rights_status` + `license_reference`. |
| `sections.csv` | Canonical catalog of report sections used for deterministic categorisation. |
| `quotes.schema.json` | Frozen JSON Schema (draft 2020-12) for one **derived export** quote record. Snapshot-tested; its hash is in the version manifest. |
| `quotes.example.jsonl` | Three clearly marked **synthetic** records showing the shape of the export. Do not treat them as real quotes. |
| `samples/` | Active XML fixtures used by `scripts/test_scraper.py` demonstrations. |
| `gold_set/` | Synthetic labelled evaluation sets (`synthetic-v1`, `synthetic-v2`); see its README. |
| `sources/`, `topics/` | Authoritative per-source JSON configs and per-topic packs. `sources.csv` and `source_topic_routes.csv` are generated mirrors; `topic_rules.json` is the generated fallback copy of the default topic pack's relevance block. |
| Validation logic | Lives in `src/media_quotes/validation/`, exposed via `mq doctor` and `scripts/validate_data.py`. |
| `jsonschema` | Declared in `pyproject.toml` core dependencies; used by validation and export models. |

## Generated outputs

Runtime artifacts are not stored under `data/`:

| Artifact | Path |
| --- | --- |
| Verified quote export | `../var/output/quotes/quotes-YYYY-MM-DD.jsonl` |
| Fixture replay export | `../var/test-output/quotes/quotes-YYYY-MM-DD.jsonl` |
| Daily digest | `../var/output/digests/YYYY-MM-DD.md` |
| Proof report | `../var/output/proof/source-proof-YYYY-MM-DD.json` and `.md` |

`quotes.jsonl` is derived from the same verified records that populate Postgres. It is rebuilt
from Postgres only with `mq export --from-db` and is never hand-maintained.

## Source registry (JSON configs and CSV mirror)

Text fields containing commas are quoted CSV strings; numeric fields (`tier`,
`cadence_hours`) are unquoted. The per-source description — role, current rights status,
what enabling each outlet requires — is in
[`docs/03-sources.md`](../docs/03-sources.md). Canonical enumerations (shared with the JSON
Schema and the Postgres `sources` table):

| Column | Meaning / allowed values |
| --- | --- |
| `source_id` | Stable machine key. Never rename it; quote records point to it. |
| `source_name`, `homepage_url` | Display name and canonical homepage. |
| `feed_url` | RSS/JSON feed for discovery only. Populated only where the feed and its robots.txt were checked during onboarding (BBC has one; Reuters is blank on purpose). A feed is not an exemption from T0 and never supplies quote text directly, but approved feeds and sitemaps can enumerate dated article URLs for `backfill --fetch-archive`. Archive discovery reads RSS/sitemap indexes only (no web crawling), is bounded by the report window and defaults to at most `--max-archive-articles` (200) articles. |
| `tier` | `1` mandatory, `2` authoritative additional, `3` non-Western original-reporting, `4` state-controlled claim-only perspective. |
| `cadence_hours` | Maximum interval between checks: `6` for Tier 1, `24` for Tiers 2–4. |
| `country_region`, `source_type` | Provenance and editorial model. `source_type` ∈ `independent`, `publicly funded`, `state-affiliated`, `state-controlled`, `aggregator`. |
| `claim_only` | `true` means the outlet's output may be collected but shown only as an attributed claim/perspective, never as an established fact. |
| `language`, `requires_translation`, `paywalled` | Language of the monitored edition, translation need and access constraint. `unknown` must be resolved at onboarding. |
| `rights_status` | `license_held`, `api_permitted`, `pending_counsel_review`, `blocked`. Only the first two permit enabling a source. |
| `access_mode` | `licensed_database`, `official_api`, `public_rss_permitted`, `pending_review`, `scraped_prohibited`. |
| `license_reference` | Licence/consent ID proving T0 approval; empty until approved. Logged on every ingest/audit row. |
| `discovery_method` | `sitemap`, `rss`, `api`, `gdelt`, `web`, `licensed_database` (shared enum in `src/media_quotes/enums.py`). |
| `archive_start_date` | Earliest date to backfill (`BACKFILL_START`, 2026-10-01 per the spec). |
| `enabled` | Whether the daily checker processes the source; requires T0 approval. |
| `availability_status` | `live`, `partial_body`, `accessible_no_quotes`, `blocked`, `discovery_only` or `fixture_only`. |
| `tier_block` | Operational block flag, separate from editorial tier. Requires `blocker_reason` when true. |
| `blocker_reason` | Human-readable reason a source cannot currently provide compliant full text. |
| `extractor_hints` | JSON-only extraction tuning: `adapter`, `default_feed_url`, `favor_precision`, `favor_recall`, `include_comments`, `include_tables`, `min_text_length`, `fallback_byline`, `strip_selectors` and `render_js`. A rendered-JS fallback additionally needs `EXTRACTION_FALLBACK=rendered`, the `render` group (`uv sync --group render`) and rights clearance. |
| `notes` | Human-readable onboarding and handling notes. |

Source JSON also supports `redirect_hosts`, an explicit allowlist for approved syndication
partners. Ukrainska Pravda uses it for `eurointegration.com.ua`; arbitrary cross-host redirects
remain blocked.

Each JSON route carries a `topic_id` (for example, `guardian` has separate `ukraine-war` and
`ai-policy` routes). This field is JSON-only: the CSV mirror carries `topic_rule_id` instead, so
treat the JSON config as authoritative for per-route topic assignment.

The registry records all 28 sources as `license_held` with a licence reference, and all 28 are
`enabled=true` for validation. 11 are `tier_block=true`; 17 are not blocked. The grant scope is
operator-attested, not independently verifiable from this checkout. The five tier-4
state-controlled outlets (`rt`, `tass`, `ria`, `xinhua` and `globaltimes`) are enabled as
`claim_only=true`; Anadolu is also `claim_only`. Reuters and BBC remain mandatory. Reuters is
`tier_block=true` because its public sitemap and direct pages return HTTP 401; licensed text/API
access or authorised samples are required. BBC is live-verified. Missing, partial and quote-free
routes are reported separately from fixture proof. The authoritative 2026-11-24 proof run covers
27 sources: 15 collected data and verified 76 live quotes, one was `accessible_no_quotes`, 11 were
blocked, none were fixture-only and Anadolu is absent after its live-route failure. All 29 approved
topic routes have `translation_rights_confirmed=false`.

## Derived quote export (`quotes.jsonl`)

One object per line, validated against `quotes.schema.json`. Key design choices:

- **Derived from verified records.** Rebuilt from Postgres only with `mq export --from-db` when
  persistence is enabled; in offline mode it is the local replay store. Regeneration is safe and
  idempotent.
- **Verbatim by default.** `quote_text_original` is an exact substring of the fetched page,
  confirmed by `verification.verbatim_match` and `content_hash`. `quote_text_en` is an
  *additional* field for non-English sources; it never replaces the original.
- **Deterministic IDs.** `Q-YYYYMMDD-NNNN` uses the publication date plus a sequence, so
  re-running a backfill produces the same IDs. A correction is a new versioned row in Postgres
  with an explanatory `notes` field and supersedes the earlier export line; the earlier row
  is retained for audit.
- **`claim_only` is carried into the output.** Every quote inherits it from the source config
  and registry row so the renderer can label state-controlled material as a claim.
- **`is_obsolete` controls presentation, not storage.** Superseded quotes stay in Postgres
  for auditability with `obsolete_reason`/`superseded_by`, but are filtered out of the
  rolling 14-day report and out of the current export view.
- **Provenance versions** (model snapshot, schema hash, prompt version, extractor version,
  `gold_set_version`) travel with each decision row in Postgres and are included in audit
  reports; the JSONL carries `content_hash` and verification metadata.

## Validation

Run `python -m media_quotes doctor` (or `python scripts/validate_data.py`) after any edit to
the registry or schema. Exit code 0 means clean, 1 means errors were found. Validation imports
the shared enums from `media_quotes.enums` and uses `jsonschema`. There is no fallback
validator: one schema, one code path.

## Adding a new source

1. Run `mq sources --action add --source-id <id> --homepage-url <url>` to scaffold
   `sources/<id>.json` with `enabled=false` and `rights_status=pending_counsel_review`.
2. Verify feed/HTML access, robots.txt, archive depth and paywall; fill `feed_url` and resolve
   any `unknown` values. Then run `mq sources --action sync-csv` to refresh the mirrored CSV.
3. Sample several articles and confirm first-hand bylines against wire copy
   (`original_reporting_flag` must be `first-hand` for Tier 3 inclusion); record the evidence.
4. Set `source_type`, `claim_only`, `rights_status`/`access_mode`/`license_reference`,
   `availability_status`, `tier_block` and `blocker_reason` per policy above.
5. Set `enabled=true` only after T0 approval, then backfill from `archive_start_date`.

## Daily output structure

Live/blob daily MD files are written to `../var/output/digests/YYYY-MM-DD.md` (canonical path
under `OUTPUT_DIR`). Synthetic fixture runs write the same structure under
`../var/test-output/` and are not operational data. The pipeline selects quotes where
`is_obsolete=false` and `publication_date`
falls in the rolling `REPORT_WINDOW_DAYS` (default 14) ending on `REPORT_DATE`, groups them
by `section_id`, and renders for each quote: verbatim text (original plus English translation
when present), source name, publication name, article name and canonical link. Quotes with
`claim_only=true` are labelled as claims. Under each section heading the configured inference
provider writes one or two sentences. The default local stand-in is deterministic; a live LLM
is the only non-deterministic part of the report.

`python -m scripts.source_proof --live --limit 5` writes
`../var/output/proof/source-proof-YYYY-MM-DD.json` and `.md`. Offline fixture proof defaults to
`../var/test-output/proof/`. The report separates collected live/sample data from fixture-only
evidence, records `tier_block` and blockers, and never counts fixture quotes as acquired source data.
