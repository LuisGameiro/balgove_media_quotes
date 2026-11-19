"""Content-addressed LLM decision-cache key (CHK-16)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def build_cache_key(
    *,
    source_hash: str,
    extractor_version: str,
    prompt_version: str,
    schema_hash: str,
    model_snapshot: str,
    decoding_params: Mapping[str, Any],
) -> str:
    payload = {
        "source_hash": source_hash,
        "extractor_version": extractor_version,
        "prompt_version": prompt_version,
        "schema_hash": schema_hash,
        "model_snapshot": model_snapshot,
        "decoding_params": dict(decoding_params),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = ["build_cache_key"]
