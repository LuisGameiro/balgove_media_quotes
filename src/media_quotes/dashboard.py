"""Streamlit dashboard for offline/blocked-mode replay artifacts.

Import-safe: Streamlit is an optional extra, so importing this module never fails in the core
environment. ``mq dashboard`` prints launch guidance instead of starting a server.
"""

from __future__ import annotations

import csv
import json
import re
from importlib import import_module
from pathlib import Path
from typing import Any

from .config import Settings, get_settings

# Guarded optional import: importing this module must work without the dashboard extra.
st: Any
try:
    st = import_module("streamlit")
except ImportError:
    st = None


def guidance(settings: Settings | None = None) -> str:  # pragma: no cover
    cfg = settings or get_settings()
    return (
        "Dashboard not launched in blocked mode; run "
        "`streamlit run src/media_quotes/dashboard.py`. "
        f"Digests: {cfg.digest_dir}; logs: {cfg.logs_dir}; audit: {cfg.audit_dir}."
    )


def latest_digests(directory: Path, limit: int = 14) -> list[tuple[str, str]]:  # pragma: no cover
    if not directory.is_dir():
        return []
    files = sorted(
        (
            path
            for path in directory.glob("*.md")
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", path.name)
        ),
        reverse=True,
    )[:limit]
    return [(path.stem, path.read_text(encoding="utf-8")) for path in files]


def coverage_gaps(audit_dir: Path) -> list[dict[str, Any]]:  # pragma: no cover
    reports = sorted(audit_dir.glob("audit-*.json"), reverse=True) if audit_dir.is_dir() else []
    if not reports:
        return []
    payload = json.loads(reports[0].read_text(encoding="utf-8"))
    gaps = payload.get("coverage_gaps", []) if isinstance(payload, dict) else []
    return [gap for gap in gaps if isinstance(gap, dict)]


def recent_logs(logs_dir: Path, limit: int = 50) -> list[str]:  # pragma: no cover
    if not logs_dir.is_dir():
        return []
    files = sorted(logs_dir.glob("run-*.jsonl"), reverse=True)
    lines: list[str] = []
    for path in files:
        with path.open(encoding="utf-8") as handle:
            lines.extend(line.strip() for line in handle if line.strip())
        if len(lines) >= limit:
            break
    return lines[:limit]


def schema_summary(schema_sql: Path) -> list[str]:  # pragma: no cover
    if not schema_sql.is_file():
        return []
    text = schema_sql.read_text(encoding="utf-8")
    return re.findall(r"(?i)CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+([\w.]+)", text)


def registered_sources(sources_csv: Path) -> list[dict[str, str]]:  # pragma: no cover
    if not sources_csv.is_file():
        return []
    with sources_csv.open(encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def main(settings: Settings | None = None) -> None:  # pragma: no cover
    """Launch the Streamlit app when installed; otherwise print deterministic guidance."""
    if st is None:
        print(guidance(settings))
        return
    cfg = settings or get_settings()
    load_digests = st.cache_data(ttl=60)(latest_digests)
    load_gaps = st.cache_data(ttl=60)(coverage_gaps)
    load_logs = st.cache_data(ttl=60)(recent_logs)
    load_sources = st.cache_data(ttl=60)(registered_sources)

    st.set_page_config(page_title="Media Quotes — offline replay", layout="wide")
    st.title("Media Quotes — blocked/degraded mode")
    st.info("Synthetic fixture replay only: no live source, database or LLM calls.")

    st.subheader("Latest digests")
    for day, markdown in load_digests(cfg.digest_dir):
        with st.expander(day):
            st.markdown(markdown)

    st.subheader("Coverage gaps")
    gaps = load_gaps(cfg.audit_dir)
    if gaps:
        st.dataframe(gaps)
    else:
        st.write("No gap report yet; run `mq run --date …`.")

    st.subheader("Logs")
    for line in load_logs(cfg.logs_dir):
        st.code(line, language="json")

    st.subheader("Sources (registry and T0 gate)")
    st.dataframe(load_sources(cfg.sources_csv))

    st.subheader("Database schema summary")
    schema_sql = Path(__file__).resolve().parent / "db" / "schema.sql"
    st.write(schema_summary(schema_sql))


__all__ = [
    "coverage_gaps",
    "guidance",
    "latest_digests",
    "main",
    "recent_logs",
    "registered_sources",
    "schema_summary",
]
