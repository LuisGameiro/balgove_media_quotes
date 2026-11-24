# One-command quality gate: docs/02-architecture.md and README "Quick start".
.PHONY: help docs-qa format-check format lint typecheck test coverage build smoke check clean

PYTHON ?= python

help:
	@echo "Targets: docs-qa format-check format lint typecheck test coverage build smoke check clean"

docs-qa:
	$(PYTHON) scripts/docs_qa.py

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

# Full-suite coverage gate. The 85% floor lives once in [tool.coverage.report].
coverage:
	$(PYTHON) -m pytest --cov=media_quotes --cov-report=term-missing

build:
	$(PYTHON) -m build

smoke:
	$(PYTHON) -m media_quotes --help >/dev/null
	mq --help >/dev/null

# docs/02-architecture.md: single gate; the CLI smoke exercises the installed `mq` entry point.
check: docs-qa format-check lint typecheck coverage build smoke

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov build dist
	rm -rf src/media_quotes.egg-info
