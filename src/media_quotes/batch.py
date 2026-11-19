"""D2 — Batch API planning for backfills (offline, deterministic).

Builds the JSONL request file the provider Batch API expects: one request per cached decision,
identified by a stable `custom_id` derived from the cache key. `custom_id` is what makes
backfill output replay-safe — provider result order is not guaranteed. No network here.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .llm_live import LLMRequest

STRICT = ConfigDict(strict=True, extra="forbid")


class BatchRequest(BaseModel):
    model_config = STRICT

    custom_id: str = Field(min_length=1)
    method: str = "POST"
    url: str = "/v1/responses"
    body: dict[str, Any]


def custom_id(cache_key: str) -> str:
    """Stable, provider-safe id; no raw hashes of user text leak into the id."""
    return f"decision-{hashlib.sha256(cache_key.encode()).hexdigest()[:16]}"


def build_batch_requests(requests: list[LLMRequest]) -> list[BatchRequest]:
    """One batch request per LLM decision; rejects duplicate ids (idempotency contract)."""
    seen: set[str] = set()
    out: list[BatchRequest] = []
    for item in requests:
        cid = custom_id(item.cache_key)
        if cid in seen:
            raise ValueError(f"duplicate batch custom_id: {cid}")
        seen.add(cid)
        out.append(
            BatchRequest(
                custom_id=cid,
                body={
                    "model": item.model_snapshot,
                    "instructions": item.system_prompt,
                    "input": item.user_prompt,
                    "temperature": float(item.decoding_params.get("temperature", 0.0)),
                    "text": {
                        "format": {
                            "type": "json_schema",
                            "name": "structured_decision",
                            "strict": True,
                        }
                    },
                },
            )
        )
    return out


def write_batch_file(requests: list[BatchRequest], path: Path) -> int:
    """Write provider JSONL; returns the number of lines. UTF-8, no BOM, newline-terminated."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for request in requests:
            handle.write(request.model_dump_json())
            handle.write("\n")
    return len(requests)
