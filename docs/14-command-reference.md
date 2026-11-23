# 14 — Command and script reference

Single-source cheatsheet and hands-on cookbook for running, verifying, testing, and
developing the Media Quotes pipeline on Windows PowerShell. All paths and commands assume
PowerShell from the repository root (`C:\Users\lgame\Desktop\balgoveinnovations`).

---

## 1. Quality gate and CI (run first, run always)

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check.ps1
```

Or install the one-word `check` shortcut into your PowerShell profile (`$PROFILE`):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_check_profile.ps1
check
```

Steps executed by `scripts/check.ps1`:
1. `docs_qa.py` — 41 requirement and cross-document consistency assertions (`CHK-08`).
2. `ruff format --check .` — zero formatting diffs.
3. `ruff check .` — zero lint or security warnings.
4. `mypy --strict src/` — strict static type checking for the shipped package (the enforced gate; tests and scripts are outside this gate).
5. `pytest --cov=media_quotes --cov-fail-under=85` — 240+ unit & integration tests, >=85% branch coverage.
6. `python -m build` — builds wheel and sdist into `dist/`.
7. `CLI smoke` — verifies `python -m media_quotes --help` and `python -m media_quotes.cli --help`.

---

## 2. All 20 CLI subcommands (`media_quotes.cli` / `mq`)

The CLI entrypoint is `.\.venv\Scripts\python.exe -m media_quotes` (or `python -m media_quotes` / `mq` when the virtualenv is active).

| Subcommand | Purpose | Example invocation |
|---|---|---|
| `doctor` | Self-diagnostic check (data files, T0 rights gate, active env/DB/Dagster config) | `python -m media_quotes doctor` |
| `validate` | Validate `data/sources.csv`, `data/sections.csv`, and `data/quotes.schema.json` | `python -m media_quotes validate` |
| `bootstrap` | Run Alembic migrations (`0001..0006`) and sync `data/sources/*.json` into Postgres | `python -m media_quotes bootstrap` |
| `version` | Print package version, schema hash, prompt version, and runtime date window | `python -m media_quotes version` |
| `run` | Execute daily collection pipeline (discover, fetch, verify, dedupe, digest, export, audit) | `python -m media_quotes run --date 2026-11-19` |
| `run --dry-run` | Plan collection and inspect T0 coverage gaps without writing files or DB rows | `python -m media_quotes run --dry-run --date 2026-11-19` |
| `audit` | Generate deterministic audit report (`var/audit/audit-YYYY-MM-DD.{json,md}`); `--topic` selects a non-default topic pack | `python -m media_quotes audit --date 2026-11-19 --topic ai-policy` |
| `digest` | Regenerate Markdown digest (`digests/YYYY-MM-DD.md`) from verified local store | `python -m media_quotes digest --date 2026-11-19` |
| `export` | Write schema-validated derived JSONL export from local files, or add `--from-db` to rebuild it from Postgres | `python -m media_quotes export --date 2026-11-19 --from-db --out var/export/latest.jsonl` |
| `logs` | Filter and print structured JSON logs correlated by `--run-id` and optional `--trace-id` | `python -m media_quotes logs --run-id run-3300d30f1c8e` |
| `eval` | Evaluate versioned gold set against precision/recall/false-quote contracts (`--split` optional) | `python -m media_quotes eval --gold-set synthetic-v1 --date 2026-11-19` |
| `sources` | Inspect source registry, T0 gate status, and per-source quote counts (`--tier`, `--rights-status`) | `python -m media_quotes sources --tier 1` |
| `topics` | List, scaffold (`init`) or show topic packs (`--action`, `--name`, `--description`) | `python -m media_quotes topics --action list` |
| `fetch` | Plan a rights-cleared fetch; add `--live` to contact the source | `python -m media_quotes fetch --source-id guardian` |
| `backfill` | Estimate historical backfill articles, cache hit rate, duration, and cost vs `--max-cost` | `python -m media_quotes backfill --from 2026-10-01 --to 2026-11-19 --max-cost 100 --dry-run` |
| `batch` | Plan or submit a cost-capped OpenRouter batch; `--status` / `--apply` import results | `python -m media_quotes batch --requests requests.json --max-cost 10 --dry-run` |
| `review` | Manage human-in-the-loop obsolescence review queue (`--action list\|seed\|decide`) | `python -m media_quotes review --action list` |
| `retention` | Check RSK-11 monthly partition detach readiness and 180d blob / 90d log cutoffs | `python -m media_quotes retention --dry-run` |
| `restore-drill` | Compare two derived JSONL exports for round-trip identity and SHA-256 `content_hash` integrity | `python -m media_quotes restore-drill --date 2026-11-19 --reference var/export/quotes-2026-11-18.jsonl` |
| `dagster` | Print Dagster assets, asset checks, jobs, schedule, sensor, and launch commands | `python -m media_quotes dagster` |
| `dashboard` | Print Streamlit dashboard status and artifact directories | `python -m media_quotes dashboard` |

