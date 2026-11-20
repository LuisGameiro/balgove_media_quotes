# 14 — Command and script reference

Single-source cheatsheet for running, verifying, testing, and developing the Media
Quotes pipeline on Windows PowerShell. All paths and commands assume PowerShell from
the repository root.

---

## 1. Quality gate and CI (run first, run always)

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check.ps1
```

Or add to your PowerShell profile (`$PROFILE`) for the one-word shortcut:
```powershell
function check { powershell -ExecutionPolicy Bypass -File (Join-Path (Get-Location) "scripts\check.ps1") }
```

Steps executed:
1. `docs_qa.py` — 41 requirement/cross-doc assertions.
2. `ruff format --check src tests scripts` — zero diffs.
3. `ruff check src tests scripts` — zero lint warnings.
4. `mypy src tests scripts` — strict type checking.
5. `pytest --cov=media_quotes --cov-fail-under=85` — 210+ tests, >=85% coverage.
6. `python -m build` — wheel and sdist into `dist/`.
7. `smoke_test.py` — clean venv install from built wheel + CLI sanity check.

---

## 2. CLI commands (`media_quotes.cli` / `mq`)

The CLI entrypoint is `python -m media_quotes` (or `mq` if installed in editable mode).

| Command | Purpose | Typical invocation |
|---|---|---|
| `run` | Daily collection pipeline (discover, fetch, verify, dedupe, digest, export) | `python -m media_quotes run --offline --date 2026-11-19` |
| `run` (dry run) | Verify pipeline without writing files | `python -m media_quotes run --offline --dry-run --date 2026-11-19` |
| `eval` | Evaluate synthetic gold set against precision/recall contracts | `python -m media_quotes eval --gold-set synthetic-v1 --date 2026-11-19` |
| `eval` (splits) | Evaluate on a specific split (`train`, `dev`, `test`, or `all`) | `python -m media_quotes eval --gold-set synthetic-v1 --split test --date 2026-11-19` |
| `audit` | View audit trail and coverage report | `python -m media_quotes audit --date 2026-11-19` |
| `logs` | Stream structured JSON logs with trace correlation | `python -m media_quotes logs --run-id <run-id>` |
| `review` | Human-in-the-loop obsolescence review queue | `python -m media_quotes review --action list` |
| `retention` | Check/apply retention policy on partitions and blobs | `python -m media_quotes retention --dry-run` |
| `restore-drill` | Verify backup export against a reference export | `python -m media_quotes restore-drill --reference var/export/quotes-2026-11-18.jsonl` |
| `sources` | List configured news sources and tier status | `python -m media_quotes sources` |
| `sources` (filtered) | Filter by tier | `python -m media_quotes sources --tier 1` |
| `version` | Print package version, commit hash, and schema version | `python -m media_quotes version` |
| `doctor` | Self-diagnostic check (DB connection, paths, T0 rights gate) | `python -m media_quotes doctor` |
| `backfill` | Replay historical partitions with cost guardrails | `python -m media_quotes backfill --from 2026-10-01 --to 2026-11-19 --dry-run` |

---

## 3. Database bootstrap and migrations

```powershell
# 1. Start local Postgres container
docker compose up -d --wait

# 2. Run schema bootstrap (applies schema.sql + seed data)
python -m media_quotes.bootstrap

# 3. Verify database health and schema version
python -m media_quotes doctor
```

---

## 4. Evaluation and gold set

```powershell
# Full gold-set evaluation (synthetic-v1: 70 articles across 6 sources)
python -m media_quotes eval --gold-set synthetic-v1 --date 2026-11-19

# Evaluate individual split
python -m media_quotes eval --gold-set synthetic-v1 --split train --date 2026-11-19
python -m media_quotes eval --gold-set synthetic-v1 --split dev --date 2026-11-19
python -m media_quotes eval --gold-set synthetic-v1 --split test --date 2026-11-19
```

Contract thresholds enforced:
- Precision: $\ge 0.98$
- Recall: $\ge 0.95$
- False quote rate: $= 0$
- Character offset accuracy: $1.0$ (exact)

---

## 5. Offline replay and restore drills

```powershell
# Step 1: Run day 1
python -m media_quotes run --offline --date 2026-11-18

