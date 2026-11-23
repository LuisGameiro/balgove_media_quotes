"""Cost-capped OpenRouter batch backfill command (dry-run by default)."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..batch import (
    batch_payload,
    build_batch_requests,
    custom_id,
    validate_batch_size,
    write_batch_file,
)
from ..batch_runner import OpenRouterBatchClient
from ..config import Settings
from ..exceptions import ConfigError
from ..llm_live import LLMRequest
from ..risk_controls.budget import budget_gate
from ..risk_controls.models import BackfillEstimate
from .common import _PRICE_PER_CALL, EXIT_FAILURE, EXIT_OK


def _load_requests(path: Path) -> list[LLMRequest]:
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ConfigError("batch requests file must be a JSON array of LLMRequest objects")
    return [LLMRequest.model_validate(item) for item in payload]


def _extract_text(result: dict[str, Any]) -> str | None:
    """Pull output text from an inlined OpenRouter batch result."""
    response = result.get("response")
    if not isinstance(response, dict):
        return None
    body = response.get("body")
    if not isinstance(body, dict):
        return None
    # Responses API (the default endpoint): output[].content[].text.
    pieces: list[str] = []
    for item in body.get("output") or []:
        if not isinstance(item, dict):
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "output_text":
                text = part.get("text")
                if isinstance(text, str):
                    pieces.append(text)
    if pieces:
        return "".join(pieces)
    # Chat Completions shape, for operators who override BATCH_ENDPOINT.
    text = body.get("output_text")
    if isinstance(text, str):
        return str(text)
    choices = body.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return str(message["content"])
    return None


def _require_matching_model(requests: list[LLMRequest], model: str) -> None:
    """Cache keys encode the model, so a batch must run under that same model."""
    if any(item.model_snapshot != model for item in requests):
        raise ConfigError(
            "every request model_snapshot must equal BATCH_MODEL_SNAPSHOT; "
            "rebuild the requests file with the batch model"
        )


def _apply_results(
    settings: Settings,
    results: list[dict[str, Any]],
    requests: list[LLMRequest],
) -> tuple[int, int]:
    from ..db.repository import DecisionCacheEntry, upsert_decision_cache
    from ..pipeline import _default_session_context

    by_cache_key = {custom_id(item.cache_key): item for item in requests}
    applied = 0
    unmatched = 0
    with _default_session_context(settings) as session:
        for result in results:
            request = by_cache_key.get(str(result.get("custom_id") or ""))
            text = _extract_text(result)
            if request is None:
                unmatched += 1
                continue
            if text is None:
                continue  # failed provider result, not a mapping mismatch
            entry = DecisionCacheEntry(
                cache_key=request.cache_key,
                model_snapshot=request.model_snapshot,
                prompt_version=request.prompt_version,
                schema_hash=request.schema_hash,
                input_hash=request.input_hash,
                output={"text": text},
            )
            upsert_decision_cache(session, entry)
            applied += 1
    return applied, unmatched


def _do_batch(
    settings: Settings,
    *,
    requests_path: Path,
    max_cost: float | None,
    execute: bool,
    status_id: str | None,
    dry_run: bool,
    apply: bool = False,
) -> int:
    if status_id:
        client = OpenRouterBatchClient(settings)
        try:
            job = client.status(status_id)
        finally:
            client.close()
        print(json.dumps(asdict(job), ensure_ascii=False, sort_keys=True, indent=2))
        if job.status == "completed" and apply and settings.persist_to_postgres:
            requests = _load_requests(requests_path)
            _require_matching_model(requests, settings.batch_model_snapshot)
            applied, unmatched = _apply_results(settings, job.results, requests)
            print(f"Applied {applied} batch result(s) to decision_cache.")
            if unmatched:
                print(
                    f"warning: {unmatched} result(s) had no matching request; "
                    "check that --requests matches the submitted batch",
                    file=sys.stderr,
                )
                return EXIT_FAILURE
        return EXIT_OK if job.status == "completed" else EXIT_FAILURE

    if dry_run or not execute:
        requests = _load_requests(requests_path)
        batch_requests, _mapping = build_batch_requests(
            requests, model=settings.batch_model_snapshot or "unset"
        )
        payload = batch_payload(
            batch_requests,
            model=settings.batch_model_snapshot or "unset",
            endpoint=settings.batch_endpoint,
        )
        size = validate_batch_size(payload)
        estimate = BackfillEstimate(
            articles=len(requests),
            calls=len(requests),
            cost=round(len(requests) * _PRICE_PER_CALL, 4),
            duration_hours=0.0,
        )
        note = budget_gate(estimate, max_cost) if max_cost is not None else "no cost cap supplied"
        print(
            json.dumps(
                {
                    "requests": len(requests),
                    "payload_bytes": size,
                    "estimated_cost": estimate.cost,
                    "budget": note,
                    "endpoint": settings.batch_endpoint,
                    "model": settings.batch_model_snapshot,
                    "dry_run": True,
                },
                indent=2,
                sort_keys=True,
            )
        )
        out = requests_path.with_suffix(".batch.json")
        write_batch_file(payload, out)
        print(f"Batch request written to {out}; no network call made.")
        return EXIT_OK

    if not settings.batch_model_snapshot:
        raise ConfigError("BATCH_MODEL_SNAPSHOT must be pinned before submitting a batch")
    if max_cost is None:
        raise ConfigError("--max-cost is required to submit a batch")

    requests = _load_requests(requests_path)
    _require_matching_model(requests, settings.batch_model_snapshot)
    batch_requests, _mapping = build_batch_requests(requests, model=settings.batch_model_snapshot)
    payload = batch_payload(
        batch_requests,
        model=settings.batch_model_snapshot,
        endpoint=settings.batch_endpoint,
    )
    validate_batch_size(payload)
    estimate = BackfillEstimate(
        articles=len(requests),
        calls=len(requests),
        cost=round(len(requests) * _PRICE_PER_CALL, 4),
        duration_hours=0.0,
    )
    budget_gate(estimate, max_cost)

    client = OpenRouterBatchClient(settings)
    try:
        batch_id = client.submit(payload)
    finally:
        client.close()

    if settings.persist_to_postgres:
        run_id = f"batch-{batch_id}"
        from ..db.repository import finish_pipeline_run, start_pipeline_run
        from ..pipeline import _default_session_context

        with _default_session_context(settings) as session:
            start_pipeline_run(
                session,
                run_id=run_id,
                pipeline="mq-batch",
                partition_date=settings.report_date,
                commit=False,
            )
            finish_pipeline_run(
                session,
                run_id=run_id,
                status="running",
                stats={
                    "batch_id": batch_id,
                    "requests": len(requests),
                    "estimated_cost": estimate.cost,
                    "max_cost": max_cost,
                },
                commit=False,
            )
            session.commit()
    print(f"Submitted batch {batch_id}; poll with `mq batch --status {batch_id}`.")
    return EXIT_OK