Exit codes:
- `0` (`EXIT_OK`): Command succeeded with no errors or coverage gaps.
- `1` (`EXIT_FAILURE`): Validation, threshold, or restore-drill failure.
- `2` (`EXIT_GAPS`): Partial success with disclosed T0 coverage gaps (expected while Reuters/BBC remain blocked).
- `3` (`EXIT_CONFIG`): Configuration error, T0 violation, or backfill `--max-cost` budget exceeded.

---

## 3. Database bootstrap and migrations

```powershell
# 1. Start local PostgreSQL 16 container
docker compose up -d --wait
docker compose ps

# 2. Apply Alembic migrations (0001_initial .. 0006_digest_topic_unique) and sync data/sources/*.json
.\.venv\Scripts\python.exe -m media_quotes.bootstrap

# 3. Or invoke via the CLI subcommand
.\.venv\Scripts\python.exe -m media_quotes bootstrap

# 4. Verify database health, active settings, and T0 gate
.\.venv\Scripts\python.exe -m media_quotes doctor
```

---

## 4. Evaluation and gold set (`mq eval`)

```powershell
# Full gold-set evaluation (synthetic-v1: 80 articles across 60/20/20 splits + 14 negative candidates)
.\.venv\Scripts\python.exe -m media_quotes eval --gold-set synthetic-v1 --date 2026-11-19

# Evaluate an individual deterministic split (train | dev | test | all)
.\.venv\Scripts\python.exe -m media_quotes eval --gold-set synthetic-v1 --split train --date 2026-11-19
.\.venv\Scripts\python.exe -m media_quotes eval --gold-set synthetic-v1 --split dev --date 2026-11-19
.\.venv\Scripts\python.exe -m media_quotes eval --gold-set synthetic-v1 --split test --date 2026-11-19
```

Contract thresholds enforced (`AcceptanceThresholds` in `src/media_quotes/evaluation.py`):
- Article recall: $\ge 0.90$
- Quote recall: $\ge 0.90$
- Span exact match: $\ge 0.95$
- Metadata accuracy: $\ge 0.95$
- Section accuracy: $\ge 0.90$
- False quote rate: $= 0.0$
- Obsolete precision: $\ge 0.90$
- Judge Cohen's $\kappa$: $\ge 0.70$

---

## 5. Offline replay, review queue, retention, and restore drills

```powershell
# Step 1: Run two daily partitions (idempotent even when re-run multiple times against Postgres)
.\.venv\Scripts\python.exe -m media_quotes run --verbose --date 2026-11-18
.\.venv\Scripts\python.exe -m media_quotes run --verbose --date 2026-11-19

# Step 2: Run restore drill comparing the two daily exports
.\.venv\Scripts\python.exe -m media_quotes restore-drill --date 2026-11-19 --reference var/export/quotes-2026-11-18.jsonl

# Step 3: Seed, inspect, and decide a human-in-the-loop obsolescence flag (RSK-07 / CHK-15)
.\.venv\Scripts\python.exe -m media_quotes review --action seed --quote-id Q-20261118-0001
.\.venv\Scripts\python.exe -m media_quotes review --action list
.\.venv\Scripts\python.exe -m media_quotes review --action decide --quote-id Q-20261118-0001 --verdict confirmed --reason-code superseded-by-new-statement
.\.venv\Scripts\python.exe -m media_quotes review --action list

# Step 4: Dry-run RSK-11 retention readiness plan
.\.venv\Scripts\python.exe -m media_quotes retention --dry-run
```

---

## 6. Development and testing shortcuts

```powershell
# Run all 240+ tests quietly
.\.venv\Scripts\python.exe -m pytest -q

# Run unit tests only
.\.venv\Scripts\python.exe -m pytest tests/unit/ -v

# Run Dagster orchestration unit tests (assets, 6 checks, governance job, SLA sensor)
.\.venv\Scripts\python.exe -m pytest tests/unit/test_orchestration.py -v

# Run OpenRouter / LLM gateway unit tests (no network calls made)
.\.venv\Scripts\python.exe -m pytest tests/unit/test_openrouter.py tests/unit/test_llm_live.py -v

# Run newspaper discovery & scraping unit tests (RSS, Sitemap, JSON-LD, Trafilatura, multi-format quotes)
.\.venv\Scripts\python.exe -m pytest tests/unit/test_newspaper_scraping.py -v

# Run integration tests (including SQL storage persistence)
.\.venv\Scripts\python.exe -m pytest tests/integration/ -v

# Format and lint check
.\.venv\Scripts\ruff.exe check src tests scripts
.\.venv\Scripts\ruff.exe format --check src tests scripts

# Strict type check of the shipped package (the enforced gate)
.\.venv\Scripts\mypy.exe --strict src/

# Type-check a touched diagnostic script/tests explicitly when changing them
.\.venv\Scripts\mypy.exe --strict scripts/test_scraper.py tests/unit/test_newspaper_scraping.py

# Documentation QA check (41 assertions)
.\.venv\Scripts\python.exe scripts/docs_qa.py

# Validate schema of data files
.\.venv\Scripts\python.exe scripts/validate_data.py

# Run end-to-end manual pipeline validation script (validate -> doctor -> run -> audit)
powershell -ExecutionPolicy Bypass -File .\scripts\manual_validate.ps1 -Date 2026-11-19
powershell -ExecutionPolicy Bypass -File .\scripts\manual_validate.ps1 -Date 2026-11-19 -DryRun
```

