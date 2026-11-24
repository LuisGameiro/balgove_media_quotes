"""Unit tests for the pure helpers in scripts/docs_qa.py.

The checker is stdlib-only and has no import-time side effects, so it can be loaded from
its file path without installing it as a package.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_checker(root: Path) -> ModuleType:
    path = root / "scripts" / "docs_qa.py"
    spec = importlib.util.spec_from_file_location("docs_qa_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def qa(project_root: Path) -> ModuleType:
    return _load_checker(project_root)


def test_result_hides_detail_on_pass(qa: ModuleType) -> None:
    assert qa.result("name", True, "noise").detail == ""
    assert qa.result("name", False, "reason").detail == "reason"


def test_between_and_fenced_block(qa: ModuleType) -> None:
    assert qa.between("A ## 5. one ## 6. two", "## 5.", "## 6.") == " one "
    assert qa.between("no marker", "## 5.") == ""
    assert qa.fenced_block("intro\n```\ncode\n```\nend") == "\ncode\n"
    assert qa.fenced_block("no fence") == ""


def test_duplicates_and_contains(qa: ModuleType) -> None:
    assert qa.duplicates(["a", "b", "a"]) == ["a"]
    assert qa.contains_all("alpha beta", ("alpha", "beta"))
    assert qa.contains_any("alpha beta", ("missing", "beta"))


def test_trace_tokens_allowed_only_in_decision_log(qa: ModuleType, project_root: Path) -> None:
    decision_path = (project_root / qa.DECISION_LOG).as_posix()
    requirements_path = (project_root / "docs/01-requirements.md").as_posix()
    docs = qa.Docs(
        root=project_root,
        files=(),
        texts={decision_path: "DEC-01", requirements_path: "DEC-02"},
        all_text="DEC-01\nDEC-02",
    )

    trace_check, _ = qa.check_no_traceability(docs)

    assert not trace_check.ok
    assert trace_check.detail == "DEC-02"


def test_docs_snapshot_and_all_checks_run(qa: ModuleType, project_root: Path) -> None:
    docs = qa.Docs.load(project_root)
    assert docs.text("README.md")
    results = [item for check in qa.CHECKS for item in check(docs)]
    assert len(results) >= 14
    assert all(isinstance(item, qa.CheckResult) for item in results)
    assert all(item.ok for item in results)
