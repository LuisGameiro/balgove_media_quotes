# Scripts

Scripts are grouped by purpose. All paths below are relative to the repository root.
Nothing in `ci/` or `data/` touches the network or a database.
Documentation is curated by hand; there is no automated docs checker.

## `ci/` — quality gates and validation

| Script | Mode | Output |
|---|---|---|
| `check.ps1` | Offline local/CI gate (data validation, ruff, mypy, pytest, build, CLI smoke) | Console pass/fail |
| `validate_data.py` | Offline registry/schema/derived-data validation | Console validation results and exit code |

`make check` and `scripts/ci/check.ps1` run the same checks. Use `make check` where `make` is
available; the PowerShell wrapper also resolves the local virtualenv on Windows.

## `ops/` — operator workflows

| Script | Mode | Output |
|---|---|---|
| `run_all.ps1` | Starts local Postgres, runs CI, bootstrap, evals and Dagster materialization; optional dashboard | Console pass/fail |
| `run_dagster.ps1` | Starts the local Dagster UI; schedules/sensor stopped by default | `var/dagster/` runtime files |
| `enable_automation.ps1` | Starts existing Dagster schedules/sensor: requires `-EnableSchedules` and/or `-EnableSensor` plus the matching `ENABLE_*` env flag | Console status |
| `blob_prune_scheduled.ps1` | Operator-run prune with a required, non-empty tombstone file | Console status |

Everything else is a direct `mq` command documented in
[05 — Command reference](../docs/05-command-reference.md). There are no wrappers around `doctor`,
`run`, `audit`, `eval` or the dashboard.

## `diagnostics/` — scraper and source proof tools

| Script | Mode | Output |
|---|---|---|
| `test_scraper.py` | Offline by default; `--live`/`--live-routes` are opt-in network probes | JSON reports on stdout; `--write-sample` writes `var/test-output/samples/article.html` |
| `source_proof.py` | `--offline` is safe; `--live`/`--url` are network opt-in | `var/test-output/proof/` or `var/output/proof/` JSON + Markdown |

Top-level `scripts/test_scraper.py` and `scripts/source_proof.py` are compatibility shims
that delegate to these implementations, so `python -m scripts.source_proof` and existing
test imports keep working.

## `data/` — fixture and gold-set generation

| Script | Mode | Output |
|---|---|---|
| `generate_gold_fixtures.py` | Offline and deterministic; `--check` writes nothing | `data/fixtures/`, `data/gold_set/synthetic-v1.json` |
| `generate_topic_matrix.py` | Offline and deterministic; `--check` writes nothing | `data/fixtures/`, `data/gold_set/synthetic-v2.json` |
