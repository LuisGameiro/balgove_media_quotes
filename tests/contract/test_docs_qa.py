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
    assert len(results) >= 16
    assert all(isinstance(item, qa.CheckResult) for item in results)
    assert all(item.ok for item in results)


def test_readme_command_paths(qa: ModuleType, project_root: Path) -> None:
    readme = (project_root / "README.md").as_posix()

    def snapshot(command: str):
        return qa.Docs(
            root=project_root,
            files=(),
            texts={readme: f"```bash\n{command}\n```"},
            all_text="",
        )

    bad = qa.check_readme_commands(snapshot("head var/output/quotes/2026-11-23.jsonl"))
    assert not bad[0].ok

    good = qa.check_readme_commands(
        snapshot(
            "export DAGSTER_HOME=var/dagster\nhead -n 5 var/output/quotes/quotes-2026-11-23.jsonl"
        )
    )
    assert all(item.ok for item in good)


def test_command_guard_rejects_graph_subcommand(qa: ModuleType, project_root: Path) -> None:
    command_path = (project_root / "docs/05-command-reference.md").as_posix()
    docs = qa.Docs(
        root=project_root,
        files=(),
        texts={
            command_path: (
                "## Commands\n\n"
                "| Command | Purpose |\n|---|---|\n"
                "| `init` | Not a top-level command |\n\n"
                "## Command boundaries\n"
            )
        },
        all_text="",
    )

    results = qa.check_commands(docs)

    assert not results[0].ok
    assert results[0].detail == "init"


def test_source_count_guard_uses_registry(qa: ModuleType, project_root: Path) -> None:
    source_path = (project_root / "docs/03-sources.md").as_posix()
    data_path = (project_root / "data/README.md").as_posix()
    docs = qa.Docs(
        root=project_root,
        files=(),
        texts={
            source_path: "**999 sources**; 999 are `tier_block=true`; 0 are not blocked.",
            data_path: "all 999 sources; 999 are `tier_block=true`; 0 are not blocked.",
        },
        all_text="",
    )

    results = {item.name: item for item in qa.check_code_facts(docs)}

    assert not results["source count documented"].ok
    assert not results["source block counts documented"].ok


def test_source_guard_uses_authoritative_json(qa: ModuleType, tmp_path: Path) -> None:
    source_dir = tmp_path / "data" / "sources"
    source_dir.mkdir(parents=True)
    (source_dir / "bbc.json").write_text('{"source_id": "bbc"}', encoding="utf-8")
    source_path = (tmp_path / "docs" / "03-sources.md").as_posix()
    docs = qa.Docs(
        root=tmp_path,
        files=(),
        texts={source_path: "BBC is documented as `bbc`."},
        all_text="",
    )

    assert qa.check_sources(docs)[0].ok


def test_repository_guard_detects_missing_module(qa: ModuleType, project_root: Path) -> None:
    current = qa.Docs.load(project_root)
    structure_path = (project_root / "docs/04-repository-structure.md").as_posix()
    docs = qa.Docs(
        root=project_root,
        files=(),
        texts={
            structure_path: current.text("docs/04-repository-structure.md").replace(
                "quote_audit.py", ""
            )
        },
        all_text="",
    )

    check = qa.check_repository_structure(docs)[0]

    assert not check.ok
    assert "quote_audit.py" in check.detail

    wrong_path = qa.Docs(
        root=project_root,
        files=(),
        texts={
            structure_path: current.text("docs/04-repository-structure.md").replace(
                "src/media_quotes/backfill.py", "backfill.py"
            )
        },
        all_text="",
    )
    path_check = qa.check_repository_structure(wrong_path)[0]
    assert not path_check.ok
    assert "src/media_quotes/backfill.py" in path_check.detail
