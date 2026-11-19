#!/usr/bin/env python3
"""Validate data assets. Thin wrapper around media_quotes.validation."""

from __future__ import annotations

import sys

from media_quotes.cli import render_errors
from media_quotes.config import PROJECT_ROOT
from media_quotes.validation import validate_data_dir


def main() -> int:
    return render_errors(validate_data_dir(PROJECT_ROOT / "data"))


if __name__ == "__main__":
    sys.exit(main())
