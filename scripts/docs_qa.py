"""CHK-08 — documentation consistency checks (docs/README.md QA checklist).

Usage: python scripts/docs_qa.py [--root PATH]

Exit code 0 = all checks pass, 1 = at least one FAIL. Standard library only; safe for CI.

The checks are pure functions over an immutable `Docs` snapshot. Nothing runs at import
time, so the module can be imported by tests without executing the suite.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Compiled once; the original script re-parsed these on every use.
LINK_RE = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")
DEC_RE = re.compile(r"\bDEC-\d{2}\b")
RSK_RE = re.compile(r"\bRSK-\d{2}\b")
REQ_RE = re.compile(r"\bREQ-\d{2}\b")
OUT_RE = re.compile(r"\bOUT-\d{2}\b")
OQ_ID_RE = re.compile(r"\bOQ-\d{2}\b")
OQ_TOKEN_RE = re.compile(r"\bOQ\b")
LEGACY_D_RE = re.compile(r"\bD[1-9]\b")
DEC_ROW_RE = re.compile(r"^\| DEC-\d{2} ", re.M)
RSK_ROW_RE = re.compile(r"^\| RSK-\d{2} ", re.M)
REQ_ROW_RE = re.compile(r"^\| REQ-\d{2} ", re.M)
CHK_ROW_RE = re.compile(r"^\| CHK-\d{2} ", re.M)
SOLVED_ROW_RE = re.compile(r"^\| (?:DEC|RSK)-\d{2} .*solved", re.M | re.I)
TRACKED_ROW_RE = re.compile(r"^\| (?:ENUM|BOOT|TRACE)-\d{2} ", re.M)
MQ_CMD_RE = re.compile(r"mq ([a-z]+)")
HEADING_RE = re.compile(r"^(#{1,6}) ")
TABLE_SEPARATOR_RE = re.compile(r"^\|[\s:|-]+\|$")
TASK_DEF_RE = re.compile(r"^\| \*\*(T\d+)\*\*", re.M)
TASK_REF_RE = re.compile(r"\bT(\d{1,2})\b")
WHITESPACE_RE = re.compile(r"\s+")
FILE_SPAN_PATH_RE = re.compile(
    r"`((?:src|tests|scripts|data|docs)/[\w./-]+\.(?:py|sql|json|csv|md|toml))`"
)

# Named constants replace the original magic numbers.
EXPECTED_DECISIONS = 19
MIN_RISKS = 16
EXPECTED_REQUIREMENTS = 14
EXPECTED_OUTPUTS = 5
EXPECTED_TASKS = 11
MIN_REGISTRY_ROWS = 28
DETAIL_LIMIT = 5

THRESHOLDS = ("100%", "0.90", "0.95", "0.70", "60/20/20")
THRESHOLD_DOCS = {
    "docs/01-requirements-analysis.md": THRESHOLDS,
    "docs/03-work-breakdown.md": THRESHOLDS,
    "docs/08-traceability.md": ("0.90", "0.70", "85%"),
}
REGISTER_STATUSES = ("solved", "accepted", "not adopted")
T0_NOTATION_FILES = (
    "README.md",
    "docs/README.md",
    "docs/01-requirements-analysis.md",
    "docs/03-work-breakdown.md",
    "docs/04-risks-and-open-questions.md",
    "docs/06-observations.md",
    "docs/07-sources.md",
    "docs/08-traceability.md",
)
ENGINEERING_TERMS = (
    "uv.lock",
    "Alembic",
    "mypy --strict",
    "ruff",
    "85%",
    "tenacity",
    "redact",
    "make check",
)
KNOWN_PATH_BASES = frozenset(
    {"src", "docs", "data", "tests", "digests", "var", "scripts", "migrations"}
)


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Docs:
    """Immutable snapshot of every Markdown file in the documentation set."""

    root: Path
    files: tuple[Path, ...]
    texts: dict[str, str]
    all_text: str

    @classmethod
    def load(cls, root: Path) -> Docs:
        files = (
            root / "README.md",
            root / "data/README.md",
            *(path for path in sorted((root / "docs").glob("*.md")) if path.name != "Test_News.md"),
        )
        texts = {path.as_posix(): path.read_text(encoding="utf-8") for path in files}
        return cls(root=root, files=files, texts=texts, all_text="\n".join(texts.values()))

    def text(self, relative: str) -> str:
        """Return a document's text; absent files contribute empty text (a check FAILs)."""
        return self.texts.get((self.root / relative).as_posix(), "")