# Step 2: Run day 2
python -m media_quotes run --offline --date 2026-11-19

# Step 3: Run restore drill comparing the two daily exports
python -m media_quotes restore-drill --reference var/export/quotes-2026-11-18.jsonl
```

---

## 6. Development and testing shortcuts

```powershell
# Run all unit tests
pytest tests/unit/ -v

# Run OpenRouter / LLM gateway unit tests (no network calls made)
pytest tests/unit/test_openrouter.py tests/unit/test_llm_live.py -v

# Run integration tests (requires Postgres)
pytest tests/integration/ -v

# Format and lint check only
ruff check src tests scripts
ruff format --check src tests scripts

# Type check only
mypy src tests scripts

# Docs QA check only
python scripts/docs_qa.py

# Validate schema of data files
python scripts/validate_data.py
```

---

## 7. Dagster (orchestration)

```powershell
$env:DAGSTER_HOME = "$PWD\var\dagster"
dagster dev -m media_quotes.orchestration
```
Definitions: `daily_quotes_digest`, `DailyPartitionsDefinition(start_date="2026-10-01")`,
`code_version=c4-daily-pipeline-v1`. Materialise a partition in the Dagster UI
(e.g. `2026-11-19`). With `PERSIST_TO_POSTGRES=true` the asset writes `pipeline_runs`,
`audit_events` and verified quotes to Postgres. Keep it disabled for live sources until the T0
rights gate is satisfied for those outlets.

`dagster` is an optional extra — install with `uv sync --frozen --all-extras` (or
`python -m pip install -e ".[orchestration]"`). CI does **not** require a running Dagster daemon.

---

## 8. Docker operations

```powershell
docker compose up -d --wait   # start local Postgres
docker compose ps             # service status
docker compose logs -f postgres  # service name from docker-compose.yml
docker compose down           # stop; add -v to remove volumes (destroys local DB)
```

---

## 9. Step-by-step operational runbook (PowerShell)

Prerequisites already done: activated `(media-quotes)` venv, minimal `.env` (§8),
`check` function installed (§1). Run from the repository root.
Each phase ends with a pass/fail signal; **do not continue past a failed phase**.

### Phase 0 — full quality gate

```powershell
check
```
Expect: docs QA 41/41, format/lint/mypy clean, 210+ tests at >=85% coverage, wheel+sdist
built, CLI smoke OK, ending in `ALL CHECKS PASSED`. If it fails, fix and re-run before
anything else. Equivalent without the profile function:
`powershell -ExecutionPolicy Bypass -File scripts/check.ps1`.

### Phase 1 — dry-run (already working; confirm again after any change)

```powershell
python -m media_quotes run --dry-run --date 2026-11-19
python -m media_quotes backfill --from 2026-10-01 --to 2026-11-19 --max-cost 100 --dry-run
python -m media_quotes doctor
python -m media_quotes version
python scripts/validate_data.py
```
Expect: no files written by `run --dry-run`; a line like `Run run-…: N quote(s), M gap(s)`;
`doctor` prints `T0 gate: 2 mandatory source(s) blocked` (Reuters/BBC — correct); backfill
prints estimate, cache-hit summary and budget note; `validate_data.py` says all data valid.

### Phase 2 — real offline run (file outputs, no DB, no network)

```powershell
python -m media_quotes run --date 2026-11-19
python -m media_quotes audit --date 2026-11-19
python -m media_quotes eval --gold-set synthetic-v1 --date 2026-11-19
python -m media_quotes review --action list
python -m media_quotes logs --run-id <run-id printed by run>
python -m media_quotes sources
python -m media_quotes retention --dry-run
```
Expect: `run` writes `var/export/2026-11-19.jsonl`, `digests/2026-11-19.md`,
`var/logs/<run-id>.jsonl`, `var/audit/*`; prints `COVERAGE GAP: reuters/bbc … rights_blocked`.
`audit` writes JSON+MD; `eval` prints all thresholds with `"pass": true` on the synthetic gold
set; `review --action list` prints `[]` (no pending flags yet); `logs` prints structured JSON
with `trace_id`; `sources` shows all rows disabled. These exit `2` when gaps exist — expected.

### Phase 3 — local Postgres + persistence

`.env` already has `PERSIST_TO_POSTGRES=true` and `LLM_PROVIDER=local`. Run:

```powershell
docker compose up -d --wait
docker compose ps                       # service "postgres" should be healthy
python -m media_quotes.bootstrap        # Alembic 0001..0003 + sources.csv sync
python -m media_quotes doctor
python -m media_quotes run --date 2026-11-19
python -m media_quotes eval --gold-set synthetic-v1 --date 2026-11-19
```
Expect: bootstrap prints applied SQL statement count and synced source count; `run` prints
`Postgres: N article(s), M quote(s) persisted`; `eval` stores an `eval_runs` row
(migration 0002). `doctor` still shows 2 mandatory sources blocked — correct until Track B
licences exist.
Optional: `docker compose logs -f postgres` to inspect DB startup.

### Phase 4 — Dagster (the next genuinely new test)

```powershell
$env:DAGSTER_HOME = "$PWD\var\dagster"
dagster dev -m media_quotes.orchestration
```
In the Dagster UI (http://localhost:3000): select asset **`daily_quotes_digest`**, partition
**`2026-11-19`**, click *Materialize*. Expect a successful run; metadata includes
`status`, `quotes`, `coverage`, `gaps`, `rejected`. The asset calls the same offline `_collect`
logic and, with `PERSIST_TO_POSTGRES=true`, writes a `pipeline_runs` row plus audit events.
If Dagster is missing: `uv sync --frozen --all-extras` (or
`python -m pip install -e ".[orchestration]"`).

### Phase 5 — Track A rights (stakeholder action)

Only after written confirmation for `guardian_api`, `gdelt`, `suspilne`, `kyivindependent`:
1. Edit `data/sources.csv`: for those four rows set `rights_status=api_permitted` (or
   `license_held`), fill `license_reference` with the real consent ID, set the approved
   `access_mode`, `enabled=true`, update `notes`.
2. Update `docs/07-sources.md` and the `DEC-14` / `08-traceability.md` records.
3. Re-sync and verify:

```powershell
python -m media_quotes.bootstrap
python -m media_quotes doctor
python -m media_quotes sources --tier 2
python -m media_quotes sources --tier 3
python -m media_quotes run --date 2026-11-19
```
Expect fewer blocked sources in `doctor`; GDELT remains **discovery only**; Reuters/BBC (and
other paywalls/state-controlled outlets) stay disabled until their own licences are signed.

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
Expect only the 1–2 sentence section summaries to change. Quote selection, deterministic
verification, rights limits and the T0 gate are untouched. To return offline, set
`LLM_PROVIDER=local` again. Use `LLM_MODEL_SNAPSHOT=openrouter/free` only if you accept a
routing alias that can change over time (weaker replay guarantee).

### Phase 7 — close the accepted risks with production evidence

- RSK-07: `mq eval` obsolete precision >= 0.90 from held-out human labels; review SLA met.
- RSK-08: three real backfills within +/-20% of the estimate; `--max-cost` breach exits `3`.
- RSK-11: load test with transactions < 60s; `mq retention`/`mq restore-drill` pass.

Then mark RSK-07/08/11 `solved` in `docs/04` and `docs/08` (keep the ID tokens so
`docs_qa.py` stays green), and record the verification in `docs/10`. Full background:
`docs/12-implementation-plan.md`.
