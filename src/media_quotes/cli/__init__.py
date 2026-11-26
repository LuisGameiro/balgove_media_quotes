"""Cohesive CLI package for local development, CI and blocked/degraded replay.

Commands are offline by default: fixture replay, local JSONL/Markdown stores and deterministic
policy functions. The explicit ``fetch`` command opens a rights-gated socket only when run
without ``--dry-run``.

Public surface: ``main``, ``cli_entry`` and the exit-code constants/helpers below.
Private handler names are frozen out of this package; import them from their defining modules.
"""

from __future__ import annotations

from .app import cli_entry, main
from .common import DATA_DIR, EXIT_CONFIG, EXIT_FAILURE, EXIT_GAPS, EXIT_OK, render_errors

__all__ = [
    "DATA_DIR",
    "EXIT_CONFIG",
    "EXIT_FAILURE",
    "EXIT_GAPS",
    "EXIT_OK",
    "cli_entry",
    "main",
    "render_errors",
]
