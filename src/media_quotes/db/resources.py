"""Dagster-friendly resource handles for database session contexts."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

SessionContextFactory = Callable[[Any], AbstractContextManager[Any]]


class DatabaseResourceHandle:
    """Opaque handle Dagster can inject as a normal Python resource object."""

    def __init__(self, factory: SessionContextFactory) -> None:
        self._factory = factory

    def context(self) -> SessionContextFactory:
        return self._factory
