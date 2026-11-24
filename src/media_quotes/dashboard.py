"""Streamlit read-only dashboard.

Presents verified quotes, review states, daily digests, and source coverage without
exposing mutating endpoints. A secure read-only interface into the operational state.
"""

from __future__ import annotations

import csv
import hmac
import json
import re
import sys
import time
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


def latest_digests(
    digest_dir: Path, topic_id: str | None = None, limit: int = 14
) -> list[tuple[str, str]]:  # pragma: no cover
    target_dir = digest_dir
    if topic_id and (digest_dir / topic_id).is_dir():
        target_dir = digest_dir / topic_id
    elif topic_id:
        return []
    if not target_dir.is_dir():
        return []
    files = sorted(
        (
            path
            for path in target_dir.glob("*.md")
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", path.name)
        ),
        reverse=True,
    )[:limit]
    return [(path.stem, path.read_text(encoding="utf-8")) for path in files]


def coverage_gaps(
    audit_dir: Path, topic_id: str | None = None
) -> list[dict[str, Any]]:  # pragma: no cover
    payload = _latest_audit(audit_dir, topic_id=topic_id)
    gaps = payload.get("coverage_gaps", []) if isinstance(payload, dict) else []
    return [gap for gap in gaps if isinstance(gap, dict)]


def _latest_audit(audit_dir: Path, topic_id: str | None = None) -> dict[str, Any]:
    if not audit_dir.is_dir():
        return {}
    reports = sorted(audit_dir.rglob("audit-*.json"), reverse=True)
    for path in reports:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and (
                topic_id is None or payload.get("topic_id", "ukraine-war") == topic_id
            ):
                return payload
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def review_queue_view(audit_dir: Path, topic_id: str | None = None) -> list[dict[str, Any]]:
    """Pending obsolescence flags from the latest audit report (E2 review queue)."""
    payload = _latest_audit(audit_dir, topic_id=topic_id)
    queue = payload.get("review_queue", [])
    if isinstance(queue, list):
        return [item for item in queue if isinstance(item, dict)]
    return []


def coverage_summary(audit_dir: Path, topic_id: str | None = None) -> list[dict[str, Any]]:
    """Per-source coverage rows from the latest audit report (E2 coverage view)."""
    payload = _latest_audit(audit_dir, topic_id=topic_id)
    rows = payload.get("coverage", [])
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    return []


