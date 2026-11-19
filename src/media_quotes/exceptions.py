"""Domain exceptions shared by adapters, policy checks and the CLI."""

from __future__ import annotations


class MediaQuotesError(Exception):
    """Base class for expected, user-facing failures."""


class ConfigError(MediaQuotesError):
    """Configuration is missing or invalid."""


class T0GateError(MediaQuotesError):
    """A source has not cleared the legal rights gate."""


class ValidationError(MediaQuotesError):
    """Data assets failed validation."""


class BudgetExceededError(MediaQuotesError):
    """Raised when a backfill would exceed its authorised budget (exit code 3)."""


class SourceNotPermitted(T0GateError):
    """Raised when a source has not cleared the T0 gate."""
