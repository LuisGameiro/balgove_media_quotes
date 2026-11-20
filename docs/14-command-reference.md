# 14 — Command reference

Every command available in this repository, with the exact flags that exist in code.
PowerShell is the primary shell; `check` is the full local gate (see root README / §8 below).
Commands come from: `pyproject.toml` (`[project.scripts]`, dev tools), `Makefile`, `scripts/`,
`src/media_quotes/cli/app.py`, `docker-compose.yml`, and the optional Dagster extra.

**Notation:** run from the repository root `C:\Users\lgame\Desktop\balgoveinnovations` unless a
different directory is shown. `mq` is installed as `mq = "media_quotes.cli:main"`; if it is not
on PATH, use `python -m media_quotes.cli` instead — both run the same parser.

---

## 1. Environment setup (one-time)

```powershell
Copy-Item .env.example .env          # then EDIT .env; never commit real secrets
py -m pip install uv                 # only if uv is missing
uv sync --frozen --group dev         # installs the locked dev dependency group
uv sync --frozen --all-extras        # full install (Dagster, Streamlit, extraction, LLM, …)
docker compose up -d --wait          # local PostgreSQL 16 service
python -m media_quotes.bootstrap     # Alembic upgrade head (0001→0002→0003) + sources.csv sync
```

Existing venv alternative (no uv): `python -m pip install -e ".[dev,test]"`.
Windows shell helper (one-time, adds `check` to both PowerShell profiles):
```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_check_profile.ps1
```

---

## 2. Quality gate

| Purpose | PowerShell / cross-platform |
|---|---|
| Full gate | `check` (profile function) or `powershell -ExecutionPolicy Bypass -File scripts/check.ps1` or `make check` (Git Bash / WSL with make installed) |
| Docs QA only | `python scripts/docs_qa.py` / `make docs-qa` |
| Format check | `python -m ruff format --check .` / `make format-check` |
| Auto-format | `python -m ruff format .` / `make format` |
| Lint | `python -m ruff check .` / `make lint` |
| Strict types | `python -m mypy --strict src/` / `make typecheck` |
| Tests + coverage | `python -m pytest --cov=media_quotes --cov-fail-under=85` / `make test` |
| Build wheel + sdist | `python -m build` / `make build` |
| CLI smoke | `python -m media_quotes --help` and `python -m media_quotes.cli --help` / `make smoke` |
| Data-asset validation | `python scripts/validate_data.py` |
| Make help / clean | `make help`, `make clean` |

---

## 3. CLI (`mq`) — all commands

### 3.1 Shared flags (available on every subcommand)

`--offline` (fixture replay, no network) · `--dry-run` (plan, write nothing) ·
`--date YYYY-MM-DD` (report date; default from `.env`/today) · `--run-id ID` ·
`--trace-id ID` · `--out PATH` · `--verbose`

Exit codes: `0` success · `1` validation/threshold failure · `2` partial success with
coverage gaps · `3` config / T0-gate / budget error (mapped in `cli/app.py`).

### 3.2 Core commands

| Command | Purpose | Command-specific flags |
|---|---|---|
| `mq doctor` | Validate config, registry/T0 gate, schema, version manifest; no network | — |
| `mq validate` | Alias for `doctor` | — |
| `mq bootstrap` | Alembic `upgrade head` + sync `sources.csv` to Postgres | — |
| `mq run` | Full offline fixture-replay pipeline → JSONL store, digest, logs, audit | — (persists to Postgres only when `PERSIST_TO_POSTGRES=true`) |
| `mq audit` | Deterministic audit report (JSON + MD) for a run/date | `--run-id`, `--out` |
| `mq digest` | Regenerate `digests/YYYY-MM-DD.md` from the local store | `--out` |
| `mq logs` | Correlated structured logs for a run | **requires `--run-id`**; optional `--trace-id` |
| `mq eval` | Gold-set metrics (recall/span/κ etc.) | `--gold-set VERSION` (default `synthetic-v1`); persists to Postgres when enabled |
| `mq sources` | Print the registry and T0 rights state | `--tier 1|2|3|4`, `--rights-status STATUS` |
| `mq version` | Print the version manifest | — |
| `mq export` | Derived JSONL export | `--out PATH` |
| `mq backfill` | Bounded offline backfill estimate + budget gate | `--from YYYY-MM-DD`, `--to YYYY-MM-DD`, `--max-cost N` |
| `mq dashboard` | Streamlit guidance / launch when installed | — |
| `mq review` | Obsolescence review queue | `--action list|decide`, `--quote-id ID`, `--verdict confirmed|rejected|escalated`, `--reason-code TEXT` |
| `mq retention` | Retention + lock readiness plan; no DDL/rows touched | uses shared `--dry-run` |
| `mq restore-drill` | Compare two derived exports (quote id + content hash) | `--reference PATH` (otherwise the two newest exports) |