def daily_source_coverage(audit_dir: Path, topic_id: str | None = None) -> list[dict[str, Any]]:
    """Collect source coverage records across all audit reports for multi-day analysis."""
    if not audit_dir.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(audit_dir.rglob("audit-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if topic_id and payload.get("topic_id") != topic_id:
            continue
        report_date = str(payload.get("report_date", ""))
        for cov in payload.get("coverage", []):
            if isinstance(cov, dict) and "source_id" in cov:
                records.append(
                    {
                        "date": report_date,
                        "source_id": cov["source_id"],
                        "quotes": cov.get("quotes", cov.get("quotes_found", 0)),
                    }
                )
    return records


def coverage_by_day_matrix(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pivot per-source daily records into a matrix with dates as columns."""
    if not records:
        return []
    dates = sorted({r["date"] for r in records if r.get("date")}, reverse=True)
    sources = sorted({r["source_id"] for r in records if r.get("source_id")})
    grid: dict[str, dict[str, Any]] = {
        s: {"source_id": s, **{d: 0 for d in dates}, "total_quotes": 0} for s in sources
    }
    for r in records:
        s = r.get("source_id")
        d = r.get("date")
        q = int(r.get("quotes", 0))
        if s in grid and d in grid[s]:
            grid[s][d] += q
            grid[s]["total_quotes"] += q
    return sorted(grid.values(), key=lambda row: str(row["source_id"]))


def recent_logs(logs_dir: Path, limit: int = 50) -> list[str]:  # pragma: no cover
    if not logs_dir.is_dir():
        return []
    files = sorted(logs_dir.glob("run*.jsonl"), reverse=True)
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


def database_summary(settings: Settings) -> dict[str, int | str]:  # pragma: no cover
    """Read-only row counts from Postgres; an 'error' key names a failed query."""
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    if __package__:
        from .db.engine import create_db_engine
    else:
        from media_quotes.db.engine import create_db_engine

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
    except SQLAlchemyError as exc:
        return {"error": type(exc).__name__}
    finally:
        engine.dispose()


def graph_projection_summary(cfg: Settings) -> dict[str, Any]:
    """Return Neo4j graph element counts or status if enabled."""
    if not cfg.sync_to_neo4j:
        return {
            "status": "disabled",
            "notes": "Set SYNC_TO_NEO4J=true to enable graph projection.",
        }
    try:
        if __package__:
            from .graph import check_graph_connectivity, get_driver, graph_session
        else:
            from media_quotes.graph import check_graph_connectivity, get_driver, graph_session

        driver = get_driver(cfg)
        if not check_graph_connectivity(driver):
            return {"status": "unreachable", "uri": cfg.neo4j_uri}
        with graph_session(driver, cfg.neo4j_database) as session:
            node_rows = session.run("MATCH (n) RETURN count(n) AS node_count").data()
            edge_rows = session.run("MATCH ()-[r]->() RETURN count(r) AS rel_count").data()
            node_count = node_rows[0]["node_count"] if node_rows else 0
            rel_count = edge_rows[0]["rel_count"] if edge_rows else 0
        return {
            "status": "connected",
            "node_count": node_count,
            "relationship_count": rel_count,
            "uri": cfg.neo4j_uri,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def main(settings: Settings | None = None) -> None:  # pragma: no cover
    """Launch the Streamlit app when installed; otherwise print deterministic guidance."""
    if st is None:
        print(guidance(settings))
        return
    cfg = settings or get_settings()

    # Password extraction: configuration first, Streamlit secrets fallback
    password_val: str | None = None
    if cfg.dashboard_password and cfg.dashboard_password.get_secret_value():
        password_val = cfg.dashboard_password.get_secret_value().strip()
    else:
        try:
            if hasattr(st, "secrets") and "DASHBOARD_PASSWORD" in st.secrets:
                val = st.secrets.get("DASHBOARD_PASSWORD")
                if val is not None:
                    password_val = str(val).strip()
        except Exception:
            password_val = None

    if not password_val:
        st.error("DASHBOARD_PASSWORD is not set; refusing to render internal material.")
        st.stop()
        return

    # Authentication Gate: require matching password
    if hasattr(st, "session_state"):
        state = st.session_state
        if not state.get("authenticated", False):
            st.title("Media Quotes — Login")
            form_ctx = st.form("login_form") if hasattr(st, "form") else None
            entered = ""
            submit = False
            if form_ctx is not None and hasattr(form_ctx, "__enter__"):
                with form_ctx:
                    entered = st.text_input("Dashboard password", type="password")
                    submit = st.form_submit_button("Enter")
            else:
                entered = st.text_input("Dashboard password", type="password")
                submit = True

            if submit:
                locked_until = state.get("auth_locked_until", 0.0)
                if time.monotonic() < locked_until:
                    st.error("Too many attempts. Wait 15 minutes before retrying.")
                elif hmac.compare_digest(entered.encode("utf-8"), password_val.encode("utf-8")):
                    state["authenticated"] = True
                    state["auth_time"] = time.monotonic()
                    state["auth_attempts"] = 0
                    state["auth_locked_until"] = 0.0
                    if hasattr(st, "rerun"):
                        st.rerun()
                else:
                    attempts = int(state.get("auth_attempts", 0)) + 1
                    state["auth_attempts"] = attempts
                    if attempts >= 5:
                        state["auth_locked_until"] = time.monotonic() + 900.0
                        st.error("Too many attempts. Wait 15 minutes before retrying.")
                    else:
                        st.error("Incorrect password.")
            st.stop()
            return
    else:
        entered = (
            st.text_input("Dashboard password", type="password")
            if hasattr(st, "text_input")
            else ""
        )
        if not hmac.compare_digest(str(entered).encode("utf-8"), password_val.encode("utf-8")):
            if hasattr(st, "warning"):
                st.warning("Enter the dashboard password to view internal material.")
            st.stop()
            return

    # Authenticated sidebar controls
    selected_topic = cfg.active_topic
    if hasattr(st, "sidebar"):
        with st.sidebar:
            st.subheader("Session")
            if st.button("Log Out") and hasattr(st, "session_state"):
                st.session_state["authenticated"] = False
                if hasattr(st, "rerun"):
                    st.rerun()

            st.subheader("Topic")
            available_topics = [cfg.active_topic]
            if cfg.topics_dir.is_dir():
                for p in sorted(cfg.topics_dir.glob("*.json")):
                    if p.name.endswith(".schema.json"):
                        continue
                    if p.stem not in available_topics:
                        available_topics.append(p.stem)
            selected_topic = st.selectbox("Active Topic", available_topics, index=0)

    # Dynamic mode banner
    if cfg.persist_to_postgres and cfg.llm_provider == "openrouter":
        mode_desc = "live Postgres + OpenRouter"
    elif cfg.persist_to_postgres:
        mode_desc = "Postgres persistent"
    else:
        mode_desc = "blocked/degraded (synthetic fixture replay)"
    st.title("Media Quotes — Operational Dashboard")
    st.info(f"Mode: **{mode_desc}** | Active Topic: **{selected_topic}**")

    st.subheader("Latest digests")
    digests = latest_digests(cfg.digest_dir, topic_id=selected_topic)
    if digests:
        for day, markdown in digests:
            with st.expander(day):
                st.markdown(markdown)
    else:
        st.write("No digests generated yet for this topic.")

    latest_audit = _latest_audit(cfg.audit_dir, topic_id=selected_topic)
    report_topic = str(latest_audit.get("topic_id", selected_topic))
    report_date = str(latest_audit.get("report_date", "none"))
    st.caption(f"Latest audit report: topic {report_topic} | date {report_date}")

    st.subheader("Per-source coverage")
    cov = coverage_summary(cfg.audit_dir, topic_id=selected_topic)
    if cov:
        st.dataframe(cov)
    else:
        st.write("No coverage data found in latest audit.")

    st.subheader("Per-source coverage by day")
    daily_cov = daily_source_coverage(cfg.audit_dir, topic_id=selected_topic)
    if daily_cov:
        st.dataframe(coverage_by_day_matrix(daily_cov))
    else:
        st.write("No multi-day coverage data found.")

    st.subheader("Coverage gaps")
    gaps = coverage_gaps(cfg.audit_dir, topic_id=selected_topic)
    if gaps:
        st.dataframe(gaps)
    else:
        st.write("No gap report yet; run `mq run --date …`.")

    st.subheader("Recent logs")
    logs = recent_logs(cfg.logs_dir)
    if logs:
        for line in logs:
            st.code(line, language="json")
    else:
        st.write("No log files found.")

    st.subheader("Sources (registry and T0 gate)")
    sources = registered_sources(cfg.sources_csv)
    if sources:
        st.dataframe(sources)

    st.subheader("Database schema summary")
    schema_sql = Path(__file__).resolve().parent / "db" / "schema.sql"
    st.write(schema_summary(schema_sql))
    if cfg.persist_to_postgres:
        counts = database_summary(cfg)
        if "error" in counts:
            st.write(f"Postgres query failed: {counts['error']}")
        elif counts:
            st.subheader("Database row counts (read-only)")
            st.dataframe([{"table": name, "rows": rows} for name, rows in counts.items()])
        else:
            st.write("Postgres is enabled but no counts were returned.")

    if cfg.sync_to_neo4j:
        st.subheader("Graph projection summary (Neo4j)")
        g_info = graph_projection_summary(cfg)
        if g_info.get("status") == "connected":
            st.dataframe(
                [
                    {"metric": "Nodes", "count": g_info.get("node_count", 0)},
                    {"metric": "Relationships", "count": g_info.get("relationship_count", 0)},
                ]
            )
        else:
            st.write(f"Neo4j status: {g_info.get('status')} ({g_info.get('uri', '')})")


__all__ = [
    "coverage_by_day_matrix",
    "coverage_gaps",
    "coverage_summary",
    "daily_source_coverage",
    "database_summary",
    "graph_projection_summary",
    "guidance",
    "latest_digests",
    "main",
    "recent_logs",
    "registered_sources",
    "review_queue_view",
    "schema_summary",
]

if __name__ == "__main__":
    main()
