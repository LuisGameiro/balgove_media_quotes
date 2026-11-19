# One-command quality gate: docs/02 §7 (CHK-10..13) and README "Quick start".
.PHONY: help docs-qa format-check format lint typecheck test build smoke check clean

PYTHON ?= python

help:
	@echo "Targets: docs-qa format-check format lint typecheck test build smoke check clean"

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

test:
	$(PYTHON) -m pytest --cov=media_quotes --cov-fail-under=85

build:
	$(PYTHON) -m build

smoke:
	$(PYTHON) -m media_quotes --help >/dev/null
	mq --help >/dev/null

# docs/02 §7: single gate; the CLI smoke exercises the installed `mq` entry point.
check: docs-qa format-check lint typecheck test build smoke

clean:
	rm -rf .pytest_cache .ruff_cache htmlcov build dist
	rm -rf src/media_quotes.egg-info