---

## 7. Dagster orchestration & Streamlit dashboard (Full CLI & UI Reference)

`DAGSTER_HOME` is configured in `.env` (`DAGSTER_HOME=C:/Users/lgame/Desktop/balgoveinnovations/var/dagster`), so Dagster commands automatically persist run history, asset check results, and sensor cursors in `var/dagster`.

### 7.1 Inspecting Definitions, Assets, Jobs, Schedules & Sensors

```powershell
# 1. Print project-level Dagster summary (assets, 6 checks, jobs, schedule, sensor, Postgres flag)
.\.venv\Scripts\python.exe -m media_quotes dagster

# 2. Validate the Dagster code location without starting a daemon
.\.venv\Scripts\dagster.exe definitions validate -m media_quotes.orchestration

# 3. List all assets and jobs in the code location
.\.venv\Scripts\dagster.exe asset list -m media_quotes.orchestration
.\.venv\Scripts\dagster.exe job list -m media_quotes.orchestration
```

### 7.2 Materializing Assets & Running Jobs from the CLI

```powershell
# 1. Materialize a single daily partition of `daily_quotes_digest`
.\.venv\Scripts\dagster.exe asset materialize -m media_quotes.orchestration --select daily_quotes_digest --partition 2026-11-19

# 2. Materialize multiple daily partitions for a backfill/restore drill comparison
.\.venv\Scripts\dagster.exe asset materialize -m media_quotes.orchestration --select daily_quotes_digest --partition 2026-11-18
.\.venv\Scripts\dagster.exe asset materialize -m media_quotes.orchestration --select daily_quotes_digest --partition 2026-11-19

# 3. Materialize the governance & evaluation assets (`gold_set_evaluation` and `retention_readiness_report`)
.\.venv\Scripts\dagster.exe asset materialize -m media_quotes.orchestration --select "gold_set_evaluation,retention_readiness_report"

# 4. Execute named Dagster jobs directly (`daily_quotes_digest_job` or `governance_audit_job`)
.\.venv\Scripts\dagster.exe job execute -m media_quotes.orchestration -j daily_quotes_digest_job --tags '{"dagster/partition": "2026-11-19"}'
.\.venv\Scripts\dagster.exe job execute -m media_quotes.orchestration -j governance_audit_job
```

### 7.3 Managing Schedules & Sensors from the CLI

```powershell
# Manage `daily_digest_schedule` (06:00 UTC daily; stopped by default)
.\.venv\Scripts\dagster.exe schedule list -m media_quotes.orchestration
.\.venv\Scripts\dagster.exe schedule start -m media_quotes.orchestration daily_digest_schedule
.\.venv\Scripts\dagster.exe schedule stop -m media_quotes.orchestration daily_digest_schedule

# Manage `review_queue_sla_sensor` (300s interval; monitors 24h obsolescence review SLA; stopped by default)
.\.venv\Scripts\dagster.exe sensor list -m media_quotes.orchestration
.\.venv\Scripts\dagster.exe sensor start -m media_quotes.orchestration review_queue_sla_sensor
.\.venv\Scripts\dagster.exe sensor stop -m media_quotes.orchestration review_queue_sla_sensor
```

### 7.4 Launching the Interactive Web UIs (Dagster & Streamlit)

```powershell
# 1. Launch the Dagster Web UI at http://localhost:3000 (ensures var/dagster exists first)
powershell -ExecutionPolicy Bypass -File .\scripts\run_dagster.ps1

# 2. Launch the Streamlit offline dashboard (requires dashboard extra)
powershell -ExecutionPolicy Bypass -File .\scripts\run_dashboard.ps1
```

The dashboard can also be launched directly:

```powershell
streamlit run src/media_quotes/dashboard.py
```

For Streamlit Community Cloud, connect this repository to GitHub, publish branch `main`, and set
the main file path to `src/media_quotes/dashboard.py`. The root `requirements.txt` installs the
project with the `dashboard` extra. No database URL or API key is needed for the offline dashboard.

