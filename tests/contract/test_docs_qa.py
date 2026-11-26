"""Contract tests for the documentation QA checker in scripts/ci/docs_qa.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def qa() -> ModuleType:
    path = ROOT / "scripts" / "ci" / "docs_qa.py"
    spec = importlib.util.spec_from_file_location("docs_qa_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_result_hides_detail_on_pass(qa: ModuleType) -> None:
    passed = qa.result("sample", True, "noise")
    failed = qa.result("sample", False, "reason")
    assert passed.ok and passed.detail == ""
    assert not failed.ok and failed.detail == "reason"


def test_all_docs_checks_run_on_current_tree(qa: ModuleType) -> None:
    results = qa.run(ROOT)
    assert results
    assert all(isinstance(item, str) for item in (r.name for r in results))
    assert all(qa.CheckResult is type(r) for r in results)


def test_command_check_uses_current_split_marker(qa: ModuleType) -> None:
    docs = qa.Docs.load(ROOT)
    assert all(item.ok for item in qa.check_commands(docs))