def result(name: str, ok: bool, detail: str = "") -> CheckResult:
    """A detail is meaningful only for a failure."""
    return CheckResult(name=name, ok=ok, detail="" if ok else detail)


def details(items: Sequence[str]) -> str:
    return "; ".join(items[:DETAIL_LIMIT])


def contains_all(text: str, needles: Iterable[str]) -> bool:
    return all(needle in text for needle in needles)


def contains_any(text: str, needles: Iterable[str]) -> bool:
    return any(needle in text for needle in needles)


def between(text: str, start: str, end: str | None = None) -> str:
    """Return text between two markers, or '' when the start marker is absent."""
    _, separator, tail = text.partition(start)
    if not separator:
        return ""
    return tail.partition(end)[0] if end else tail


def fenced_block(text: str) -> str:
    """Return the contents of the first triple-backtick block, or '' if absent."""
    parts = text.split("```")
    return parts[1] if len(parts) > 2 else ""


def duplicates(values: Iterable[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if count > 1)


def check_links(docs: Docs) -> list[CheckResult]:
    bad: list[str] = []
    for file in docs.files:
        for match in LINK_RE.finditer(docs.texts[file.as_posix()]):
            target = match.group(1)
            if target.startswith(("http", "mailto")):
                continue
            if not (file.parent / target).resolve().exists():
                bad.append(f"{file.name} -> {target}")
    return [result("links resolve", not bad, details(bad))]


def check_canonical_md_path(docs: Docs) -> list[CheckResult]:
    ok = "digests/YYYY-MM-DD.md" in docs.all_text and "reports/YYYY-MM-DD.md" not in docs.text(
        "data/README.md"
    )
    return [result("canonical md path", ok)]


def check_mq_contract(docs: Docs) -> list[CheckResult]:
    contract = between(docs.text("docs/02-stack-and-architecture.md"), "## 5.", "## 6.")
    root_section = between(docs.text("README.md"), "## Command interface")
    contract_cmds = set(MQ_CMD_RE.findall(contract))
    root_cmds = set(MQ_CMD_RE.findall(root_section))
    unmatched = sorted(root_cmds - contract_cmds)
    return [result("mq commands match contract", not unmatched, f"unmatched: {unmatched}")]


def check_registers(docs: Docs) -> list[CheckResult]:
    reg = docs.text("docs/04-risks-and-open-questions.md")
    trace = docs.text("docs/08-traceability.md")
    dec_defined = set(DEC_RE.findall(reg + between(trace, "## 8.")))
    rsk_defined = set(RSK_RE.findall(reg + between(trace, "## 8.")))
    dec_refs = set(DEC_RE.findall(docs.all_text)) - dec_defined
    rsk_refs = set(RSK_RE.findall(docs.all_text)) - rsk_defined

    bad_status = [
        line[:30]
        for line in reg.splitlines()
        if (line.startswith("| DEC-") or line.startswith("| RSK-"))
        and not contains_any(line.lower(), REGISTER_STATUSES)
    ]
    neo4j_ok = (docs.root / "docs/09-neo4j-integration.md").exists() and contains_all(
        docs.text("docs/09-neo4j-integration.md").lower(),
        ("graph model", "sync architecture", "rollout", "chk-14"),
    )
    all_check_ids = tuple(range(1, 20))
    risk_plan_ok = (docs.root / "docs/10-risk-mitigation-plan.md").exists() and contains_all(
        docs.text("docs/10-risk-mitigation-plan.md").lower(),
        ("rsk-07", "rsk-08", "rsk-11", "chk-15", "chk-16", "chk-17"),
    )
    return [
        result(
            "DEC ids defined",
            not dec_refs and len(dec_defined) == EXPECTED_DECISIONS,
            f"undefined: {sorted(dec_refs)}",
        ),
        result("RSK ids defined", not rsk_refs and len(rsk_defined) >= MIN_RISKS),
        result("no OQ ids", OQ_ID_RE.search(docs.all_text) is None),
        result("no OQ token", OQ_TOKEN_RE.search(docs.all_text) is None),
        result("no legacy D-ids", LEGACY_D_RE.search(docs.all_text) is None),
        result("register statuses", not bad_status, details(bad_status)),
        result("no solved rows in 04", SOLVED_ROW_RE.search(reg) is None),
        result("neo4j planning doc", neo4j_ok),
        result("CHK ids defined", all(f"CHK-{n:02d}" in trace for n in all_check_ids)),
        result("risk plan doc", risk_plan_ok),
    ]


def check_trace_ids(docs: Docs) -> list[CheckResult]:
    d01 = docs.text("docs/01-requirements-analysis.md")
    trace = docs.text("docs/08-traceability.md")
    req_defined = set(REQ_RE.findall(d01))
    out_defined = set(OUT_RE.findall(d01))
    req_traced = set(REQ_RE.findall(trace))
    out_traced = set(OUT_RE.findall(trace))
    untraced = sorted((req_defined | out_defined) - (req_traced | out_traced))
    ok = (
        req_defined == req_traced
        and out_defined == out_traced
        and len(req_defined) == EXPECTED_REQUIREMENTS
        and len(out_defined) == EXPECTED_OUTPUTS
    )
    return [result("REQ/OUT traced", ok, f"untraced: {untraced}")]


def check_thresholds(docs: Docs) -> list[CheckResult]:
    results = [result(f"threshold {value}", value in docs.all_text) for value in THRESHOLDS]
    for path, required in THRESHOLD_DOCS.items():
        missing = [value for value in required if value not in docs.text(path)]
        results.append(
            result(f"thresholds in {Path(path).stem}", not missing, f"missing: {missing}")
        )
    return results


def check_numeric_consistency(docs: Docs) -> list[CheckResult]:
    d07 = docs.text("docs/07-sources.md")
    srcs = (docs.root / "data/sources.csv").read_text(encoding="utf-8")
    count = len(list(csv.DictReader(io.StringIO(srcs))))
    documented = re.search(r"(\d+) source rows", d07)
    ok = documented is not None and int(documented.group(1)) == count
    return [result("source count documented", ok, f"csv={count}")]


def check_double_blank_lines(docs: Docs) -> list[CheckResult]:
    bad: list[str] = []
    for file in docs.files:
        lines = docs.texts[file.as_posix()].splitlines()
        for index in range(len(lines) - 1):
            if not lines[index].strip() and not lines[index + 1].strip():
                bad.append(f"{file.name}:{index + 1}")
    return [result("no double blank lines", not bad, f"at: {details(bad)}")]


def check_schema_enum(docs: Docs) -> list[CheckResult]:
    schema = json.loads((docs.root / "data/quotes.schema.json").read_text(encoding="utf-8"))
    enum_values = schema["properties"]["source_type"].get("enum", [])
    d01 = docs.text("docs/01-requirements-analysis.md")
    missing = [value for value in enum_values if value not in d01]
    return [result("source_type enum", not missing, f"missing in 01: {missing}")]


def check_t0_notation(docs: Docs) -> list[CheckResult]:
    missing = [path for path in T0_NOTATION_FILES if "T0 gate" not in docs.text(path)]
    return [result("T0 notation", not missing, f"missing in: {missing}")]


def check_eval_tense(docs: Docs) -> list[CheckResult]:
    canonical = (
        "docs/02-stack-and-architecture.md",
        "docs/08-traceability.md",
    )
    ok = "goes read-only" not in docs.all_text and all(
        "went read-only 2026-10-31" in WHITESPACE_RE.sub(" ", docs.text(path)) for path in canonical
    )
    return [result("evals tense", ok)]


def check_jsonl_derived(docs: Docs) -> list[CheckResult]:
    schema_text = (docs.root / "data/quotes.schema.json").read_text(encoding="utf-8")
    ok = (
        "actual quote store" not in docs.all_text
        and "derived export" in docs.text("data/README.md")
        and "derived export" in schema_text
    )
    return [result("jsonl is derived", ok)]


def check_observations(docs: Docs) -> list[CheckResult]:
    d06 = docs.text("docs/06-observations.md").lower()
    d06_ok = contains_all(
        d06,
        ("mandatory sources", "dagster", "reuters", "bbc", "prefect", "airflow"),
    )
    return [
        result("doc05 retired", not (docs.root / "docs/05-critique-and-improvements.md").exists()),
        result("doc06 observations", d06_ok),
    ]


def check_engineering_standards(docs: Docs) -> list[CheckResult]:
    engineering = docs.text("docs/02-stack-and-architecture.md")
    trace = docs.text("docs/08-traceability.md")
    ok = contains_all(engineering, ENGINEERING_TERMS) and all(
        f"CHK-{n:02d}" in trace for n in range(10, 14)
    )
    return [result("engineering standards", ok)]


def check_registry_rows(docs: Docs) -> list[CheckResult]:
    srcs = (docs.root / "data/sources.csv").read_text(encoding="utf-8")
    ok = srcs.count("\n") >= MIN_REGISTRY_ROWS and ('"reuters"' in srcs or "\nreuters," in srcs)
    return [result("registry rows", ok)]


def check_code_spans(docs: Docs) -> list[CheckResult]:
    bad: list[str] = []
    for file in docs.files:
        in_fence = False
        for number, line in enumerate(docs.texts[file.as_posix()].splitlines(), 1):
            if line.strip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            if line.count("`") % 2 != 0:
                bad.append(f"{file.name}:{number} odd backtick count")
    return [result("code spans intact", not bad, details(bad))]


def check_file_span_paths(docs: Docs) -> list[CheckResult]:
    """Paths written as inline code must exist, not just Markdown links."""
    bad: list[str] = []
    for file in docs.files:
        for match in FILE_SPAN_PATH_RE.finditer(docs.texts[file.as_posix()]):
            path = match.group(1)
            if not (docs.root / path).exists():
                bad.append(f"{file.name} -> {path}")
    return [result("code span paths exist", not bad, details(bad))]


def check_tables(docs: Docs) -> list[CheckResult]:
    detail = ""
    for file in docs.files:
        lines = docs.texts[file.as_posix()].splitlines()
        for index, line in enumerate(lines[:-1]):
            if line.startswith("|") and not TABLE_SEPARATOR_RE.match(line):
                has_separator = TABLE_SEPARATOR_RE.match(lines[index + 1])
                is_standalone = index == 0 or not lines[index - 1].startswith("|")
                if not has_separator and is_standalone:
                    detail = f"{file.name}:{index + 1} missing separator"
                    break
        if detail:
            break
    return [result("table separators", not detail, detail)]


def check_duplicate_ids(docs: Docs) -> list[CheckResult]:
    reg = docs.text("docs/04-risks-and-open-questions.md")
    d01 = docs.text("docs/01-requirements-analysis.md")
    trace = docs.text("docs/08-traceability.md")
    found = {
        "dec": duplicates(DEC_ROW_RE.findall(reg)),
        "rsk": duplicates(RSK_ROW_RE.findall(reg)),
        "req": duplicates(REQ_ROW_RE.findall(d01)),
        "chk": duplicates(CHK_ROW_RE.findall(trace)),
    }
    detail = " ".join(f"{name}={values}" for name, values in found.items())
    return [result("no duplicate IDs", not any(found.values()), detail)]


def check_layout_paths(docs: Docs) -> list[CheckResult]:
    layout = fenced_block(between(docs.text("README.md"), "## Layout"))
    tokens: set[str] = set()
    for line in layout.splitlines():
        token = line.strip().split()[0] if line.strip() else ""
        is_candidate = bool(token) and ("/" in token or token.endswith((".py", ".sql")))
        if is_candidate and token.split("/")[0] in KNOWN_PATH_BASES:
            tokens.add(token.rstrip("/,"))
    missing = [
        path
        for path in sorted(tokens)
        if not (docs.root / path.replace("{blobs,audit}", "blobs")).exists()
    ]
    return [result("layout paths exist", not missing, f"missing: {details(missing)}")]


def check_sources_documented(docs: Docs) -> list[CheckResult]:
    srcs = (docs.root / "data/sources.csv").read_text(encoding="utf-8")
    ids = [row["source_id"] for row in csv.DictReader(io.StringIO(srcs))]
    d07 = docs.text("docs/07-sources.md")
    missing = [source_id for source_id in ids if f"`{source_id}`" not in d07]
    return [result("sources documented in 07", not missing, f"missing: {details(missing)}")]


def check_tracked_changes(docs: Docs) -> list[CheckResult]:
    trace = docs.text("docs/08-traceability.md")
    tracked = {
        line.split("|", 2)[1].strip(): line
        for line in trace.splitlines()
        if TRACKED_ROW_RE.match(line)
    }
    bad: list[str] = []
    for tracked_id in ("ENUM-01", "BOOT-01", "TRACE-01"):
        line = tracked.get(tracked_id, "")
        if not line or not contains_all(line, ("T1", "CHK-", "Balgove Innovations")):
            bad.append(f"{tracked_id}: row missing or incomplete")

    def code(relative: str) -> str:
        path = docs.root / relative
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    enums = code("src/media_quotes/enums.py")
    validation = code("src/media_quotes/validation/sources.py")
    if not contains_all(
        enums, ("SourceType =", "RightsStatus =", "AccessMode =", "DiscoveryMethod =")
    ) or not contains_all(
        validation,
        (
            "from ..enums import",
            "SOURCE_TYPES",
            "RIGHTS_STATUSES",
            "ACCESS_MODES",
            "DISCOVERY_METHODS",
        ),
    ):
        bad.append("ENUM-01: canonical enums not shared by enums.py/validation.py")

    bootstrap = code("src/media_quotes/bootstrap.py").lower()
    schema = code("src/media_quotes/db/schema.sql")
    migration = code("migrations/versions/0001_initial.py")
    if not (
        (docs.root / "alembic.ini").is_file()
        and (docs.root / "migrations/versions").is_dir()
        and "alembic" in bootstrap
        and "schema.sql" not in bootstrap
        and "op.execute" in migration
        and "canonical_quote_id" in schema
    ):
        bad.append("BOOT-01: Alembic migration path or canonical id missing")

    observability = code("src/media_quotes/observability.py")
    if not contains_all(
        observability,
        ("redact_sensitive", "_SENSITIVE_KEYS", "OTLPSpanExporter", "ConsoleSpanExporter"),
    ):
        bad.append("TRACE-01: redaction processor or OTLP exporter missing")

    return [result("tracked changes complete", not bad, details(bad))]


def check_task_ids(docs: Docs) -> list[CheckResult]:
    defined = set(TASK_DEF_RE.findall(docs.text("docs/03-work-breakdown.md")))
    referenced = {f"T{number}" for number in TASK_REF_RE.findall(docs.all_text)}
    missing = sorted(referenced - defined)
    ok = defined == {f"T{i}" for i in range(EXPECTED_TASKS)} and not missing
    return [result("task ids defined", ok, f"undefined: {missing}")]


def check_headings(docs: Docs) -> list[CheckResult]:
    jumps: list[str] = []
    for file in docs.files:
        previous = 0
        in_fence = False
        for number, line in enumerate(docs.texts[file.as_posix()].splitlines(), 1):
            if line.strip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            match = HEADING_RE.match(line)
            if match:
                level = len(match.group(1))
                if previous and level > previous + 1:
                    jumps.append(f"{file.name}:{number}")
                previous = level
    return [result("heading levels", not jumps, f"jumps: {details(jumps)}")]


def check_trailing_whitespace(docs: Docs) -> list[CheckResult]:
    bad: list[str] = []
    for file in docs.files:
        for number, line in enumerate(docs.texts[file.as_posix()].splitlines(), 1):
            if line and line != line.rstrip():
                bad.append(f"{file.name}:{number}")
    return [result("no trailing whitespace", not bad, f"at: {details(bad)}")]


# Registration order determines output order; keep it stable for CI diffs.
CHECKS: tuple[Callable[[Docs], list[CheckResult]], ...] = (
    check_links,
    check_canonical_md_path,
    check_mq_contract,
    check_registers,
    check_trace_ids,
    check_thresholds,
    check_numeric_consistency,
    check_double_blank_lines,
    check_schema_enum,
    check_t0_notation,
    check_eval_tense,
    check_jsonl_derived,
    check_observations,
    check_engineering_standards,
    check_registry_rows,
    check_code_spans,
    check_file_span_paths,
    check_tables,
    check_duplicate_ids,
    check_layout_paths,
    check_sources_documented,
    check_tracked_changes,
    check_task_ids,
    check_headings,
    check_trailing_whitespace,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Documentation consistency checks (CHK-08)")
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="project root (default: repository root)",
    )
    args = parser.parse_args(argv)

    docs = Docs.load(args.root)
    results = [item for check in CHECKS for item in check(docs)]
    failures = [item.name for item in results if not item.ok]

    for item in results:
        line = f"[{'PASS' if item.ok else 'FAIL'}] {item.name}"
        if item.detail and not item.ok:
            line += f" — {item.detail}"
        print(line)
    print(f"\n{len(results) - len(failures)}/{len(results)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
