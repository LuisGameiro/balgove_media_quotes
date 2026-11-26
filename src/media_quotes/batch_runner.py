"""OpenRouter Batch API submit/poll wrapper with an injectable HTTP client.

Policy: an operator must pass an explicit estimated-cost ceiling, no network call happens on
dry-run, and only successful results are applied to decision_cache by custom_id.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from .exceptions import ConfigError

if TYPE_CHECKING:
    from .config import Settings


@dataclass(frozen=True, slots=True)
class BatchJob:
    id: str
    status: str
    request_counts: dict[str, int]
    results: list[dict[str, Any]]


class OpenRouterBatchClient:
    """Thin wrapper over POST/GET /api/v1/batches; client is injectable for tests."""

    def __init__(self, settings: Settings, *, client: httpx.Client | None = None) -> None:
        api_key = settings.openrouter_api_key
        if api_key is None or not api_key.get_secret_value():
            raise ConfigError("OPENROUTER_API_KEY is required for batch operations")
        self.settings = settings
        self._client = client or httpx.Client(timeout=settings.openrouter_timeout_seconds)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    @property
    def _headers(self) -> dict[str, str]:
        api_key = self.settings.openrouter_api_key
        if api_key is None:
            raise ConfigError("OPENROUTER_API_KEY is required for batch operations")
        return {
            "Authorization": f"Bearer {api_key.get_secret_value()}",
            "HTTP-Referer": self.settings.openrouter_app_url,
            "X-Title": self.settings.openrouter_app_title,
        }

    @property
    def _url(self) -> str:
        return f"{self.settings.openrouter_base_url.rstrip('/')}/batches"

    def submit(self, payload: dict[str, Any]) -> str:
        response = self._client.post(self._url, json=payload, headers=self._headers)
        response.raise_for_status()
        batch_id = response.json().get("id")
        if not isinstance(batch_id, str) or not batch_id:
            raise ConfigError("batch submit response did not contain an id")
        return batch_id

    def status(self, batch_id: str) -> BatchJob:
        # batch_id becomes a URL path segment; keep the API key on the batches path.
        if not re.fullmatch(r"[A-Za-z0-9_-]+", batch_id):
            raise ConfigError("invalid batch id")
        response = self._client.get(f"{self._url}/{batch_id}", headers=self._headers)
        response.raise_for_status()
        payload = response.json()
        return BatchJob(
            id=str(payload.get("id") or batch_id),
            status=str(payload.get("status") or "unknown"),
            request_counts=dict(payload.get("request_counts") or {}),
            results=[
                dict(item) for item in (payload.get("results") or []) if isinstance(item, dict)
            ],
        )