`src/media_quotes/dashboard.py` resolves its config import both as `media_quotes.dashboard`
and when Streamlit executes it as a standalone script, so the previous
`ImportError: attempted relative import with no known parent package` no longer occurs.

### 7.5 Summary of Dagster Definitions (`src/media_quotes/orchestration.py`)

- **Assets (`3`):**
  - `daily_quotes_digest` (`DailyPartitionsDefinition(start_date="2026-10-01")`, group `media_quotes`, `code_version=c4-daily-pipeline-v2`)
  - `gold_set_evaluation` (group `media_quotes_governance`, `CHK-03`)
  - `retention_readiness_report` (group `media_quotes_governance`, `CHK-17` / `RSK-11`)
- **Asset checks (`6`):**
  - `export_schema_valid` — verifies strict `ExportQuoteRecord` JSONL deserialization.
  - `audit_gates_pass` — verifies `gates_pass=true` in the partition's audit report.
  - `coverage_gaps_disclosed` — verifies blocked sources are disclosed as coverage gaps.
  - `t0_rights_boundary_enforced` (`CHK-01`) — verifies zero live fetches for blocked sources.
  - `quote_rights_limits_respected` (`DEC-10` / `CHK-19`) — verifies `<= 30` words and required attribution.
  - `gold_set_thresholds_met` (`CHK-03`) — verifies `0%` false-quote rate and all gold-set thresholds.
 All five checks on `daily_quotes_digest` accept `CheckConfig(topic_id)`. When a check is executed
 on its own, pass the same `topic_id` as the asset run it validates; otherwise the configured
 default topic is assumed.
- **Jobs (`2`):** `daily_quotes_digest_job`, `governance_audit_job`.
- **Schedule & sensor (`2`):** `daily_digest_schedule` (`0 6 * * * UTC`, stopped by default), `review_queue_sla_sensor` (300s interval, stopped by default).
- **Resources & config:** `settings`, `database`; run config `DailyPipelineConfig(dry_run, persist_to_postgres, topic_id)` and `GoldSetEvalConfig(gold_set, report_date)`.
- **Rich UI metadata:** Inline Markdown digest and audit previews (`MetadataValue.md`), per-source coverage and T0 status table (`MetadataValue.table`), clickable artifact paths (`MetadataValue.path`), and structured secret-redacted compute logs.

---

## 8. Docker operations

```powershell
docker compose up -d --wait      # start local Postgres
docker compose ps                # service status
docker compose logs -f postgres  # tail Postgres container logs
docker compose down              # stop container; add -v to remove volumes (destroys local DB)
```

---

## 9. Step-by-step operational runbook (PowerShell)

Prerequisites: activated `(media-quotes)` venv, `.env` copied from `.env.example`, `check` function installed (§1). Run from the repository root. Each phase ends with a pass/fail signal; **do not continue past a failed phase**.

### Phase 0 — full quality gate

```powershell
check
```
Expect: docs QA 41/41, format/lint/mypy clean, 240+ tests at >=85% coverage, wheel+sdist built, CLI smoke OK, ending in `ALL CHECKS PASSED`. Equivalent without the profile function: `powershell -ExecutionPolicy Bypass -File .\scripts\check.ps1`.

### Phase 1 — dry-run (confirm after any change)

```powershell
python -m media_quotes run --dry-run --date 2026-11-19
python -m media_quotes backfill --from 2026-10-01 --to 2026-11-19 --max-cost 100 --dry-run
python -m media_quotes doctor
python -m media_quotes version
python scripts/validate_data.py
```
Expect: no files written by `run --dry-run`; `doctor` prints `T0 gate: 2 mandatory source(s) blocked` (Reuters/BBC — correct) plus active config (`llm_provider`, `persist_to_postgres`, `dagster_home`); backfill prints estimate, cache-hit summary and budget note; `validate_data.py` says all data valid.

### Phase 2 — real offline run (file outputs, no network)

```powershell
python -m media_quotes run --date 2026-11-19
python -m media_quotes audit --date 2026-11-19
python -m media_quotes eval --gold-set synthetic-v1 --date 2026-11-19
python -m media_quotes review --action list
python -m media_quotes logs --run-id run-1870a49f4880
python -m media_quotes sources
python -m media_quotes retention --dry-run
```
PowerShell treats `<` and `>` as redirection operators: paste the **actual** run id printed by `run`; do not type angle-bracket placeholders.

### Phase 3 — local Postgres + persistence

`.env` has `PERSIST_TO_POSTGRES=true` and `LLM_PROVIDER=local`. Run:

