"""Streamlit dashboard for offline/blocked-mode replay artifacts.

Import-safe: Streamlit is an optional extra, so importing this module never fails in the core
environment. ``mq dashboard`` prints launch guidance instead of starting a server.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import Settings

# Streamlit executes this file directly, leaving __package__ empty, so resolve the
# config module both as a package import and as a standalone script.
if __package__:
    config_module = import_module(".config", __package__)
else:
    src_dir = str(Path(__file__).resolve().parents[1])
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    config_module = import_module("media_quotes.config")

get_settings: Any = config_module.get_settings

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
    reports = sorted(audit_dir.rglob("audit-*.json"), reverse=True) if audit_dir.is_dir() else []
    if not reports:
        return []
    payload = json.loads(reports[0].read_text(encoding="utf-8"))
    gaps = payload.get("coverage_gaps", []) if isinstance(payload, dict) else []
    return [gap for gap in gaps if isinstance(gap, dict)]


def _latest_audit(audit_dir: Path) -> dict[str, Any]:
    reports = sorted(audit_dir.rglob("audit-*.json"), reverse=True) if audit_dir.is_dir() else []
    if not reports:
        return {}
    payload = json.loads(reports[0].read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def review_queue_view(audit_dir: Path) -> list[dict[str, Any]]:
    """Pending obsolescence flags from the latest audit report (E2 review queue)."""
    payload = _latest_audit(audit_dir)
    queue = payload.get("review_queue", [])
    return [item for item in queue if isinstance(item, dict)]


def coverage_summary(audit_dir: Path) -> list[dict[str, Any]]:
    """Per-source coverage rows from the latest audit report (E2 coverage view)."""
    payload = _latest_audit(audit_dir)
    rows = payload.get("coverage", [])
    return [row for row in rows if isinstance(row, dict)]


def recent_logs(logs_dir: Path, limit: int = 50) -> list[str]:  # pragma: no cover
    if not logs_dir.is_dir():
        return []
    files = sorted(logs_dir.glob("run-*.jsonl"), reverse=True)
    lines: list[str] = []
    for path in files:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    lines.append(line.strip())
                    if len(lines) >= limit:
                        return lines
    return lines


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


def database_summary(settings: Settings) -> dict[str, int]:  # pragma: no cover
    """Read-only row counts from Postgres; empty when the DB is off or unreachable."""
    from sqlalchemy import text

    from .db.engine import create_db_engine

    engine = create_db_engine(settings)
    try:
        counts_sql = {
            "sources": text("SELECT count(*) FROM sources"),
            "articles": text("SELECT count(*) FROM articles"),
            "quotes": text("SELECT count(*) FROM quotes"),
            "pipeline_runs": text("SELECT count(*) FROM pipeline_runs"),
            "audit_events": text("SELECT count(*) FROM audit_events"),
        }
        with engine.connect() as connection:
            return {
                name: int(connection.execute(statement).scalar_one())
                for name, statement in counts_sql.items()
            }
    except Exception:
        return {}
    finally:
        engine.dispose()


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
    load_review = st.cache_data(ttl=60)(review_queue_view)
    load_coverage = st.cache_data(ttl=60)(coverage_summary)

    st.set_page_config(page_title="Media Quotes — offline replay", layout="wide")
    st.title("Media Quotes — blocked/degraded mode")
    st.info("Synthetic fixture replay only: no live source, database or LLM calls.")

    st.subheader("Latest digests")
    for day, markdown in load_digests(cfg.digest_dir):
        with st.expander(day):
            st.markdown(markdown)

    report_topic = str(_latest_audit(cfg.audit_dir).get("topic_id", "ukraine-war"))
    st.caption(f"Latest audit report: topic {report_topic}")
    st.subheader("Per-source coverage")
    st.dataframe(load_coverage(cfg.audit_dir))

    st.subheader("Obsolescence review queue")
    pending = load_review(cfg.audit_dir)
    if pending:
        st.dataframe(pending)
    else:
        st.write("No pending review flags.")

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
    if cfg.persist_to_postgres:
        counts = database_summary(cfg)
        if counts:
            st.subheader("Database row counts (read-only)")
            st.dataframe([{"table": name, "rows": rows} for name, rows in counts.items()])
        else:
            st.write("Postgres is enabled but unreachable.")


__all__ = [
    "coverage_gaps",
    "coverage_summary",
    "guidance",
    "latest_digests",
    "main",
    "recent_logs",
    "registered_sources",
    "review_queue_view",
    "schema_summary",
]
