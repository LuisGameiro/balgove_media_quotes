"""Blob pruning requires both age and an explicit operator tombstone."""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from media_quotes.blob_prune import load_tombstones, prune_blobs
from media_quotes.config import Settings
from media_quotes.exceptions import ConfigError

TODAY = date(2026, 11, 19)
OLD_TOMBSTONED = "a" * 64
OLD_KEPT = "b" * 64
RECENT_TOMBSTONED = "c" * 64


def _settings(root: Path) -> Settings:
    return Settings(_env_file=None, blob_dir=root / "raw", report_date=TODAY)


def _blob(root: Path, digest: str, days_old: int) -> Path:
    folder = root / "raw" / "sample-source"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{digest}.html"
    path.write_text("body", encoding="utf-8")
    stamp = (datetime(2026, 11, 19, 12, tzinfo=UTC) - timedelta(days=days_old)).timestamp()
    os.utime(path, (stamp, stamp))
    return path


def _seeded_blobs(root: Path) -> tuple[Path, Path, Path]:
    return (
        _blob(root, OLD_TOMBSTONED, 365),
        _blob(root, OLD_KEPT, 365),
        _blob(root, RECENT_TOMBSTONED, 5),
    )


def test_dry_run_reports_without_deleting(tmp_path: Path) -> None:
    old, kept, recent = _seeded_blobs(tmp_path)
    report = prune_blobs(
        _settings(tmp_path),
        today=TODAY,
        tombstones=frozenset({OLD_TOMBSTONED, RECENT_TOMBSTONED}),
        dry_run=True,
    )

    assert (report["scanned"], report["eligible"], report["pruned"], report["kept"]) == (3, 1, 0, 2)
    assert report["cutoff"] == "2026-05-23"
    assert all(path.is_file() for path in (old, kept, recent))


def test_execute_deletes_only_old_tombstoned_blob(tmp_path: Path) -> None:
    old, kept, recent = _seeded_blobs(tmp_path)
    report = prune_blobs(
        _settings(tmp_path),
        today=TODAY,
        tombstones=frozenset({OLD_TOMBSTONED, RECENT_TOMBSTONED}),
        dry_run=False,
    )

    assert report["pruned"] == 1
    assert report["deleted"] == [f"sample-source/{OLD_TOMBSTONED}.html"]
    assert not old.exists()
    assert kept.is_file() and recent.is_file()


def test_load_tombstones_accepts_list_and_object(tmp_path: Path) -> None:
    plain = tmp_path / "plain.json"
    plain.write_text(json.dumps([OLD_TOMBSTONED]), encoding="utf-8")
    wrapped = tmp_path / "wrapped.json"
    wrapped.write_text(json.dumps({"digests": [OLD_TOMBSTONED]}), encoding="utf-8")

    assert load_tombstones(plain) == load_tombstones(wrapped) == frozenset({OLD_TOMBSTONED})


def test_load_tombstones_rejects_bad_digest(tmp_path: Path) -> None:
    manifest = tmp_path / "bad.json"
    manifest.write_text(json.dumps(["not-a-sha256"]), encoding="utf-8")
    with pytest.raises(ConfigError, match="64 lowercase hexadecimal"):
        load_tombstones(manifest)


def test_cli_prune_is_dry_run_until_execute(tmp_path: Path, monkeypatch: Any) -> None:
    from media_quotes.cli import app

    old, _, _ = _seeded_blobs(tmp_path)
    manifest = tmp_path / "tombstones.json"
    manifest.write_text(json.dumps([OLD_TOMBSTONED]), encoding="utf-8")
    monkeypatch.setattr(app, "get_settings", lambda: _settings(tmp_path))

    assert app.main(["blob-prune", "--tombstones", str(manifest)]) == 0
    assert old.is_file()
    assert app.main(["blob-prune", "--tombstones", str(manifest), "--execute", "--dry-run"]) == 0
    assert old.is_file()  # the shared safety flag still wins
    assert app.main(["blob-prune", "--tombstones", str(manifest), "--execute"]) == 0
    assert not old.exists()


def test_meta_unlink_failure_does_not_count_deleted_blob_as_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old, _, _ = _seeded_blobs(tmp_path)
    meta = old.with_suffix(".meta")
    meta.write_text("{}", encoding="utf-8")

    real_unlink = Path.unlink

    def fail_on_meta(path: Path) -> None:
        if path.suffix == ".meta":
            raise OSError("meta file locked")
        real_unlink(path)

    monkeypatch.setattr(Path, "unlink", fail_on_meta)
    report = prune_blobs(
        _settings(tmp_path),
        today=TODAY,
        tombstones=frozenset({OLD_TOMBSTONED}),
        dry_run=False,
    )
    assert report["pruned"] == 1
    assert report["kept"] == 2  # only the two policy-ineligible blobs
    assert not old.exists()
    assert meta.is_file()
