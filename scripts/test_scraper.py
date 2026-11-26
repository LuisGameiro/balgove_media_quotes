"""Compatibility shim for ``scripts.test_scraper``.

The implementation lives in ``scripts/diagnostics/test_scraper.py``. This module aliases
itself to that implementation so existing imports and monkeypatch targets keep working,
and supports direct execution by path as well as ``python -m scripts.test_scraper``.
"""

import sys
from pathlib import Path

if __package__:
    from .diagnostics import test_scraper as _impl
else:  # executed by path: make the repository root importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.diagnostics import test_scraper as _impl

sys.modules[__name__] = _impl
setattr(  # noqa: B010  # keep the old import path working
    sys.modules[__package__ or "scripts"],
    "test_scraper",
    _impl,
)

if __name__ == "__main__":
    raise SystemExit(_impl.main())
