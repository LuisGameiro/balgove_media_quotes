"""Documentation QA for the explanatory documentation set.

Usage: python scripts/docs_qa.py [--root PATH]

Standard library only; safe for CI. Nothing runs at import time, so the pure checks and
helpers can also be used by tests/contract/test_docs_qa.py.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LINK_RE = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")
HEADING_RE = re.compile(r"^(#{1,6}) ")
TABLE_SEPARATOR_RE = re.compile(r"^\|[\s:|-]+\|$")
FILE_SPAN_PATH_RE = re.compile(
    r"`((?:src|tests|scripts|data|docs)/[\w./-]+\.(?:py|sql|json|csv|md|toml))`"
)
TRACE_RE = re.compile(r"\b(?:DEC|RSK|CHK)-\d{2}\b")
OLD_DOC_RE = re.compile(
    r"(?:01-requirements-analysis|02-stack-and-architecture|03-work-breakdown|"
    r"04-risks-and-open-questions|06-observations|07-sources|08-traceability|"
    r"09-neo4j-integration|10-risk-mitigation-plan|11-repository-structure|"
    r"12-implementation-plan|13-code-review-and-feature-roadmap|"
    r"14-command-reference|15-source-topic-study|16-topic-language-ai-judge-plan|"
    r"17-connection-and-fetching-reference)\.md"
)

DECISION_LOG = "docs/07-decision-log.md"

EXPECTED_DOCS = (
    "docs/01-requirements.md",
    "docs/02-architecture.md",
    "docs/03-sources.md",
    "docs/04-repository-structure.md",
    "docs/05-command-reference.md",
    "docs/06-whats-left.md",
    DECISION_LOG,
    "docs/README.md",
    "docs/Test_News.md",
)


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Docs:
    root: Path
    files: tuple[Path, ...]
    texts: dict[str, str]
    all_text: str

    @classmethod
    def load(cls, root: Path) -> Docs:
        files = (
            root / "README.md",
            root / "data/README.md",
            *(
                path
                for path in sorted((root / "docs").iterdir())
                if path.is_file() and not path.name.startswith("Test_News")
            ),
        )
        texts = {
            path.as_posix(): path.read_text(encoding="utf-8")
            for path in files
            if path.suffix == ".md" and path.is_file()
        }
        return cls(root=root, files=files, texts=texts, all_text="\n".join(texts.values()))

    def text(self, relative: str) -> str:
        return self.texts.get((self.root / relative).as_posix(), "")


def result(name: str, ok: bool, detail: str = "") -> CheckResult:
    return CheckResult(name=name, ok=bool(ok), detail="" if ok else detail)


def details(items: Sequence[str], limit: int = 5) -> str:
    return "; ".join(items[:limit])


def contains_all(text: str, needles: Iterable[str]) -> bool:
    return all(needle in text for needle in needles)


def contains_any(text: str, needles: Iterable[str]) -> bool:
    return any(needle in text for needle in needles)


def between(text: str, start: str, end: str | None = None) -> str:
    _, separator, tail = text.partition(start)
    if not separator:
        return ""
    return tail.partition(end)[0] if end else tail


def fenced_block(text: str) -> str:
    parts = text.split("```")
    return parts[1] if len(parts) > 2 else ""


def duplicates(values: Iterable[str]) -> list[str]:
    from collections import Counter

    return sorted(value for value, count in Counter(values).items() if count > 1)


def check_required_docs(docs: Docs) -> list[CheckResult]:
    missing = [name for name in EXPECTED_DOCS if not (docs.root / name).is_file()]
    return [result("required docs", not missing, details(missing))]


def check_links(docs: Docs) -> list[CheckResult]:
    bad: list[str] = []
    for file in docs.files:
        if file.suffix != ".md":
            continue
        for match in LINK_RE.finditer(docs.texts.get(file.as_posix(), "")):
            target = match.group(1)
            if target.startswith(("http", "mailto")):
                continue
            if not (file.parent / target).resolve().exists():
                bad.append(f"{file.name} -> {target}")
    return [result("links resolve", not bad, details(bad))]


def check_code_spans(docs: Docs) -> list[CheckResult]:
    bad: list[str] = []
    for file in docs.files:
        for number, line in enumerate(docs.texts.get(file.as_posix(), "").splitlines(), 1):
            if line.strip().startswith("```") or line.count("`") % 2:
                continue
            for match in FILE_SPAN_PATH_RE.finditer(line):
                path = match.group(1)
                if not (docs.root / path).exists():
                    bad.append(f"{file.name}:{number} -> {path}")
    return [result("code span paths exist", not bad, details(bad))]


def check_structure(docs: Docs) -> list[CheckResult]:
    double: list[str] = []
    trailing: list[str] = []
    tables: list[str] = []
    headings: list[str] = []
    for file in docs.files:
        text = docs.texts.get(file.as_posix(), "")
        lines = text.splitlines()
        for index in range(len(lines) - 1):
            if not lines[index].strip() and not lines[index + 1].strip():
                double.append(f"{file.name}:{index + 1}")
        for index, line in enumerate(lines, 1):
            if line != line.rstrip():
                trailing.append(f"{file.name}:{index}")
            heading = HEADING_RE.match(line)
            if heading and index > 1 and len(heading.group(1)) > 3:
                headings.append(f"{file.name}:{index}")
            no_separator = index == len(lines) or not TABLE_SEPARATOR_RE.match(lines[index])
            standalone = index == 1 or not lines[index - 2].startswith("|")
            if (
                line.startswith("|")
                and not TABLE_SEPARATOR_RE.match(line)
                and no_separator
                and standalone
            ):
                tables.append(f"{file.name}:{index}")
    return [
        result("no double blank lines", not double, details(double)),
        result("no trailing whitespace", not trailing, details(trailing)),
        result("table separators", not tables, details(tables)),
        result("heading levels", not headings, details(headings)),
    ]


def check_no_traceability(docs: Docs) -> list[CheckResult]:
    trace_text = "\n".join(
        text for path, text in docs.texts.items() if Path(path) != docs.root / DECISION_LOG
    )
    hits = sorted(set(TRACE_RE.findall(trace_text)))
    old = sorted(set(OLD_DOC_RE.findall(docs.all_text)))
    return [
        result("no stray trace tokens", not hits, details(hits)),
        result("no old doc names", not old, details(old)),
    ]


def check_sources(docs: Docs) -> list[CheckResult]:
    text = (docs.root / "data/sources.csv").read_text(encoding="utf-8")
    ids = [row["source_id"] for row in csv.DictReader(io.StringIO(text))]
    d03 = docs.text("docs/03-sources.md")
    missing = [source_id for source_id in ids if f"`{source_id}`" not in d03]
    return [result("sources documented", not missing, details(missing))]


def check_whats_left(docs: Docs) -> list[CheckResult]:
    text = docs.text("docs/06-whats-left.md")
    ok = text.count("- [ ]") >= 10 and "#" in text
    return [result("whats-left steps", ok)]


def check_canonical_md_path(docs: Docs) -> list[CheckResult]:
    ok = "digests/YYYY-MM-DD.md" in docs.text("docs/01-requirements.md")
    return [result("canonical md path", ok)]


def check_code_facts(docs: Docs) -> list[CheckResult]:
    """Small guards that stop docs drifting from the code they describe."""
    alembic = (docs.root / "alembic.ini").read_text(encoding="utf-8")
    config = (docs.root / "src/media_quotes/config.py").read_text(encoding="utf-8")
    return [
        result(
            "alembic script location",
            "script_location = %(here)s/migrations" in alembic,
        ),
        result("output dir setting", "output_dir:" in config and "OUTPUT_DIR" in config),
        result("source count documented", "28 sources" in docs.text("docs/03-sources.md")),
    ]


def check_commands(docs: Docs) -> list[CheckResult]:
    """Every `mq` command named in the reference must exist in the CLI parser."""
    app = (docs.root / "src/media_quotes/cli/app.py").read_text(encoding="utf-8")
    known = set(re.findall(r'add_parser\(\s*"([a-z][a-z0-9-]*)"', app))
    text = docs.text("docs/05-command-reference.md")
    section = text.split("## Commands", 1)[-1].split("## Command boundaries", 1)[0]
    documented = set(re.findall(r"^\|\s*`([a-z][a-z0-9-]*)", section, re.MULTILINE))
    missing = sorted(documented - known)
    return [result("documented commands exist", not missing, details(missing))]


CHECKS: tuple[Callable[[Docs], list[CheckResult]], ...] = (
    check_required_docs,
    check_links,
    check_code_spans,
    check_structure,
    check_no_traceability,
    check_sources,
    check_whats_left,
    check_canonical_md_path,
    check_code_facts,
    check_commands,
)


def run(root: Path) -> list[CheckResult]:
    docs = Docs.load(root)
    return [item for check in CHECKS for item in check(docs)]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Documentation QA")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    results = run(args.root)
    for item in results:
        status = "PASS" if item.ok else "FAIL"
        print(f"[{status}] {item.name}" + (f" — {item.detail}" if item.detail else ""))
    failed = sum(1 for item in results if not item.ok)
    print(f"{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