```powershell
docker compose up -d --wait
docker compose ps                       # service "postgres" should be healthy
python -m media_quotes.bootstrap        # Alembic 0001..0006 + data/sources/*.json sync
python -m media_quotes doctor
python -m media_quotes run --date 2026-11-19
python -m media_quotes eval --gold-set synthetic-v1 --date 2026-11-19
```
Expect: bootstrap applies migrations and syncs 28 sources; `run` prints `Postgres: N article(s), M quote(s) persisted`; `eval` stores an `eval_runs` row and 98 `gold_set_labels` rows. Re-running the same date is idempotent.

### Phase 4 — Dagster UI & governance assets

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_dagster.ps1
```
In the Dagster UI (http://localhost:3000): select asset **`daily_quotes_digest`**, partition **`2026-11-19`**, click *Materialize*. Expect a successful run with inline Markdown previews (`digest_preview`, `audit_report_preview`), an interactive `coverage_table`, and all 6 asset checks passing. Then materialize **`gold_set_evaluation`** and **`retention_readiness_report`** in the `media_quotes_governance` group.

### Phase 5 — Track A rights (stakeholder action)

Only after written confirmation for `guardian_api`, `gdelt`, `suspilne`, `kyivindependent`:
1. Edit `data/sources.csv`: for those four rows set `rights_status=api_permitted` (or `license_held`), fill `license_reference` with the real consent ID, set the approved `access_mode`, `enabled=true`, update `notes`.
2. Update `docs/07-sources.md` and the `DEC-14` / `08-traceability.md` records.
3. Re-sync and verify:

```powershell
python -m media_quotes.bootstrap
python -m media_quotes doctor
python -m media_quotes sources --tier 2
python -m media_quotes sources --tier 3
python -m media_quotes run --date 2026-11-19
```
Expect fewer blocked sources in `doctor`; GDELT remains **discovery only**; Reuters/BBC (and other paywalls/state-controlled outlets) stay disabled until their own licences are signed.

### Phase 6 — optional live LLM (section inference only)

In `.env`, after rotating any exposed key:

```dotenv
LLM_PROVIDER=openrouter
LLM_MODEL_SNAPSHOT=openai/gpt-4o-mini-2024-07-18
OPENROUTER_API_KEY=<new-rotated-key>
```
Then:
```powershell
python -m media_quotes run --date 2026-11-19
python -m media_quotes audit --date 2026-11-19
```
Expect only the 1–2 sentence section summaries to change. Quote selection, deterministic verification, rights limits and the T0 gate are untouched. To return offline, set `LLM_PROVIDER=local` again.

### Phase 7 — close the accepted risks with production evidence

- RSK-07: `mq eval` obsolete precision >= 0.90 from held-out human labels; review SLA met.
- RSK-08: three real backfills within +/-20% of the estimate; `--max-cost` breach exits `3`.
- RSK-11: load test with transactions < 60s; `mq retention`/`mq restore-drill` pass.

Then mark RSK-07/08/11 `solved` in `docs/04` and `docs/08` (keep the ID tokens so `docs_qa.py` stays green), and record the verification in `docs/10`. Full background: `docs/12-implementation-plan.md`.

---

## 10. Verification of Brief §4 Quality Requirements (Requirement-by-Requirement Commands)

Every quality requirement in §4 of the brief (`REQ-01`..`REQ-14` in [01-requirements-analysis.md](01-requirements-analysis.md)) is verifiable through a deterministic CLI command, Dagster asset check, or unit test suite:

| Brief §4 Quality Requirement | Req ID | Implementation Module(s) | Command(s) to Verify |
|---|---|---|---|
| **0. T0 rights gate: no source ingested without recorded permission; logs include `license_reference`** | `REQ-01` | `src/media_quotes/registry.py`, `src/media_quotes/db/schema.sql`, `src/media_quotes/orchestration.py` | `.\.venv\Scripts\python.exe -m media_quotes doctor`<br>`.\.venv\Scripts\python.exe -m media_quotes sources --tier 1`<br>`.\.venv\Scripts\python.exe -m pytest tests/unit/test_registry.py tests/unit/test_fetch.py -v` |
| **1. All material articles from mandatory sources should be detected** *(in blocked mode: replayed from synthetic fixtures and disclosed as explicit coverage gaps)* | `REQ-02` | `src/media_quotes/adapters/`, `src/media_quotes/cli/audit.py` | `.\.venv\Scripts\python.exe -m media_quotes run --verbose --date 2026-11-19`<br>`.\.venv\Scripts\python.exe -m media_quotes audit --date 2026-11-19`<br>`.\.venv\Scripts\python.exe -m media_quotes eval --gold-set synthetic-v1 --date 2026-11-19` *(checks `article_recall >= 0.90`)* |
| **2. All material quotes from those articles should be detected** | `REQ-05` | `src/media_quotes/adapters/`, `src/media_quotes/gold_set.py`, `src/media_quotes/cli/eval.py` | `.\.venv\Scripts\python.exe -m media_quotes eval --gold-set synthetic-v1 --date 2026-11-19` *(checks `quote_recall >= 0.90` across 60/20/20 splits)*<br>`.\.venv\Scripts\python.exe -m media_quotes eval --gold-set synthetic-v1 --split test --date 2026-11-19` |
| **3. Quotes relevant, substantial, self-explanatory, and cleansed (no ads, page numbers, table symbols, cookie notices)** | `REQ-06` | `src/media_quotes/verification_stages.py`, `src/media_quotes/verification.py`, `src/media_quotes/rights_guard.py` | `.\.venv\Scripts\python.exe -m pytest tests/unit/test_verification_stages.py tests/unit/test_verification.py tests/unit/test_rights_guard.py -v`<br>`.\.venv\Scripts\python.exe -m media_quotes eval --gold-set synthetic-v1 --date 2026-11-19` *(verifies `false_quote_rate == 0.0` against 14 negative candidates)* |
| **4. All material quotes are extracted and saved (no important articles or quotes left out)** | `REQ-07` | `src/media_quotes/pipeline.py`, `src/media_quotes/db/repository.py`, `src/media_quotes/export.py` | `.\.venv\Scripts\python.exe -m media_quotes run --date 2026-11-19`<br>`.\.venv\Scripts\python.exe -m media_quotes export --date 2026-11-19`<br>`.\.venv\Scripts\python.exe -m media_quotes restore-drill --date 2026-11-19 --reference var/export/quotes-2026-11-18.jsonl` |
| **5. Accurate extraction of quotes and metadata from the primary source (exact NFC span, speaker, canonical URL, date, byline, tier, claim-only flags)** | `REQ-03`, `REQ-04`, `REQ-08` | `src/media_quotes/text_utils.py`, `src/media_quotes/verification.py`, `src/media_quotes/models.py` | `.\.venv\Scripts\python.exe -m media_quotes eval --gold-set synthetic-v1 --date 2026-11-19` *(verifies `span_exact_match == 1.0` and `metadata_accuracy == 1.0`)*<br>`.\.venv\Scripts\python.exe -m pytest tests/unit/test_text_utils.py tests/unit/test_models.py -v` |
| **6. Accurate load for the MD file (verbatim text, categorisation into canonical sections, and obsolete quotes excluded/badged)** | `REQ-09` | `src/media_quotes/digest.py`, `src/media_quotes/curation.py`, `src/media_quotes/risk_controls/obsolescence.py` | `.\.venv\Scripts\python.exe -m media_quotes digest --date 2026-11-19`<br>`.\.venv\Scripts\python.exe -m media_quotes review --action seed --quote-id Q-20261118-0001`<br>`.\.venv\Scripts\python.exe -m media_quotes review --action decide --quote-id Q-20261118-0001 --verdict confirmed --reason-code superseded`<br>`.\.venv\Scripts\python.exe -m pytest tests/unit/test_curation.py tests/unit/test_digest.py -v` |
| **7. Relevant 1–2 sentence interpretation of quotes for each section in the MD file** | `REQ-10` | `src/media_quotes/llm.py`, `src/media_quotes/llm_live.py`, `src/media_quotes/llm_openrouter.py` | `.\.venv\Scripts\python.exe -m pytest tests/unit/test_llm_live.py tests/unit/test_openrouter.py -v`<br>`.\.venv\Scripts\python.exe -m media_quotes run --date 2026-11-19` *(uses offline `LLM_PROVIDER=local` or live `LLM_PROVIDER=openrouter`)* |
| **8. Database of quotes is auditable and easily integratable with frontier labs' LLMs (strict frozen JSON Schema + content-addressed SHA-256 hashes)** | `REQ-11` | `data/quotes.schema.json`, `src/media_quotes/export.py`, `src/media_quotes/db/schema.sql` | `.\.venv\Scripts\python.exe scripts/validate_data.py`<br>`.\.venv\Scripts\python.exe -m media_quotes export --date 2026-11-19 --out var/export/quotes.jsonl`<br>`.\.venv\Scripts\dagster.exe asset materialize -m media_quotes.orchestration --select daily_quotes_digest --partition 2026-11-19` *(runs the `export_schema_valid` check)* |
| **9. Logs are saved for auditability and traceability (`run_id`, `trace_id`, `license_reference`, secret redaction)** | `REQ-12` | `src/media_quotes/observability.py`, `src/media_quotes/cli/logs.py`, `src/media_quotes/db/repository.py` | `.\.venv\Scripts\python.exe -m media_quotes logs --run-id run-1870a49f4880`<br>`.\.venv\Scripts\python.exe -m pytest tests/unit/test_observability.py -v` |
| **10. Processing handles potential errors (network issues, rate limits, timeouts, unavailable content, oversized responses)** | `REQ-13` | `src/media_quotes/fetch.py`, `src/media_quotes/llm_openrouter.py`, `src/media_quotes/orchestration.py` | `.\.venv\Scripts\python.exe -m pytest tests/unit/test_fetch.py tests/unit/test_openrouter.py -v` |
| **11. Process media articles regardless of size or content type (DOM/Trafilatura extraction, JSON/HTML/XML fixtures, quality gate)** | `REQ-14` | `src/media_quotes/extraction.py`, `src/media_quotes/adapters/` | `.\.venv\Scripts\python.exe -m pytest tests/unit/test_extraction.py tests/unit/test_adapters.py tests/unit/test_newspaper_scraping.py -v` |

---

## 11. Testing PostgreSQL Storage & Newspaper Scraping (Hands-On Recipes)

### 11.1 Inspecting All Populated PostgreSQL Tables & Full-Text Search

When `PERSIST_TO_POSTGRES=true` is active in `.env`, `mq run`, `mq eval`, and `mq review` populate `sources`, `pipeline_runs`, `articles` (including `extracted_text`, `raw_blob_ref`, `extraction_method`, and `article_metadata`), `quotes` (including `char_start`, `char_end`, `dom_path`, `speaker`, `attribution_span`), `quote_classifications`, `digests`, `obsolescence_flags`, `audit_events`, `eval_runs`, and `gold_set_labels`.

```powershell
# 1. Seed a review flag, run the pipeline, and run gold-set evaluation
.\.venv\Scripts\python.exe -m media_quotes review --action seed --quote-id Q-20261118-0001
.\.venv\Scripts\python.exe -m media_quotes run --date 2026-11-19
.\.venv\Scripts\python.exe -m media_quotes eval --gold-set synthetic-v1 --date 2026-11-19

