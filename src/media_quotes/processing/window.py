"""Reporting-window arithmetic shared by the pipeline, CLI and orchestration."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import Settings


def window(report_date: date, settings: Settings) -> tuple[date, date]:
    """Return the inclusive [start, end] report window ending at report_date."""
    return report_date - timedelta(days=settings.report_window_days - 1), report_date


_window = window  # frozen private alias for legacy callers
