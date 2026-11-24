# Data files

This folder holds the auditable inputs and derived outputs of the Media Quotes Collection
System described in [`../docs/Test_News.md`](../docs/Test_News.md).

**Last verified:** 2026-11-23 against the JSON source configs, topic packs, `sources.csv`, `sections.csv` and `quotes.schema.json`
(owner: Balgove Innovations). `quotes.schema.json` has a placeholder `$id`
(`https://example.com/schemas/quotes.schema.json`); it is a stable identifier, not a live URL.

> **System of record:** PostgreSQL (`src/media_quotes/db/schema.sql`). Every file below is
> either a versioned input/config or a **derived export** generated from Postgres. Never edit
> a derived file by hand — a correction is a new versioned row in Postgres, and the export is
> regenerated. `quotes.jsonl` is not the quote store.

## Files

| File | Role |
| --- | --- |
| `sources.csv` | Mirrored tabular view of the authoritative `sources/*.json` configs. Regenerate with `mq sources --action sync-csv`. The T0 gate uses `rights_status` + `license_reference`. |
| `sections.csv` | Canonical catalog of report sections used for deterministic categorisation. |
| `quotes.schema.json` | Frozen JSON Schema (draft 2020-12) for one **derived export** quote record. Snapshot-tested; its hash is in the version manifest. |
| `quotes.jsonl` | **Derived export** of the current Postgres quote state. One JSON object per line; generated at runtime (not committed with synthetic data). Not hand-maintained. |
| `quotes.example.jsonl` | Three clearly marked **synthetic** records showing the shape of the export. Do not treat them as real quotes. |
| `report_template.md` | Historical Markdown template. The active renderer is the Python builder in `src/media_quotes/digest.py`; keep this as a reference for section ordering. |
| `sources/`, `topics/` | Authoritative per-source JSON configs and per-topic packs, plus `source_topic_routes.csv` and `topic_rules.json` for the topic gate. |
| Validation logic | Lives in the `src/media_quotes/validation/` package, exposed via `mq doctor` and `scripts/validate_data.py`. |
| `jsonschema` | Declared in `pyproject.toml` core dependencies; used by validation and export models. |
| `reports/` | Legacy compatibility directory — no active output is written here. Daily MD files go to `../var/output/digests/YYYY-MM-DD.md`. |

## Source registry (`sources.csv`)

Text fields containing commas are quoted CSV strings; numeric fields (`tier`,
`cadence_hours`) are unquoted. The per-source description — role, current rights status,
what enabling each outlet requires — is in
[`docs/03-sources.md`](../docs/03-sources.md). Canonical enumerations (shared with the JSON
Schema and the Postgres `sources` table):

| Column | Meaning / allowed values |
| --- | --- |
| `source_id` | Stable machine key. Never rename it; quote records point to it. |
| `source_name`, `homepage_url` | Display name and canonical homepage. |
| `feed_url` | RSS/JSON feed for discovery only. Populated only where the feed and its robots.txt were checked during onboarding (BBC has one; Reuters is blank on purpose). A feed is not an exemption from T0 and never supplies full text or a backfillable archive. |
| `tier` | `1` mandatory, `2` authoritative additional, `3` non-Western original-reporting, `4` excluded by default. |
| `cadence_hours` | Maximum interval between checks: `6` for Tier 1, `24` for Tiers 2–3, `0` when `enabled=false`. |
| `country_region`, `source_type` | Provenance and editorial model. `source_type` ∈ `independent`, `publicly funded`, `state-affiliated`, `state-controlled`, `aggregator`. |
| `claim_only` | `true` means the outlet's output may be collected but shown only as an attributed claim/perspective, never as an established fact. |
| `language`, `requires_translation`, `paywalled` | Language of the monitored edition, translation need and access constraint. `unknown` must be resolved at onboarding. |
| `rights_status` | `license_held`, `api_permitted`, `pending_counsel_review`, `blocked`. Only the first two permit enabling a source. |
| `access_mode` | `licensed_database`, `official_api`, `public_rss_permitted`, `pending_review`, `scraped_prohibited`. |
| `license_reference` | Licence/consent ID proving T0 approval; empty until approved. Logged on every ingest/audit row. |
| `discovery_method` | `sitemap`, `rss`, `api`, `gdelt`, `web`, `licensed_database` (shared enum in `src/media_quotes/enums.py`). |
| `archive_start_date` | Earliest date to backfill (`BACKFILL_START`, 2026-10-01 per the spec). |
| `enabled` | Whether the daily checker processes the source; requires T0 approval. |
| `notes` | Human-readable onboarding and handling notes. |

All 28 outlets are licensed (`license_held`, attested 2026-11-23); 23 are `enabled=true`
in `sources/*.json`. The 5 tier-4 state-controlled outlets (`rt`, `tass`, `ria`, `xinhua` and
`globaltimes`) stay `enabled=false` and `claim_only=true`; flip `enabled` only if you
explicitly want to study those narratives. Reuters and BBC are Tier 1 mandatory and
licensed — they are never substituted, only reported as coverage gaps when no articles are
found.

## Derived quote export (`quotes.jsonl`)

One object per line, validated against `quotes.schema.json`. Key design choices:

- **Derived, not authoritative.** Generated from Postgres; regeneration is safe and idempotent.
- **Verbatim by default.** `quote_text_original` is an exact substring of the fetched page,
  confirmed by `verification.verbatim_match` and `content_hash`. `quote_text_en` is an
  *additional* field for non-English sources; it never replaces the original.
- **Deterministic IDs.** `Q-YYYYMMDD-NNNN` uses the publication date plus a sequence, so
  re-running a backfill produces the same IDs. A correction is a new versioned row in Postgres
  with an explanatory `notes` field and supersedes the earlier export line; the earlier row
  is retained for audit.
- **`claim_only` is carried into the output.** Every quote inherits it from `sources.csv` so
  the renderer can label state-controlled material as a claim.
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
4. Set `source_type`, `claim_only` and `rights_status`/`access_mode`/`license_reference` per
   policy above.
5. Set `enabled=true` only after T0 approval, then backfill from `archive_start_date`.

## Daily output structure

Daily MD files are written to `../var/output/digests/YYYY-MM-DD.md` (canonical path under
`OUTPUT_DIR`). The pipeline selects quotes where `is_obsolete=false` and `publication_date`
falls in the rolling `REPORT_WINDOW_DAYS` (default 14) ending on `REPORT_DATE`, groups them
by `section_id`, and renders for each quote: verbatim text (original plus English translation
when present), source name, publication name, article name and canonical link. Quotes with
`claim_only=true` are labelled as claims. Under each section heading the LLM writes one or two
sentences of inference; this is the only non-deterministic part of the report.
