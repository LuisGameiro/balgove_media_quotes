"""Deterministic LLM context packs built from verified export records or graph subgraphs.

No network or LLM calls happen here. Token counts are a conservative word-based estimate so
the builder stays dependency-free; quotes are added whole or not at all.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from .config import DEFAULT_TOPIC_ID
from .models import ExportQuoteRecord
from .processing.export import content_hash
from .text_utils import require_safe_id

GENERATOR_VERSION = "context-pack-v1"
GRAPH_GENERATOR_VERSION = "graph-context-pack-v1"


def estimate_tokens(text: str) -> int:
    """Approximate tokens without a tokenizer: one token per four characters."""
    return (len(text) + 3) // 4


def build_context_pack(
    records: Sequence[ExportQuoteRecord],
    *,
    max_tokens: int = 4000,
    topic_id: str = DEFAULT_TOPIC_ID,
    generator_version: str = GENERATOR_VERSION,
) -> dict[str, Any]:
    """Build a bounded pack with provenance; never split or rewrite a quote."""
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    safe_topic = require_safe_id(topic_id, "topic_id")
    kept: list[dict[str, Any]] = []
    used = 0
    for record in records:
        if record.topic_id != safe_topic:
            continue
        item = {
            "quote_id": record.quote_id,
            "quote_text": record.quote_text_original,
            "source_id": record.source_id,
            "section_id": record.section_id,
            "content_hash": record.content_hash,
        }
        cost = estimate_tokens(json.dumps(item, ensure_ascii=False))
        if used + cost > max_tokens:
            continue
        kept.append(item)
        used += cost
    quote_ids = [item["quote_id"] for item in kept]
    manifest = {
        "generator_version": generator_version,
        "topic_id": safe_topic,
        "token_budget": max_tokens,
        "token_estimate": used,
        "quote_ids": quote_ids,
        "quote_count": len(kept),
        "input_hash": content_hash(quote_ids),
    }
    return {"manifest": manifest, "quotes": kept}


def build_graph_context_pack(
    subgraph_quotes: Sequence[dict[str, Any]],
    *,
    max_tokens: int = 4000,
    topic_id: str = DEFAULT_TOPIC_ID,
    generator_version: str = GRAPH_GENERATOR_VERSION,
) -> dict[str, Any]:
    """Build a bounded GraphRAG pack enriched with speaker, tier, and supersession context."""
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    safe_topic = require_safe_id(topic_id, "topic_id")
    kept: list[dict[str, Any]] = []
    used = 0
    for quote in subgraph_quotes:
        item: dict[str, Any] = {
            "quote_id": quote.get("quote_id"),
            "quote_text": quote.get("quote_text_en") or quote.get("quote_text"),
            "source_id": quote.get("source_id"),
            "tier": quote.get("tier"),
            "country_region": quote.get("country_region"),
            "claim_only": quote.get("claim_only", False),
            "section_id": quote.get("section_id"),
            "article_url": quote.get("article_url"),
        }
        if quote.get("speaker"):
            item["speaker"] = quote["speaker"]
        if quote.get("supersedes_quote_id"):
            item["supersedes"] = {
                "quote_id": quote["supersedes_quote_id"],
                "reason": quote.get("supersession_reason", "superseded"),
            }
        cost = estimate_tokens(json.dumps(item, ensure_ascii=False))
        if used + cost > max_tokens:
            continue
        kept.append(item)
        used += cost

    quote_ids = [str(item["quote_id"]) for item in kept]
    manifest = {
        "generator_version": generator_version,
        "topic_id": safe_topic,
        "token_budget": max_tokens,
        "token_estimate": used,
        "quote_ids": quote_ids,
        "quote_count": len(kept),
        "input_hash": content_hash(quote_ids),
    }
    return {"manifest": manifest, "quotes": kept}


__all__ = [
    "GENERATOR_VERSION",
    "GRAPH_GENERATOR_VERSION",
    "build_context_pack",
    "build_graph_context_pack",
    "estimate_tokens",
]
