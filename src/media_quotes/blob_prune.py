"""Operator-driven blob pruning: delete only old blobs with an explicit tombstone.

Blobs are content-addressed, so their only age signal is file mtime. A tombstone is an
operator-supplied manifest of sha256 digests; ``RetentionPolicy.allow_prune`` requires both
the age cutoff and a tombstone before a file may be deleted.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .exceptions import ConfigError
from .risk_controls.models import RetentionPolicy

__all__ = ["load_tombstones", "prune_blobs"]

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_ID = re.compile(r"^[a-z0-9_-]+$")
_SUFFIXES = {".html", ".json", ".xml"}


def load_tombstones(path: Path) -> frozenset[str]:
    """Read a tombstone manifest: either a list of digests or ``{"digests": [...]}``."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"invalid tombstone manifest {path}: {exc}") from exc
    values = payload.get("digests") if isinstance(payload, Mapping) else payload
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ConfigError("tombstone manifest must be a list of sha256 digests")
    digests = frozenset(values)
    if any(not _DIGEST.fullmatch(digest) for digest in digests):
        raise ConfigError("tombstone digests must be 64 lowercase hexadecimal characters")
    return digests


def prune_blobs(
    settings: Settings,
    *,
    today: date,
    tombstones: frozenset[str],
    dry_run: bool,
) -> dict[str, Any]:
    """Report (and optionally delete) blobs that pass the retention policy."""
    policy = RetentionPolicy()
    root = settings.blob_dir.resolve()
    report: dict[str, Any] = {
        "root": str(root),
        "cutoff": policy.prune_cutoffs(today)[0].isoformat(),
        "dry_run": dry_run,
        "scanned": 0,
        "eligible": 0,
        "pruned": 0,
        "kept": 0,
        "deleted": [],
    }
    if not root.is_dir():
        return report

    for source_dir in sorted(root.iterdir()):
        if (
            source_dir.is_symlink()
            or not source_dir.is_dir()
            or not _SOURCE_ID.fullmatch(source_dir.name)
        ):
            continue
        for blob in sorted(source_dir.iterdir()):
            if (
                blob.is_symlink()
                or not blob.is_file()
                or not blob.resolve().is_relative_to(source_dir.resolve())
                or blob.suffix.lower() not in _SUFFIXES
            ):
                continue
            digest = blob.stem
            if not _DIGEST.fullmatch(digest):
                continue
            report["scanned"] += 1
            blob_date = datetime.fromtimestamp(blob.stat().st_mtime, UTC).date()
            if not policy.allow_prune(blob_date, today, tombstone_written=digest in tombstones):
                report["kept"] += 1
                continue
            report["eligible"] += 1
            if not dry_run:
                blob.unlink()
                report["pruned"] += 1
                report["deleted"].append(f"{source_dir.name}/{blob.name}")
    return report
