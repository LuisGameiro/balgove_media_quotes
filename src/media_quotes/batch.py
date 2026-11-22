"""OpenRouter Batch API requests for cost-capped backfills.

OpenRouter's Batch API differs from OpenAI's file-upload flow: requests are sent
inline to POST /api/v1/batches and results are inlined on GET. `custom_id` is
derived from the versioned cache key, which makes results replay-safe even though
provider result order is not guaranteed. No network call happens here.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .exceptions import DuplicateBatchRequestError
from .llm_live import LLMRequest

STRICT = ConfigDict(strict=True, extra="forbid")
MAX_BATCH_REQUESTS = 50_000
MAX_BATCH_BYTES = 200_000_000


class BatchRequest(BaseModel):
    model_config = STRICT

    custom_id: str = Field(min_length=1)
    body: dict[str, Any]


def custom_id(cache_key: str) -> str:
    """Stable, provider-safe id; no raw hashes of user text leak into the id."""
    return f"decision-{hashlib.sha256(cache_key.encode('utf-8')).hexdigest()[:16]}"


def build_batch_requests(
    requests: list[LLMRequest], *, model: str
) -> tuple[list[BatchRequest], dict[str, str]]:
    """Build inline requests and a custom_id -> cache_key map; reject duplicates.

    The map lets a caller write results into decision_cache without trusting the
    provider's ordering.
    """
    seen: dict[str, str] = {}
    out: list[BatchRequest] = []
    for item in requests:
        cid = custom_id(item.cache_key)
        if cid in seen:
            raise DuplicateBatchRequestError(f"duplicate batch custom_id: {cid}")
        seen[cid] = item.cache_key
        out.append(
            BatchRequest(
                custom_id=cid,
                body={
                    "model": model,
                    "input": item.user_prompt,
                    "instructions": item.system_prompt,
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
    if len(out) > MAX_BATCH_REQUESTS:
        raise ValueError(f"batch exceeds {MAX_BATCH_REQUESTS} requests: {len(out)}")
    return out, seen


def batch_payload(
    requests: list[BatchRequest], *, model: str, endpoint: str = "/v1/responses"
) -> dict[str, Any]:
    """Build the POST /api/v1/batches body."""
    return {
        "endpoint": endpoint,
        "model": model,
        "requests": [item.model_dump(mode="json") for item in requests],
    }


def validate_batch_size(payload: dict[str, Any]) -> int:
    """Reject payloads over OpenRouter's 200 MB batch limit; return byte size."""
    encoded = __import__("json").dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_BATCH_BYTES:
        raise ValueError(f"batch payload too large: {len(encoded)} > {MAX_BATCH_BYTES}")
    return len(encoded)


def write_batch_file(payload: dict[str, Any], path: Path) -> int:
    """Write the request body for audit/replay; returns the number of requests."""
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)
    return len(payload.get("requests", []))