# 2. Check row counts across all PostgreSQL tables
docker compose exec postgres psql -U news -d news -c "SELECT 'sources' AS table_name, count(*) FROM sources UNION ALL SELECT 'pipeline_runs', count(*) FROM pipeline_runs UNION ALL SELECT 'articles', count(*) FROM articles UNION ALL SELECT 'quotes', count(*) FROM quotes UNION ALL SELECT 'quote_classifications', count(*) FROM quote_classifications UNION ALL SELECT 'digests', count(*) FROM digests UNION ALL SELECT 'obsolescence_flags', count(*) FROM obsolescence_flags UNION ALL SELECT 'audit_events', count(*) FROM audit_events UNION ALL SELECT 'eval_runs', count(*) FROM eval_runs UNION ALL SELECT 'gold_set_labels', count(*) FROM gold_set_labels;"

# 3. Inspect quote provenance (exact character offsets, DOM path, speaker, attribution span)
docker compose exec postgres psql -U news -d news -c "SELECT canonical_quote_id, speaker, char_start, char_end, dom_path, attribution_span FROM quotes ORDER BY canonical_quote_id;"

# 4. Inspect full extracted article text & JSONB metadata stored in `articles`
docker compose exec postgres psql -U news -d news -c "SELECT source_id, publication_date, extraction_method, length(extracted_text) AS text_chars, article_metadata->>'byline' AS byline FROM articles ORDER BY publication_date, source_id;"

