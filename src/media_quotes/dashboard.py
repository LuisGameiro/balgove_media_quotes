"""Streamlit read-only dashboard.

Presents verified quotes, review states, daily digests, and source coverage without
exposing mutating endpoints. A secure read-only interface into the operational state.
"""

from __future__ import annotations

import contextlib
import csv
import hmac
import json
import os
import re
import sys
import time
from collections.abc import Callable, Mapping
from datetime import date, timedelta
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

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
DEFAULT_TOPIC_ID: str = config_module.DEFAULT_TOPIC_ID

AUTH_TTL_SECONDS = 8 * 60 * 60
AUTH_MAX_ATTEMPTS = 5
AUTH_LOCK_SECONDS = 15 * 60
AUTH_LOCK_STALE_SECONDS = 60.0
AUTH_STATE_KEYS = ("attempts", "locked_until", "updated_at")
FIXTURE_DIGEST_MARKER = "OFFLINE FIXTURE REPLAY"


def _legacy_provenance(payload: dict[str, Any]) -> bool:
    """Legacy artifacts have no provenance or are explicitly fixture-sourced."""
    return payload.get("provenance") in (None, "fixture")


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


def _topic_artifact_dirs(root: Path, topic_id: str | None) -> list[Path]:
    if not root.is_dir():
        return []
    if topic_id and topic_id != DEFAULT_TOPIC_ID:
        target = root / topic_id
        return [target] if target.is_dir() else []
    dirs = [root]
    if topic_id and (root / topic_id).is_dir():
        dirs.append(root / topic_id)
    return dirs


def latest_digests(
    digest_dir: Path, topic_id: str | None = None, limit: int = 14
) -> list[tuple[str, str]]:  # pragma: no cover
    for root in (digest_dir, digest_dir / "fixtures"):
        for target_dir in _topic_artifact_dirs(root, topic_id):
            files = sorted(
                (
                    path
                    for path in target_dir.glob("*.md")
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", path.name)
                ),
                reverse=True,
            )
            digests: list[tuple[str, str]] = []
            for path in files:
                text = path.read_text(encoding="utf-8")
                if root != digest_dir / "fixtures" and FIXTURE_DIGEST_MARKER in text:
                    continue
                digests.append((path.stem, text))
                if len(digests) >= limit:
                    break
            if digests:
                return digests
    return []


def _audit_report_trees(audit_dir: Path) -> list[list[Path]]:
    """Group audit reports by provenance tree: canonical live first, then fixtures.

    A tree never includes another tree, so live and fixture counts are never mixed.
    """
    fixture_root = audit_dir / "fixtures"
    excluded = (fixture_root, audit_dir / "runs")
    trees: list[list[Path]] = []
    for root, skip in ((audit_dir, excluded), (fixture_root, ())):
        if not root.is_dir():
            continue
        trees.append(
            sorted(
                path
                for path in root.rglob("audit-*.json")
                if all(not path.is_relative_to(other) for other in skip)
            )
        )
    return trees


def _latest_audit(audit_dir: Path, topic_id: str | None = None) -> dict[str, Any]:
    for reports in _audit_report_trees(audit_dir):
        for path in reversed(reports):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    continue
                # Canonical artifacts are live-sourced; legacy fixtures live only in fixtures/.
                if not path.is_relative_to(audit_dir / "fixtures") and _legacy_provenance(payload):
                    continue
                if topic_id is None or payload.get("topic_id", DEFAULT_TOPIC_ID) == topic_id:
                    return payload
            except (OSError, ValueError):
                continue
    return {}


_AUDIT_DATED_NAME = re.compile(r"^audit-(\d{4}-\d{2}-\d{2})\.json$")


def _within_window(path: Path, start: date, end: date) -> bool:
    """Dated audit files must fall in [start, end]; malformed names fail open."""
    match = _AUDIT_DATED_NAME.fullmatch(path.name)
    if match is None:
        return True
    try:
        day = date.fromisoformat(match.group(1))
    except ValueError:
        return True
    return start <= day <= end


