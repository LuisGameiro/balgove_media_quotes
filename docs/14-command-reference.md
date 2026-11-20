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
docker compose logs -f db     # follow DB logs (service named in docker-compose.yml)
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

## 9. Recommended operational order (where Dagster fits)

1. `check` — full gate green.
2. `python -m media_quotes run --dry-run --date 2026-11-19` — already working.
3. Real offline run: `python -m media_quotes run --date 2026-11-19`, then `audit`, `eval`, `review --action list`.
4. Local Postgres: `docker compose up -d --wait` → `python -m media_quotes.bootstrap` → `doctor` → rerun `run` with `PERSIST_TO_POSTGRES=true`.
5. **Dagster test:** `dagster dev -m media_quotes.orchestration`, materialise `2026-11-19`; verify `pipeline_runs` / audit rows in Postgres.
6. Track A rights: apply your written confirmations to `data/sources.csv` (see `docs/07`), re-bootstrap, re-run.
7. Live LLM (optional): set `LLM_PROVIDER=openrouter` + rotated `OPENROUTER_API_KEY` + pinned `LLM_MODEL_SNAPSHOT` in `.env`; rerun a date and compare section inferences only.
8. Close RSK-07/08/11 with real backfill/load-test/restore evidence (see `docs/10` and `docs/12` §8.6).

Full background and phase-by-phase status live in `docs/12-implementation-plan.md`.
