"""Shared helpers for data validation."""

from __future__ import annotations

import csv
from pathlib import Path


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def check_columns(
    rows: list[dict[str, str]], expected: list[str], label: str, errors: list[str]
) -> None:
    if not rows:
        errors.append(f"{label}: no rows found")
        return
    missing = set(expected) - set(rows[0])
    extra = set(rows[0]) - set(expected)
    if missing:
        errors.append(f"{label}: missing columns {sorted(missing)}")
    if extra:
        errors.append(f"{label}: unexpected columns {sorted(extra)}")
