"""Compatibility shim for ``scripts.source_proof``.

The implementation lives in ``scripts/diagnostics/source_proof.py``. This module aliases
itself to that implementation so old imports and ``python -m scripts.source_proof``
keep working, and also supports direct execution by path.
"""

import sys
from pathlib import Path

if __package__:
    from .diagnostics import source_proof as _impl
else:  # executed by path: make the repository root importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.diagnostics import source_proof as _impl

sys.modules[__name__] = _impl
setattr(  # noqa: B010  # keep the old import path working
    sys.modules[__package__ or "scripts"],
    "source_proof",
    _impl,
)

if __name__ == "__main__":
    raise SystemExit(_impl.main())
