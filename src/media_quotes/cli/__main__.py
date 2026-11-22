"""python -m media_quotes.cli entry point."""

from __future__ import annotations

import sys

from . import cli_entry

if __name__ == "__main__":
    sys.exit(cli_entry())
