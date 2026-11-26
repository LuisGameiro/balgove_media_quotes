# Data files

This folder holds the registry inputs, frozen schema, fixtures and gold sets used by the Media Quotes
Collection System described in [`../docs/Test_News.md`](../docs/Test_News.md).

> **System of record:** PostgreSQL (`../migrations/schema.sql`) when `PERSIST_TO_POSTGRES=true`.
> In offline fixture mode the dated JSONL file is the local replay store. Regenerate derived files;
> do not edit them by hand.

## Committed files

| File or folder | Role |
| --- | --- |
| `sources/*.json` | Authoritative per-source configs. |
| `sources.csv`, `source_topic_routes.csv` | Generated tabular mirrors. Refresh with `mq sources --action sync-csv`. |
| `topics/*.json`, `topic_rules.json`, `sections.csv` | Topic packs, versioned topic gate rules and report sections. |
| `quotes.schema.json` | Frozen draft 2020-12 schema for one derived export record. Its `$id` is a stable identifier, not a live URL. |
| `quotes.example.jsonl` | Three clearly synthetic records showing the export shape. |
| `fixtures/` | Read-only synthetic article pages for offline replay. |
| `gold_set/` | Labelled synthetic evaluation sets; see `gold_set/README.md`. |

Generated output belongs under `../var/output/`, never here. Dated `quotes-YYYY-MM-DD.jsonl` files are created at runtime.

## Registry rules

The registry records all 28 outlets as `license_held` and enabled for validation. That is an operator
attestation, not independently verified legal proof. Tier 4 is enabled as claim-only perspective
material; it is not excluded by default. Reuters and BBC remain mandatory. Reuters returned HTTP 401
on 2026-11-24 and is reported as a coverage gap.

The full registry description, tiers, T0 gate and live route health are in
[`../docs/03-sources.md`](../docs/03-sources.md). Canonical enumerations live in
`../src/media_quotes/enums.py` and are shared with the schema and JSON Schema.

## Export rules

One JSON object per line, validated against `quotes.schema.json`.

- `quote_text_original` is an exact substring of the fetched page, confirmed by `verbatim_match`
  and `content_hash`. `quote_text_en` is an additional translation, never a replacement.
- The rights word cap applies to both original text and `quote_text_en` when a translation is
  present.
- IDs use `Q-YYYYMMDD-NNNN`, so a backfill is repeatable. A correction is a new versioned Postgres
  row that supersedes the earlier line, which is kept for audit.
- `claim_only` travels with every quote so the renderer can label perspective material.
- Superseded quotes stay in Postgres but are filtered from the rolling 14-day report.
- Model, schema, prompt and extractor versions travel with each decision row.

## Validation

Run `mq doctor` or `python -m scripts.ci.validate_data` after editing the registry or schema. Exit
code `0` means clean, `1` means errors. Validation uses the shared enums and `jsonschema`.

## Adding a source

1. `mq sources --action add --source-id <id> --homepage-url <url>` scaffolds a disabled config.
2. Verify feed/HTML access, robots.txt, archive depth and paywall, then run `sync-csv`.
3. Sample articles and record first-hand evidence. New sources start as `unknown` until signed off.
4. Set `source_type`, `claim_only` and rights fields.
5. Set `enabled=true` only after T0 approval, then backfill from `archive_start_date`.

## Daily output

`mq run` selects non-obsolete quotes published in the rolling `REPORT_WINDOW_DAYS` window, groups
them by section and writes to `../var/output/digests/YYYY-MM-DD.md`. Canonical paths belong to a
complete `live_feed` run with no filter; partial runs use `../var/output/*/runs/<scope>/`.

`python -m scripts.source_proof --live --limit 5` writes the per-source JSON and Markdown proof
report under `../var/output/proof/`, separating live, sample and fixture evidence.