def daily_source_coverage(
    audit_dir: Path,
    topic_id: str | None = None,
    *,
    report_window_days: int | None = None,
) -> list[dict[str, Any]]:
    """Collect per-source coverage from the first provenance tree with matching reports.

    Live and fixture trees are never combined, so a source/day is not double counted.
    Only filenames dated inside the recent window are opened; malformed names fail open.
    """
    if report_window_days is None:
        report_window_days = get_settings().report_window_days
    today = date.today()
    window_start = today - timedelta(days=max(report_window_days, 1) - 1)
    for reports in _audit_report_trees(audit_dir):
        records: list[dict[str, Any]] = []
        matched = False
        for path in reports:
            if not _within_window(path, window_start, today):
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            if not path.is_relative_to(audit_dir / "fixtures") and _legacy_provenance(payload):
                continue
            if topic_id and payload.get("topic_id") != topic_id:
                continue
            matched = True
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
        if matched:
            return records
    return []


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


def browse_quotes(
    export_dir: Path, topic_id: str | None = None, limit: int = 500
) -> list[dict[str, Any]]:
    """Read governed JSONL exports for one topic, newest files first."""
    if limit <= 0 or not export_dir.is_dir():
        return []

    display_fields = (
        "quote_id",
        "publication_date",
        "source_id",
        "source_name",
        "source_tier",
        "section_id",
        "claim_only",
        "is_obsolete",
        "quote_text_original",
        "quote_text_en",
        "original_reporting_flag",
        "rights_status",
        "article_name",
        "article_author",
        "article_url",
        "topic_id",
        "topic_status",
        "topic_score",
    )
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in (export_dir, export_dir / "fixtures"):
        for target_dir in _topic_artifact_dirs(root, topic_id):
            for path in sorted(target_dir.glob("quotes-*.jsonl"), reverse=True):
                try:
                    with path.open(encoding="utf-8") as handle:
                        for line in handle:
                            if not line.strip():
                                continue
                            try:
                                record = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            if not isinstance(record, dict):
                                continue
                            if root != export_dir / "fixtures" and _legacy_provenance(record):
                                continue
                            if topic_id and record.get("topic_id", DEFAULT_TOPIC_ID) != topic_id:
                                continue
                            quote_id = str(record.get("quote_id") or "")
                            if not quote_id or quote_id in seen:
                                continue
                            seen.add(quote_id)
                            rows.append(
                                {key: record[key] for key in display_fields if key in record}
                            )
                            # shortcut: cap at 500 newest quotes; add pagination if needed.
                            if len(rows) >= limit:
                                return rows
                except OSError:
                    continue
        if rows:
            return rows
    return rows


def filter_quotes(
    rows: list[dict[str, Any]],
    *,
    text: str = "",
    source_id: str = "",
    section_id: str = "",
    include_obsolete: bool = False,
) -> list[dict[str, Any]]:
    """Apply cheap interactive filters after the cached JSONL load."""
    needle = text.strip().casefold()
    source = source_id.strip().casefold()
    section = section_id.strip().casefold()
    filtered: list[dict[str, Any]] = []
    for row in rows:
        if not include_obsolete and row.get("is_obsolete"):
            continue
        if source and source not in str(row.get("source_id") or "").casefold():
            continue
        if section and section not in str(row.get("section_id") or "").casefold():
            continue
        if needle and not any(
            needle in str(row.get(field) or "").casefold()
            for field in ("quote_text_original", "quote_text_en", "article_name")
        ):
            continue
        filtered.append(row)
    return filtered


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


def _db_counts(database_url: str) -> dict[str, int | str]:  # pragma: no cover
    """Read-only row counts keyed by a hashable URL, so Streamlit can cache them."""
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    if __package__:
        from .db.engine import create_db_engine_from_url
    else:
        from media_quotes.db.engine import create_db_engine_from_url

    engine = create_db_engine_from_url(database_url)
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


