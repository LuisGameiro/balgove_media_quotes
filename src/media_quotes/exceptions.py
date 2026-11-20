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


class RateLimitExceededError(MediaQuotesError, RuntimeError):
    """Raised when an HTTP or API request violates rate limits."""


class BatchError(MediaQuotesError):
    """Base class for batch processing errors."""


class DuplicateBatchRequestError(BatchError, ValueError):
    """Raised when duplicate batch requests are detected."""


class ReviewError(MediaQuotesError):
    """Base class for editorial and obsolescence review errors."""


class ReviewStateError(ReviewError, ValueError):
    """Raised when an invalid state transition is requested on a review item."""


class ReviewNotFoundError(ReviewError, KeyError):
    """Raised when a review flag cannot be found."""


class ExtractionError(MediaQuotesError):
    """Raised when article extraction fails or produces unparseable output."""
