"""CLI exit-code boundary tests."""

from __future__ import annotations

import pytest

from media_quotes import cli
from media_quotes.exceptions import ConfigError, SourceNotPermitted, ValidationError


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ValidationError("bad data"), 1),
        (ConfigError("bad config"), 3),
        (SourceNotPermitted("t0"), 1),
        (ValueError("deep processing failure"), 1),
    ],
)
def test_domain_errors_map_to_exit_codes(monkeypatch, error: Exception, code: int) -> None:
    def _raise() -> int:
        raise error

    monkeypatch.setattr("media_quotes.cli.app._validate", _raise)
    assert cli.main(["doctor"]) == code