# 5. Query the PostgreSQL GIN Full-Text Search index on `articles` and `quotes`
docker compose exec postgres psql -U news -d news -c "SELECT canonical_quote_id, speaker, quote_text_raw FROM quotes WHERE to_tsvector('english', quote_text_raw) @@ plainto_tsquery('english', 'ceasefire monitoring');"
```

### 11.2 Testing Newspaper Discovery (RSS/Atom, Google News Sitemaps) & Trafilatura HTML Scraping

```powershell
# Run the dedicated newspaper discovery & scraping test suite:
# - RSS 2.0 & Atom feed discovery with date-window filtering
# - Google News XML Sitemap (<news:news>) discovery
# - JSON-LD NewsArticle + OpenGraph metadata extraction
# - Trafilatura 2.x clean body extraction & boilerplate/ad quality gate
# - Multi-format quote & speaker extraction (speaker-first, verb-first, Ukrainian guillemets «...»)
# - Content-addressed BlobCache (var/blobs/<source_id>/<sha256>.html)
.\.venv\Scripts\python.exe -m pytest tests/unit/test_newspaper_scraping.py tests/unit/test_extraction.py tests/unit/test_fetch.py -v
```

### 11.3 Live Feed Fetching of Today's News & Source Ingestion Validation

Discover real article URLs with `--live-feed` first, then copy `articles[].url` into `--live`. A
placeholder slug such as `sample-article-slug` returns `404 Not Found`; the scraper now prints the
exact discovery and local-HTML alternatives when that happens. For manual validation without a
network request, use `--html-file` with `my_article.html` or a fixture under `data/fixtures/`.

```powershell
# 1. Get today's news live from The Guardian (fetches live RSS, top 2 articles, extracts & verifies quotes)
.\.venv\Scripts\python.exe scripts/test_scraper.py --source-id guardian --live-feed --limit 2