Examples:
```powershell
python -m media_quotes run --dry-run --date 2026-11-19
python -m media_quotes audit --date 2026-11-19 --run-id run-3300d30f1c8e
python -m media_quotes logs --run-id run-3300d30f1c8e
python -m media_quotes eval --gold-set synthetic-v1 --date 2026-11-19
python -m media_quotes backfill --from 2026-10-01 --to 2026-11-19 --max-cost 100 --dry-run
python -m media_quotes sources --tier 2
python -m media_quotes review --action list
python -m media_quotes review --action decide --quote-id Q-20261118-0001 --verdict confirmed --reason-code first-hand-supersession
python -m media_quotes retention --dry-run
python -m media_quotes restore-drill --reference var/export/2026-11-18.jsonl
```

---

## 4. Scripts in `scripts/`

| Script | Purpose | Invocation |
|---|---|---|
| `docs_qa.py` | Documentation consistency checks (CHK-08) | `python scripts/docs_qa.py [--root PATH]` |
| `validate_data.py` | Registry / schema / data-asset validation | `python scripts/validate_data.py` |
| `check.ps1` | Windows full gate | `powershell -ExecutionPolicy Bypass -File scripts/check.ps1` |
| `install_check_profile.ps1` | Installs the `check` PowerShell function (one-time) | `powershell -ExecutionPolicy Bypass -File scripts/install_check_profile.ps1` |
| `generate_gold_fixtures.py` | Generate synthetic gold-set/fixture data | `python scripts/generate_gold_fixtures.py --help` (check current flags before use) |

---

## 5. Python module entry points

```powershell
python -m media_quotes --help           # top-level entry point (same CLI)
python -m media_quotes.cli --help       # explicit package entry point
python -m media_quotes.bootstrap        # Alembic migrations + registry sync
python -m pytest                        # full test suite (with coverage from pyproject)
python -m ruff check .                  # lint
python -m mypy --strict src/            # type-check
python -m build                         # wheel + sdist into dist/
```

---

## 6. Docker Compose

```powershell
docker compose up -d --wait   # start local Postgres
docker compose ps             # service status
docker compose logs -f postgres  # service name from docker-compose.yml
docker compose down           # stop; add -v to remove volumes (destroys local DB)
```

---

## 7. Dagster (orchestration)

```powershell
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

## 8. `.env` reference (local only; gitignored)

| Variable | Default / purpose |
|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | local Compose credentials (local-only) |
| `DATABASE_URL` | SQLAlchemy URL (e.g. `postgresql+psycopg://news:news@127.0.0.1:5432/news`) |
| `LOG_LEVEL`, `LOG_JSON`, `OTEL_ENABLED` | logging / tracing toggles |
| `LLM_PROVIDER` | `local` (offline, default) or `openrouter` (live section inference only) |
| `LLM_MODEL_SNAPSHOT` | pinned model id; required for `openrouter`; never `unset`/`latest` |
| `OPENROUTER_API_KEY` | required for `openrouter`; **rotate immediately if ever exposed** |
| `OPENROUTER_BASE_URL`, `OPENROUTER_APP_URL`, `OPENROUTER_APP_TITLE`, `OPENROUTER_TIMEOUT_SECONDS`, `OPENROUTER_MAX_RETRIES` | optional OpenRouter settings, all have defaults |
| `PERSIST_TO_POSTGRES` | `true` enables Postgres writes in `mq run` / `mq eval` / Dagster; default `false` (file-only) |
| `REPORT_DATE`, `BACKFILL_START`, `REPORT_WINDOW_DAYS` | date controls (`--date` overrides at the CLI) |
| `MAX_QUOTE_WORDS`, `ATTRIBUTION_FORMAT`, `LICENCE_TERRITORY`, `EXTERNAL_PUBLICATION` | DEC-10 rights limits; renderer enforces them |

Provider selection is env-only (no CLI flag). Live inference changes only the 1–2 sentence
section summaries — never verification, quote selection or the T0 gate.

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
dagster dev -m media_quotes.orchestration
```
In the Dagster UI (http://localhost:3000): select asset **`daily_quotes_digest`**, partition
**`2026-11-19`**, click *Materialize*. Expect a successful run; metadata includes
`status`, `quotes`, `coverage`, `gaps`, `rejected`. The asset calls the same offline `_collect`
logic and, with `PERSIST_TO_POSTGRES=true`, writes a `pipeline_runs` row plus audit events.
If Dagster is missing: `uv sync --frozen --all-extras` (or
`python -m pip install -e ".[orchestration]"`). Ignore the local `.tmp_dagster_home_*`
runtime directory (gitignored under `var/dagster/`).

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