def database_summary(settings: Settings) -> dict[str, int | str]:  # pragma: no cover
    """Read-only row counts from Postgres; an 'error' key names a failed query."""
    return cast(dict[str, int | str], _db_counts_cached(settings.database_url_value))


def graph_projection_summary(cfg: Settings) -> dict[str, Any]:
    """Return Neo4j graph element counts or status if enabled."""
    if not cfg.sync_to_neo4j:
        return {
            "status": "disabled",
            "notes": "Set SYNC_TO_NEO4J=true to enable graph projection.",
        }
    driver = None
    try:
        if __package__:
            from .graph import (
                check_graph_connectivity,
                fetch_sync_metadata,
                get_driver,
                graph_session,
            )
        else:
            from media_quotes.graph import (
                check_graph_connectivity,
                fetch_sync_metadata,
                get_driver,
                graph_session,
            )

        driver = get_driver(cfg)
        if not check_graph_connectivity(driver):
            return {"status": "unreachable", "uri": cfg.neo4j_uri}
        with graph_session(driver, cfg.neo4j_database) as session:
            node_rows = session.run("MATCH (n) RETURN count(n) AS node_count").data()
            edge_rows = session.run("MATCH ()-[r] RETURN count(r) AS rel_count").data()
            node_count = node_rows[0]["node_count"] if node_rows else 0
            edge_count = edge_rows[0]["rel_count"] if edge_rows else 0
            metadata = fetch_sync_metadata(session)
        return {
            "status": "connected",
            "node_count": node_count,
            "relationship_count": edge_count,
            "uri": cfg.neo4j_uri,
            **metadata,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
    finally:
        if driver is not None:
            driver.close()


if st is not None and hasattr(st, "cache_data"):
    _latest_audit_cached = st.cache_data(ttl="30s", max_entries=32)(_latest_audit)
    latest_digests_cached = st.cache_data(ttl="30s", max_entries=32)(latest_digests)
    daily_source_coverage_cached = st.cache_data(ttl="30s", max_entries=32)(daily_source_coverage)
    browse_quotes_cached = st.cache_data(ttl="30s", max_entries=16)(browse_quotes)
    _db_counts_cached = st.cache_data(ttl="30s", max_entries=8)(_db_counts)
else:  # pragma: no cover - Streamlit is optional for CLI imports
    _latest_audit_cached = _latest_audit
    latest_digests_cached = latest_digests
    daily_source_coverage_cached = daily_source_coverage
    browse_quotes_cached = browse_quotes
    _db_counts_cached = _db_counts


def _session_active(state: Any, now: float) -> bool:
    """True only for an authenticated session inside the 8-hour auth_time window."""
    if not isinstance(state, Mapping) or not state.get("authenticated", False):
        return False
    auth_time = state.get("auth_time")
    if isinstance(auth_time, bool) or not isinstance(auth_time, (int, float)):
        return False
    return 0 <= now - auth_time < AUTH_TTL_SECONDS


def _auth_state_path(logs_dir: Path) -> Path:
    return logs_dir / ".dashboard-auth.json"


def _read_auth_state(logs_dir: Path) -> dict[str, int | float]:
    """Read only the three allowed counters; never load arbitrary stored keys."""
    state: dict[str, int | float] = {"attempts": 0, "locked_until": 0.0, "updated_at": 0.0}
    try:
        raw = json.loads(_auth_state_path(logs_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return state
    if isinstance(raw, dict):
        for key in AUTH_STATE_KEYS:
            value = raw.get(key)
            if not isinstance(value, bool) and isinstance(value, (int, float)):
                state[key] = value
    return state


def _write_auth_state(logs_dir: Path, state: dict[str, int | float]) -> None:
    path = _auth_state_path(logs_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Only counters are persisted: never a password, IP, token or session identifier.
    path.write_text(json.dumps({key: state[key] for key in AUTH_STATE_KEYS}), encoding="utf-8")


def _mutate_auth_state(
    logs_dir: Path, mutate: Callable[[dict[str, int | float]], dict[str, int | float]]
) -> dict[str, int | float]:
    """Locked read-modify-write; contention or IO errors fail open with the last read."""
    last_read = _read_auth_state(logs_dir)
    path = _auth_state_path(logs_dir)
    lock = path.with_name(path.name + ".lock")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        for _attempt in range(2):
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                break
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime <= AUTH_LOCK_STALE_SECONDS:
                        return last_read
                    lock.unlink(missing_ok=True)
                except OSError:
                    return last_read
        else:  # pragma: no cover - both attempts were removed by another process
            return last_read
    except OSError:
        return last_read
    try:
        state = mutate(_read_auth_state(logs_dir))
        _write_auth_state(logs_dir, state)
        return state
    except OSError:
        return last_read
    finally:
        os.close(fd)
        with contextlib.suppress(OSError):
            lock.unlink()


def _record_auth_failure(logs_dir: Path, now: float) -> dict[str, int | float]:
    def mutate(state: dict[str, int | float]) -> dict[str, int | float]:
        attempts = int(state["attempts"]) + 1
        state["attempts"] = attempts
        state["updated_at"] = now
        if attempts >= AUTH_MAX_ATTEMPTS:
            state["locked_until"] = now + AUTH_LOCK_SECONDS
        return state

    return _mutate_auth_state(logs_dir, mutate)


def _reset_auth_state(logs_dir: Path, now: float) -> dict[str, int | float]:
    def mutate(state: dict[str, int | float]) -> dict[str, int | float]:
        return {"attempts": 0, "locked_until": 0.0, "updated_at": now}

    return _mutate_auth_state(logs_dir, mutate)


def _is_locked(state: dict[str, int | float], now: float) -> bool:
    return now < state["locked_until"]


def _render_overview(
    audit: dict[str, Any],
    coverage: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    queue: list[dict[str, Any]],
    source_count: int,
) -> None:
    with st.container(border=True):
        st.subheader("Run overview")
        quote_count = sum(
            int(row.get("quotes", row.get("quotes_found", 0)) or 0) for row in coverage
        )
        pending = sum(
            1 for flag in queue if flag.get("state") in {"pending", "needs_evidence", "escalated"}
        )
        with st.container(horizontal=True):
            st.metric("Report date", str(audit.get("report_date", "none")), border=True)
            st.metric("Verified quotes", quote_count, border=True)
            st.metric("Coverage gaps", len(gaps), border=True)
            st.metric("Pending review", pending, border=True)
            st.metric("Registered sources", source_count, border=True)
        chart_data = [
            {
                "source_id": str(row.get("source_id") or "unknown"),
                "quotes": int(row.get("quotes", row.get("quotes_found", 0)) or 0),
            }
            for row in coverage
        ]
        if chart_data:
            st.bar_chart(
                chart_data,
                x="source_id",
                y="quotes",
                alt="Verified quotes by source for the latest audit",
            )


def _render_coverage(
    cfg: Settings, topic: str, coverage: list[dict[str, Any]], gaps: list[dict[str, Any]]
) -> None:
    with st.container(border=True):
        st.subheader("Per-source coverage")
        if coverage:
            st.dataframe(coverage, hide_index=True, alt="Latest per-source coverage")
        else:
            st.write("No coverage data found in latest audit.")
        st.subheader("Per-source coverage by day")
        daily = daily_source_coverage_cached(
            cfg.audit_dir, topic_id=topic, report_window_days=cfg.report_window_days
        )
        if daily:
            st.dataframe(
                coverage_by_day_matrix(daily),
                hide_index=True,
                alt="Per-source quote counts by day",
            )
        else:
            st.write("No multi-day coverage data found.")
        st.subheader("Coverage gaps")
        if gaps:
            st.dataframe(gaps, hide_index=True, alt="Latest coverage gaps")
        else:
            st.write("No gap report yet; run `mq run --date …`.")


def _render_digests_and_logs(cfg: Settings, topic: str) -> None:
    with st.container(border=True):
        st.subheader("Latest digests")
        digests = latest_digests_cached(cfg.digest_dir, topic_id=topic)
        if digests:
            for day, markdown in digests:
                with st.expander(day):
                    st.markdown(markdown)
        else:
            st.write("No digests generated yet for this topic.")
        st.subheader("Recent logs")
        logs = recent_logs(cfg.logs_dir)
        if logs:
            for line in logs:
                st.code(line, language="json")
        else:
            st.write("No log files found.")


def _render_quotes(export_dir: Path, topic: str) -> None:
    with st.container(border=True):
        st.subheader("Browse quotes (read-only)")
        rows = browse_quotes_cached(export_dir, topic_id=topic)
        if not rows:
            st.caption("No JSONL quote files found. Run `mq run --date ...` first.")
            return
        with st.expander("Filters", expanded=True):
            text = st.text_input("Quote or article contains", key="qb_text", type="search")
            source = st.text_input("Source ID", key="qb_source")
            section = st.text_input("Section", key="qb_section")
            include_obsolete = st.checkbox("Include obsolete", value=False, key="qb_obsolete")
        filtered = filter_quotes(
            rows,
            text=text,
            source_id=source,
            section_id=section,
            include_obsolete=include_obsolete,
        )
        dataframe_options: dict[str, Any] = {}
        if hasattr(st, "column_config"):
            dataframe_options["column_config"] = {
                "article_url": st.column_config.LinkColumn("Article")
            }
        st.dataframe(
            filtered,
            hide_index=True,
            alt="Governed verified quotes with article metadata",
            **dataframe_options,
        )
        st.caption(
            f"Showing {len(filtered)} of {len(rows)} quotes. "
            "Data is read-only; article bodies are not exported."
        )


def _render_data(cfg: Settings, schema_sql: Path, queue: list[dict[str, Any]]) -> None:
    with st.container(border=True):
        st.subheader("Obsolescence review queue")
        if queue:
            st.dataframe(queue, hide_index=True, alt="Obsolescence review queue")
        else:
            st.write("No review flags in the latest audit.")

        st.subheader("Sources (registry and T0 gate)")
        sources = registered_sources(cfg.sources_csv)
        if sources:
            st.dataframe(sources, hide_index=True, alt="Registered sources and rights gate")

        st.subheader("Database schema summary")
        st.write(schema_summary(schema_sql))
        if cfg.persist_to_postgres:
            counts = database_summary(cfg)
            if "error" in counts:
                st.write(f"Postgres query failed: {counts['error']}")
            elif counts:
                st.subheader("Database row counts (read-only)")
                st.dataframe(
                    [{"table": name, "rows": rows} for name, rows in counts.items()],
                    hide_index=True,
                    alt="Read-only Postgres row counts",
                )
            else:
                st.write("Postgres is enabled but no counts were returned.")

        if cfg.sync_to_neo4j:
            st.subheader("Graph projection summary (Neo4j)")
            graph_info = graph_projection_summary(cfg)
            if graph_info.get("status") == "connected":
                st.dataframe(
                    [
                        {"metric": "Nodes", "count": graph_info.get("node_count", 0)},
                        {
                            "metric": "Relationships",
                            "count": graph_info.get("relationship_count", 0),
                        },
                    ],
                    hide_index=True,
                    alt="Neo4j projection element counts",
                )
            else:
                st.write(f"Neo4j status: {graph_info.get('status')} ({graph_info.get('uri', '')})")


def main(settings: Settings | None = None) -> None:  # pragma: no cover
    """Launch the Streamlit app when installed; otherwise print deterministic guidance."""
    if st is None:
        print(guidance(settings))
        return
    st.set_page_config(page_title="Media quotes", layout="wide")
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

    # Authentication Gate: 8-hour session TTL plus a server-wide attempt state
    if hasattr(st, "session_state"):
        state = st.session_state
        now = time.time()
        if not _session_active(state, now):
            state.pop("auth_time", None)
            state["authenticated"] = False

        if not state.get("authenticated", False):
            # Check the server-wide lockout before rendering any login form.
            if _is_locked(_read_auth_state(cfg.logs_dir), now):
                st.error("Too many attempts. Wait 15 minutes before retrying.")
                st.stop()
                return
            st.title("Media quotes — Login")
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
                if hmac.compare_digest(entered.encode("utf-8"), password_val.encode("utf-8")):
                    _reset_auth_state(cfg.logs_dir, now)
                    state["authenticated"] = True
                    state["auth_time"] = now
                    if hasattr(st, "rerun"):
                        st.rerun()
                else:
                    updated = _record_auth_failure(cfg.logs_dir, now)
                    if _is_locked(updated, now):
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
                for path in sorted(cfg.topics_dir.glob("*.json")):
                    if path.name.endswith(".schema.json"):
                        continue
                    if path.stem not in available_topics:
                        available_topics.append(path.stem)
            selected_topic = st.selectbox("Active topic", available_topics, index=0)

    if cfg.persist_to_postgres and cfg.llm_provider == "openrouter":
        mode_desc = "live Postgres + OpenRouter"
    elif cfg.persist_to_postgres:
        mode_desc = "Postgres persistent"
    else:
        mode_desc = "blocked/degraded (synthetic fixture replay)"
    st.title("Media quotes — operational dashboard")
    if hasattr(st, "badge"):
        st.badge(f"Mode: {mode_desc}")
    else:  # pragma: no cover - older Streamlit
        st.info(f"Mode: {mode_desc}")
    st.caption(f"Active topic: {selected_topic}")

    audit = _latest_audit_cached(cfg.audit_dir, topic_id=selected_topic)
    coverage_rows = audit.get("coverage", [])
    coverage = (
        [row for row in coverage_rows if isinstance(row, dict)]
        if isinstance(coverage_rows, list)
        else []
    )
    gap_rows = audit.get("coverage_gaps", [])
    gaps = [row for row in gap_rows if isinstance(row, dict)] if isinstance(gap_rows, list) else []
    queue_rows = audit.get("review_queue", [])
    queue = (
        [row for row in queue_rows if isinstance(row, dict)] if isinstance(queue_rows, list) else []
    )
    sources = registered_sources(cfg.sources_csv)
    schema_sql = Path(__file__).resolve().parent / "db" / "schema.sql"

    tabs = st.tabs(
        ["Overview", "Coverage", "Digests and logs", "Quotes", "Data"],
        on_change="rerun",
    )
    overview, coverage_tab, digests_tab, quotes_tab, data_tab = tabs
    if overview.open:
        with overview:
            _render_overview(audit, coverage, gaps, queue, len(sources))
    if coverage_tab.open:
        with coverage_tab:
            _render_coverage(cfg, selected_topic, coverage, gaps)
    if digests_tab.open:
        with digests_tab:
            _render_digests_and_logs(cfg, selected_topic)
    if quotes_tab.open:
        with quotes_tab:
            _render_quotes(cfg.export_dir, selected_topic)
    if data_tab.open:
        with data_tab:
            _render_data(cfg, schema_sql, queue)


__all__ = [
    "browse_quotes",
    "coverage_by_day_matrix",
    "daily_source_coverage",
    "database_summary",
    "filter_quotes",
    "graph_projection_summary",
    "guidance",
    "latest_digests",
    "main",
    "recent_logs",
    "registered_sources",
    "schema_summary",
]

if __name__ == "__main__":
    main()
