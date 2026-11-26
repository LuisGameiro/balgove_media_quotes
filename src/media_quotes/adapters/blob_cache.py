"""Content-addressed local blob cache and path-confined fixture source."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from ..config import get_settings
from ..processing.export import safe_replace

__all__ = ["BlobCache", "FixtureSource"]

_FIXTURE_SCHEME = "fixture://"
_SAFE_ID = re.compile(r"^[a-z0-9_-]+$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_SAFE_SUFFIX = re.compile(r"^\.?[A-Za-z0-9]{1,16}$")


class BlobCache:
    """Content-addressed local disk cache for scraped articles.

    Stores files under ``var/output/raw/<source_id>/<sha256>.html``.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or get_settings().blob_dir).resolve()

    def store(self, source_id: str, raw: bytes, *, suffix: str = ".html") -> tuple[str, Path]:
        if not _SAFE_ID.fullmatch(source_id):
            raise ValueError(f"invalid source id for blob cache: {source_id!r}")
        digest = hashlib.sha256(raw).hexdigest()
        ext = suffix if suffix.startswith(".") else f".{suffix}"
        if not _SAFE_SUFFIX.fullmatch(ext):
            raise ValueError(f"invalid suffix: {suffix!r}")
        folder = (self.root / source_id).resolve()
        if not folder.is_relative_to(self.root):
            raise ValueError("blob folder escapes root")
        folder.mkdir(parents=True, exist_ok=True)
        target = (folder / f"{digest}{ext}").resolve()
        if not target.is_relative_to(folder):
            raise ValueError("blob target escapes folder")
        if target.exists():
            return digest, target
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=folder,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as f:
            temp_path = Path(f.name)
            f.write(raw)
        try:
            safe_replace(temp_path, target, tolerate_existing=True)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
        return digest, target

    def read(self, source_id: str, digest: str, *, suffix: str = ".html") -> bytes:
        if not _SAFE_ID.fullmatch(source_id) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid source_id or sha256 digest")
        ext = suffix if suffix.startswith(".") else f".{suffix}"
        if not _SAFE_SUFFIX.fullmatch(ext):
            raise ValueError(f"invalid suffix: {suffix!r}")
        target = (self.root / source_id / f"{digest}{ext}").resolve()
        if not target.is_relative_to(self.root) or not target.is_file():
            raise FileNotFoundError(f"blob not found: {source_id}/{digest}{ext}")
        return target.read_bytes()

    def store_meta(self, source_id: str, digest: str, metadata: Mapping[str, Any]) -> Path:
        """Persist fetch-time sidecar metadata (URL, feed title) beside a raw blob."""
        if not _SAFE_ID.fullmatch(source_id) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid source_id or sha256 digest")
        folder = (self.root / source_id).resolve()
        if not folder.is_relative_to(self.root):
            raise ValueError("blob folder escapes root")
        folder.mkdir(parents=True, exist_ok=True)
        target = (folder / f"{digest}.meta").resolve()
        if not target.is_relative_to(folder):
            raise ValueError("blob metadata target escapes folder")
        raw = (json.dumps(dict(metadata), ensure_ascii=False, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=folder,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as f:
            temp_path = Path(f.name)
            f.write(raw)
        try:
            # Metadata is an update, not immutable content: a persistent Windows lock
            # must surface instead of silently keeping a stale sidecar.
            safe_replace(temp_path, target)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
        return target

    def read_meta(self, source_id: str, digest: str) -> dict[str, Any]:
        """Read sidecar metadata for a cached blob, returning {} when absent or invalid."""
        if not _SAFE_ID.fullmatch(source_id) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            return {}
        target = (self.root / source_id / f"{digest}.meta").resolve()
        if not target.is_relative_to(self.root) or not target.is_file():
            return {}
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return cast(dict[str, Any], payload) if isinstance(payload, dict) else {}


class FixtureSource:
    """Injectable, path-confined source of local fixture bytes.

    Refs have the shape ``fixture://<source_id>/<filename>``. Only regular files directly
    inside ``data/fixtures/<source_id>/`` can be read, so a fixture cannot escape its root.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or get_settings().fixtures_dir).resolve()

    def list_refs(self, source_id: str) -> list[str]:
        folder = self._folder(source_id)
        if not folder.is_dir():
            return []
        names = sorted(
            path.name
            for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() in {".html", ".json", ".xml"}
        )
        return [f"{_FIXTURE_SCHEME}{source_id}/{name}" for name in names]

    def read(self, ref: str) -> bytes:
        source_id, name = self._split(ref)
        path = (self._folder(source_id) / name).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("fixture ref escapes the fixtures directory")
        if not path.is_file():
            raise FileNotFoundError(f"fixture not found: {ref}")
        return path.read_bytes()

    def _folder(self, source_id: str) -> Path:
        if not _SAFE_ID.fullmatch(source_id):
            raise ValueError(f"invalid fixture source id: {source_id!r}")
        return self.root / source_id

    @staticmethod
    def _split(ref: str) -> tuple[str, str]:
        if not ref.startswith(_FIXTURE_SCHEME):
            raise ValueError(f"not a fixture ref: {ref}")
        source_id, _, name = ref[len(_FIXTURE_SCHEME) :].partition("/")
        if not _SAFE_ID.fullmatch(source_id) or not _SAFE_NAME.fullmatch(name):
            raise ValueError(f"invalid fixture ref: {ref}")
        return source_id, name
