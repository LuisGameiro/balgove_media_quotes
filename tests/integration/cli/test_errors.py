"""CLI exit-code boundary tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from media_quotes import cli
from media_quotes.exceptions import ConfigError, SourceNotPermitted, ValidationError

_PYDANTIC_ERROR = PydanticValidationError.from_exception_data(
    "Settings", [{"type": "missing", "loc": ("field",), "input": {}}]
)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ValidationError("bad data"), 1),
        (ConfigError("bad config"), 3),
        (SourceNotPermitted("t0"), 1),
        (_PYDANTIC_ERROR, 3),
        (FileNotFoundError("missing config file"), 3),
    ],
)
def test_domain_errors_map_to_exit_codes(monkeypatch, error: Exception, code: int) -> None:
    def _raise() -> int:
        raise error

    monkeypatch.setattr(cli, "_validate", _raise)
    assert cli.main(["doctor"]) == code
