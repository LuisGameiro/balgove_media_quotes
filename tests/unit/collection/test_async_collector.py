"""Async live-collection primitives and the single sync CLI boundary wrapper."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from media_quotes.ingestion import live


class _Result:
    pass


def test_async_collector_can_be_awaited_from_running_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    async def fake_async(*args: Any, **kwargs: Any) -> _Result:
        called.append("async")
        return _Result()

    monkeypatch.setattr(live, "collect_live_source_async", fake_async)

    async def caller() -> _Result:
        # No asyncio.run here: this already runs inside an event loop.
        return await live.collect_live_source_async()

    result = asyncio.run(caller())
    assert isinstance(result, _Result)
    assert called == ["async"]


def test_sync_wrapper_runs_async_collector(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_async(*args: Any, **kwargs: Any) -> _Result:
        return _Result()

    monkeypatch.setattr(live, "collect_live_source_async", fake_async)
    assert isinstance(live.collect_live_source(), _Result)