# 2. Validate data ingestion health for a single source (registry row, T0 boundary, fixtures, feed)
.\.venv\Scripts\python.exe scripts/test_scraper.py --validate-source guardian
.\.venv\Scripts\python.exe scripts/test_scraper.py --validate-source reuters
.\.venv\Scripts\python.exe scripts/test_scraper.py --validate-source bbc

# 3. Audit data getting readiness across all 28 registered sources
.\.venv\Scripts\python.exe scripts/test_scraper.py --validate-all-sources

# 4. After step 1, copy articles[].url and test ONE real article page.
#    Do NOT use a made-up slug such as sample-article-slug: it returns 404 Not Found.
#    .\.venv\Scripts\python.exe scripts/test_scraper.py --source-id guardian --live --url <paste-real-url>
```

The portable form of the same diagnostic commands is:

```sh
python scripts/test_scraper.py --validate-all-sources
python scripts/test_scraper.py --validate-source <id>
python scripts/test_scraper.py --validate-all-sources --live-routes
```

`--validate-source <id>` (for example `guardian`, `reuters` or `bbc`) prints one source card with
the registry row, `topic_gate`, `language_detection`, `categories`, `translation_readiness`,
`gate_enforcement`, `rights_status` and `source_type`. `--validate-all-sources` prints the same
cards for all 28 sources plus `approved_routes` / `blocked_routes`, `sources_with_fixtures`,
`sources_with_live_feeds`, a source x section `category_coverage` map and a `failures` list. The
command exits `1` when any failure is recorded and `0` otherwise; the JSON report is printed in
both cases.

Live route checks are **off by default**. `--live-routes` is an explicit opt-in and probes only
sources that have both an approved topic route and a usable T0 row. A blocked or unapproved
source is reported as `blocked_not_probed`; its URL is never fetched during validation. Use
`--source-topics-csv PATH` and `--topic-rules PATH` to override the `source_topics_csv` and
`topic_rules_path` settings for a single diagnostic run.

---

## 12. Modular Topic Packs & File-Per-Source Configuration

Sources and monitored topics are managed as standalone JSON files so adding or removing outlets
and switching subjects requires zero Python changes:

- **One file per topic:** `data/topics/<topic_id>.json` (for example `ukraine-war.json` and
  `ai-policy.json`) defines sections, section keywords, stand-in summaries, quick screening
  keywords, and deterministic relevance rules (`anchors`, `supporting_terms`, `exclusions`).
- **One file per source:** `data/sources/<source_id>.json` holds the outlet's T0 rights fields,
  per-topic discovery routes (`routes[]`), and optional `extractor_hints` (including custom
  adapter plugin overrides registered via `@register_source_adapter` / `@register_adapter`).
- **Derived CSV compatibility:** `data/sources.csv` and `data/source_topic_routes.csv` remain
  synchronised mirrors generated from `data/sources/*.json` via `mq sources --action sync-csv`.

### 12.1 Managing Topic Packs (`mq topics` and `--topic`)

```powershell
# List all available topic packs under data/topics/
.\.venv\Scripts\python.exe -m media_quotes topics --action list

# Inspect a topic pack as JSON
.\.venv\Scripts\python.exe -m media_quotes topics --action show --topic ai-policy

# Scaffold a new topic pack (data/topics/<topic_id>.json)
.\.venv\Scripts\python.exe -m media_quotes topics --action init --topic energy-grid --name "Clean Energy & Grid"

# Run the offline pipeline, digest, or export for a specific topic pack
.\.venv\Scripts\python.exe -m media_quotes run --topic ai-policy --date 2026-11-19
.\.venv\Scripts\python.exe -m media_quotes digest --topic ai-policy --date 2026-11-19
.\.venv\Scripts\python.exe -m media_quotes export --topic ai-policy --date 2026-11-19

# Test live RSS discovery and quote verification for a specific topic pack
.\.venv\Scripts\python.exe scripts/test_scraper.py --source-id guardian --topic ai-policy --live-feed --limit 2
```

### 12.2 Adding & Syncing Sources (`mq sources`)

```powershell
# Scaffold a new T0-safe (disabled/pending_counsel_review) source JSON under data/sources/
.\.venv\Scripts\python.exe -m media_quotes sources --action add `
  --source-id examplewire `
  --name "Example Wire" `
  --homepage-url https://examplewire.org `
  --feed-url https://examplewire.org/rss.xml `
  --topic ai-policy

# Regenerate data/sources.csv and data/source_topic_routes.csv from data/sources/*.json
.\.venv\Scripts\python.exe -m media_quotes sources --action sync-csv

# Validate all topic packs, source JSONs, T0 gates, and CSV parity
.\.venv\Scripts\python.exe scripts/validate_data.py
```
