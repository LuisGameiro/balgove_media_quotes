"""D2 — deterministic OpenRouter Batch API request planning (offline)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from media_quotes.batch import (
    batch_payload,
    build_batch_requests,
    custom_id,
    validate_batch_size,
    write_batch_file,
)
from media_quotes.llm_live import LLMRequest


def req(cache_key: str) -> LLMRequest:
    return LLMRequest(
        decision_type="section_inference",
        cache_key=cache_key,
        input_hash="i",
        model_snapshot="openai/gpt-4o-mini-2024-07-18",
        prompt_version="p1",
        schema_hash="s1",
        system_prompt="Return JSON only",
        user_prompt="<quote>data</quote>",
    )


def test_custom_id_is_stable_and_safe() -> None:
    assert custom_id("k1") == custom_id("k1")
    assert custom_id("k1") != custom_id("k2")
    assert custom_id("k1").startswith("decision-")


def test_batch_requests_use_custom_id_and_strict_schema() -> None:
    requests, mapping = build_batch_requests(
        [req("k1"), req("k2")], model="openai/gpt-4o-mini-2024-07-18"
    )
    assert [item.custom_id for item in requests] == [custom_id("k1"), custom_id("k2")]
    assert mapping[custom_id("k1")] == "k1"
    body = requests[0].body
    assert body["text"]["format"]["strict"] is True
    assert body["model"] == "openai/gpt-4o-mini-2024-07-18"


def test_duplicate_custom_id_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        build_batch_requests([req("k1"), req("k1")], model="openai/gpt-4o-mini-2024-07-18")


def test_batch_payload_uses_openrouter_inline_shape() -> None:
    requests, _ = build_batch_requests([req("k1")], model="openai/gpt-4o-mini-2024-07-18")
    payload = batch_payload(requests, model="openai/gpt-4o-mini-2024-07-18")
    assert payload["endpoint"] == "/v1/responses"
    assert payload["model"] == "openai/gpt-4o-mini-2024-07-18"
    assert payload["requests"][0]["custom_id"] == custom_id("k1")
    assert validate_batch_size(payload) > 0


def test_write_batch_file_is_audit_json(tmp_path: Path) -> None:
    requests, _ = build_batch_requests([req("k1")], model="openai/gpt-4o-mini-2024-07-18")
    path = tmp_path / "batch.json"
    payload = batch_payload(requests, model="openai/gpt-4o-mini-2024-07-18")
    count = write_batch_file(payload, path)
    assert count == 1
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["requests"][0]["custom_id"] == custom_id("k1")
