# One-command quality gate: docs/02-architecture.md and README "Quick start".
.PHONY: help validate-data format-check format lint typecheck test coverage build smoke check clean

PYTHON ?= python

help:
	@echo "Targets: validate-data format-check format lint typecheck test coverage build smoke check clean"

validate-data:
	$(PYTHON) scripts/ci/validate_data.py

format-check:
	$(PYTHON) -m ruff format --check .

format:
	$(PYTHON) -m ruff format .

lint:
	$(PYTHON) -m ruff check .

typecheck:
	$(PYTHON) -m mypy --strict src/

# Fast local/full-suite run. Targeted runs stay fast; coverage is enforced by `make coverage`.
test:
	$(PYTHON) -m pytest

# Coverage gate over the default pytest selection: the `integration` marker is deselected by
# addopts in pyproject.toml (no live Postgres/feed). The 85% floor lives in [tool.coverage.report].
coverage:
	$(PYTHON) -m pytest --cov=media_quotes --cov-report=term-missing

build:
	$(PYTHON) -m build

smoke:
	$(PYTHON) -m media_quotes --help >/dev/null
	$(PYTHON) -m media_quotes.cli --help >/dev/null
	@if command -v mq >/dev/null 2>&1; then mq --help >/dev/null; fi

# docs/02-architecture.md: single gate; the CLI smoke exercises both module and installed entry points.
check: validate-data format-check lint typecheck coverage build smoke

# POSIX shell (Git Bash on Windows) is required for `clean`; `make check` needs no shell extras.
clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov build dist
	rm -rf src/media_quotes.egg-info
