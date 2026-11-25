from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pydantic import SecretStr

import media_quotes.dashboard as dashboard
from media_quotes.config import Settings


class MockSt:
    """Mock Streamlit context for driving dashboard view execution."""

    def __init__(
        self,
        entered_password: str = "",
        authenticated: bool = True,
        button_clicks: dict[str, bool] | None = None,
        has_session_state: bool = True,
    ) -> None:
        if has_session_state:
            self.session_state: dict[str, Any] = {
                "authenticated": authenticated,
                "auth_time": time.time() if authenticated else 0.0,
            }
        self.sidebar = self
        self._entered = entered_password
        self._button_clicks = button_clicks or {}
        self.titles: list[str] = []
        self.infos: list[str] = []
        self.subheaders: list[str] = []
        self.captions: list[str] = []
        self.writes: list[Any] = []
        self.dataframes: list[Any] = []
        self.codes: list[str] = []
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.metrics: list[tuple[str, Any]] = []
        self.badges: list[str] = []
        self.charts: list[Any] = []
        self.tabs_created: list[str] = []

    def __enter__(self) -> MockSt:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass

    def set_page_config(self, *a: Any, **kw: Any) -> None:
        pass

    def title(self, text: str) -> None:
        self.titles.append(text)

    def subheader(self, text: str) -> None:
        self.subheaders.append(text)

    def caption(self, text: str) -> None:
        self.captions.append(text)

    def info(self, text: str) -> None:
        self.infos.append(text)

    def write(self, *a: Any) -> None:
        self.writes.extend(a)

    def dataframe(self, data: Any, **kwargs: Any) -> None:
        self.dataframes.append(data)

    def code(self, text: str, language: str = "", **kwargs: Any) -> None:
        self.codes.append(text)

    def container(self, **kwargs: Any) -> MockSt:
        return self

    def metric(self, label: str, value: Any, delta: Any = None, **kwargs: Any) -> None:
        self.metrics.append((label, value))

    def badge(self, label: str, **kwargs: Any) -> None:
        self.badges.append(label)

    def bar_chart(self, data: Any, **kwargs: Any) -> None:
        self.charts.append(data)

    def checkbox(self, label: str, value: bool = False, **kwargs: Any) -> bool:
        return value

    def tabs(self, names: list[str], **kwargs: Any) -> list[MockTab]:
        self.tabs_created = list(names)
        return [MockTab() for _ in names]

    def error(self, text: str) -> None:
        self.errors.append(text)

    def warning(self, text: str) -> None:
        self.warnings.append(text)

    def expander(self, name: str, **kwargs: Any) -> MockSt:
        return self

    def markdown(self, text: str) -> None:
        pass

    def button(self, label: str, **kwargs: Any) -> bool:
        return self._button_clicks.get(label, False)

    def selectbox(self, label: str, options: list[str], index: int = 0, **kwargs: Any) -> str:
        return options[index] if options else ""

    def text_input(self, label: str, type: str = "", **kwargs: Any) -> str:
        return self._entered

    def form(self, name: str) -> MockSt:
        return self

    def form_submit_button(self, label: str) -> bool:
        return True

    def stop(self) -> None:
        pass

    def rerun(self) -> None:
        pass


class MockTab(MockSt):
    """Tab container; all tabs render so every branch stays covered."""

    def __init__(self) -> None:
        super().__init__()
        self.open = True


def test_dashboard_full_authenticated_flow(tmp_path: Path, monkeypatch: Any) -> None:
    # Set up mock folders
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "audit-2026-11-20.json").write_text(
        json.dumps(
            {
                "topic_id": "ukraine-war",
                "provenance": "live_feed",
                "report_date": "2026-11-20",
                "coverage": [{"source_id": "reuters", "status": "blocked"}],
                "coverage_gaps": [{"reason": "quota"}],
                "review_queue": [{"quote_id": "Q1", "state": "pending"}],
            }
        ),
        encoding="utf-8",
    )

    digests_dir = tmp_path / "digests"
    digests_dir.mkdir(parents=True, exist_ok=True)
    (digests_dir / "2026-11-20.md").write_text("# Digest 2026-11-20\nContent", encoding="utf-8")

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_content = '{"event": "start"}\n{"event": "done"}\n'
    (logs_dir / "run1.jsonl").write_text(log_content, encoding="utf-8")

    topics_dir = tmp_path / "topics"
    topics_dir.mkdir(parents=True, exist_ok=True)
    (topics_dir / "ukraine-war.json").write_text("{}", encoding="utf-8")
    (topics_dir / "ai-policy.json").write_text("{}", encoding="utf-8")

    sources_csv = tmp_path / "sources.csv"
    sources_csv.write_text(
        "source_id,publication_name,tier,usable,access_mode,rights_status\n"
        "reuters,Reuters,1,false,fixture_replay,pending\n",
        encoding="utf-8",
    )

    settings = Settings(
        _env_file=None,
        dashboard_password=SecretStr("supersecretpassword123"),
        audit_dir=audit_dir,
        digest_dir=digests_dir,
        logs_dir=logs_dir,
        topics_dir=topics_dir,
        sources_csv=sources_csv,
        persist_to_postgres=False,
    )

    mock_st = MockSt(authenticated=True, button_clicks={"Log Out": True})
    monkeypatch.setattr(dashboard, "st", mock_st)

    dashboard.main(settings=settings)

    assert "Media quotes — operational dashboard" in mock_st.titles
    assert any("ukraine-war" in caption for caption in mock_st.captions)
    assert any(len(df) > 0 for df in mock_st.dataframes)
    assert len(mock_st.codes) > 0
    assert len(mock_st.metrics) == 5
    assert mock_st.tabs_created == ["Overview", "Coverage", "Digests and logs", "Quotes", "Data"]
    assert mock_st.charts  # quotes-by-source bar chart
    assert any("pending" in str(label).lower() for label, _ in mock_st.metrics)
    assert mock_st.session_state["authenticated"] is False


def test_dashboard_login_submission(tmp_path: Path, monkeypatch: Any) -> None:
    settings = Settings(
        _env_file=None,
        dashboard_password=SecretStr("supersecretpassword123"),
        audit_dir=tmp_path / "audit",
        digest_dir=tmp_path / "digests",
        logs_dir=tmp_path / "logs",
        topics_dir=tmp_path / "topics",
        sources_csv=tmp_path / "sources.csv",
    )

    # 1. Incorrect password
    mock_st = MockSt(entered_password="wrongpassword", authenticated=False)  # noqa: S106
    monkeypatch.setattr(dashboard, "st", mock_st)
    dashboard.main(settings=settings)
    assert "Incorrect password." in mock_st.errors
    assert mock_st.session_state["authenticated"] is False

    # 2. Correct password
    mock_st = MockSt(entered_password="supersecretpassword123", authenticated=False)  # noqa: S106
    monkeypatch.setattr(dashboard, "st", mock_st)
    dashboard.main(settings=settings)
    assert mock_st.session_state["authenticated"] is True


def test_dashboard_locks_after_repeated_bad_passwords(tmp_path: Path, monkeypatch: Any) -> None:
    settings = Settings(
        _env_file=None,
        dashboard_password=SecretStr("supersecretpassword123"),
        audit_dir=tmp_path / "audit",
        digest_dir=tmp_path / "digests",
        logs_dir=tmp_path / "logs",
        topics_dir=tmp_path / "topics",
        sources_csv=tmp_path / "sources.csv",
    )
    mock_st = MockSt(entered_password="wrong", authenticated=False)  # noqa: S106
    monkeypatch.setattr(dashboard, "st", mock_st)
    for _ in range(6):
        dashboard.main(settings=settings)
    assert any("Too many attempts" in msg for msg in mock_st.errors)


def test_dashboard_no_session_state_fallback(tmp_path: Path, monkeypatch: Any) -> None:
    settings = Settings(
        _env_file=None,
        dashboard_password=SecretStr("supersecretpassword123"),
        audit_dir=tmp_path / "audit",
        digest_dir=tmp_path / "digests",
        logs_dir=tmp_path / "logs",
        topics_dir=tmp_path / "topics",
        sources_csv=tmp_path / "sources.csv",
    )
    # Incorrect password without session state
    mock_st1 = MockSt(entered_password="wrong", has_session_state=False)  # noqa: S106
    monkeypatch.setattr(dashboard, "st", mock_st1)
    dashboard.main(settings=settings)
    assert any("Enter the dashboard password" in w for w in mock_st1.warnings)

    # Correct password without session state
    mock_st2 = MockSt(entered_password="supersecretpassword123", has_session_state=False)  # noqa: S106
    monkeypatch.setattr(dashboard, "st", mock_st2)
    dashboard.main(settings=settings)
    assert "Media quotes — operational dashboard" in mock_st2.titles


def test_dashboard_helpers(tmp_path: Path) -> None:
    assert dashboard.guidance() != ""
    assert dashboard.schema_summary(tmp_path / "nonexistent.sql") == []

    sql_file = tmp_path / "schema.sql"
    sql_file.write_text(
        "CREATE TABLE articles (\n"
        "    article_id UUID PRIMARY KEY,\n"
        "    title TEXT NOT NULL\n"
        ");\n"
        "CREATE TABLE quotes (\n"
        "    quote_id UUID PRIMARY KEY\n"
        ");\n",
        encoding="utf-8",
    )
    summary = dashboard.schema_summary(sql_file)
    assert "articles" in summary
    assert "quotes" in summary

    # Empty recent logs
    assert dashboard.recent_logs(tmp_path / "no_logs") == []
    # Empty digests
    assert dashboard.latest_digests(tmp_path / "no_digests") == []
    # Empty sources
    assert dashboard.registered_sources(tmp_path / "no_sources.csv") == []

    # Populated digests in topic subdirs
    digests_dir = tmp_path / "digests_sub"
    (digests_dir / "ukraine-war").mkdir(parents=True, exist_ok=True)
    (digests_dir / "ukraine-war" / "2026-11-20.md").write_text("# Digest", encoding="utf-8")
    digests = dashboard.latest_digests(digests_dir, topic_id="ukraine-war")
    assert len(digests) == 1
    assert "2026-11-20" in digests[0][0]


def test_topic_without_artifacts_never_shows_other_topics(tmp_path: Path) -> None:
    digests_dir = tmp_path / "digests"
    digests_dir.mkdir()
    (digests_dir / "2026-11-20.md").write_text("# root", encoding="utf-8")
    assert dashboard.latest_digests(digests_dir, topic_id="ai-policy") == []

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "audit-2026-11-20.json").write_text("{}", encoding="utf-8")
    assert dashboard._latest_audit(audit_dir, topic_id="ai-policy") == {}


def test_dashboard_modes_and_postgres(tmp_path: Path, monkeypatch: Any) -> None:
    # 1. Test live Postgres + OpenRouter mode description
    cfg_live = Settings(
        _env_file=None,
        dashboard_password=SecretStr("pass123"),
        persist_to_postgres=True,
        llm_provider="openrouter",
        openrouter_api_key=SecretStr("sk-openrouter-key-12345678901234567890"),
        llm_model_snapshot="openai/gpt-4o-mini-2024-07-18",
        audit_dir=tmp_path / "audit",
        digest_dir=tmp_path / "digests",
        logs_dir=tmp_path / "logs",
        topics_dir=tmp_path / "topics",
        sources_csv=tmp_path / "sources.csv",
    )
    mock_st1 = MockSt(authenticated=True)
    monkeypatch.setattr(dashboard, "st", mock_st1)
    monkeypatch.setattr(dashboard, "database_summary", lambda s: {"sources": 10, "quotes": 50})
    dashboard.main(settings=cfg_live)
    assert any("live Postgres + OpenRouter" in badge for badge in mock_st1.badges)

    # 2. Test Postgres persistent mode description with DB error
    cfg_pg = Settings(
        _env_file=None,
        dashboard_password=SecretStr("pass123"),
        persist_to_postgres=True,
        llm_provider="local",
        audit_dir=tmp_path / "audit",
        digest_dir=tmp_path / "digests",
        logs_dir=tmp_path / "logs",
        topics_dir=tmp_path / "topics",
        sources_csv=tmp_path / "sources.csv",
    )
    mock_st2 = MockSt(authenticated=True)
    monkeypatch.setattr(dashboard, "st", mock_st2)
    monkeypatch.setattr(dashboard, "database_summary", lambda s: {"error": "ConnectionRefused"})
    dashboard.main(settings=cfg_pg)
    assert any("Postgres persistent" in badge for badge in mock_st2.badges)
    assert any("ConnectionRefused" in str(w) for w in mock_st2.writes)

    # 3. Test Postgres persistent with empty counts
    mock_st3 = MockSt(authenticated=True)
    monkeypatch.setattr(dashboard, "st", mock_st3)
    monkeypatch.setattr(dashboard, "database_summary", lambda s: {})
    dashboard.main(settings=cfg_pg)
    assert any("no counts were returned" in str(w) for w in mock_st3.writes)


def test_dashboard_st_none(monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setattr(dashboard, "st", None)
    dashboard.main()
    captured = capsys.readouterr()
    assert "Dashboard not launched in blocked mode" in captured.out


def test_session_active_uses_eight_hour_auth_time_window() -> None:
    now = time.time()
    assert dashboard._session_active({"authenticated": True, "auth_time": now - 10}, now)
    assert not dashboard._session_active(
        {"authenticated": True, "auth_time": now - dashboard.AUTH_TTL_SECONDS - 1}, now
    )
    assert not dashboard._session_active({"authenticated": True}, now)
    assert not dashboard._session_active({"authenticated": False, "auth_time": now}, now)


def test_expired_session_logs_out(tmp_path: Path, monkeypatch: Any) -> None:
    settings = Settings(
        _env_file=None,
        dashboard_password=SecretStr("supersecretpassword123"),
        logs_dir=tmp_path / "logs",
        audit_dir=tmp_path / "audit",
    )
    mock_st = MockSt(authenticated=True)
    mock_st.session_state["auth_time"] = time.time() - dashboard.AUTH_TTL_SECONDS - 1
    monkeypatch.setattr(dashboard, "st", mock_st)

    dashboard.main(settings=settings)
    assert mock_st.session_state["authenticated"] is False
    assert "auth_time" not in mock_st.session_state
    assert any("Login" in title for title in mock_st.titles)
    assert "Media quotes — operational dashboard" not in mock_st.titles


def test_fresh_session_respects_server_lockout(tmp_path: Path, monkeypatch: Any) -> None:
    settings = Settings(
        _env_file=None,
        dashboard_password=SecretStr("supersecretpassword123"),
        logs_dir=tmp_path / "logs",
        audit_dir=tmp_path / "audit",
    )
    now = time.time()
    dashboard._write_auth_state(
        settings.logs_dir, {"attempts": 5, "locked_until": now + 900, "updated_at": now}
    )
    mock_st = MockSt(entered_password="supersecretpassword123", authenticated=False)  # noqa: S106
    monkeypatch.setattr(dashboard, "st", mock_st)

    dashboard.main(settings=settings)
    assert any("Too many attempts" in msg for msg in mock_st.errors)
    assert not any("Login" in title for title in mock_st.titles)
    assert mock_st.session_state["authenticated"] is False


def test_auth_five_failures_lock_and_success_resets(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    now = time.time()
    for _ in range(4):
        state = dashboard._record_auth_failure(logs_dir, now)
    assert state["attempts"] == 4
    assert not dashboard._is_locked(state, now)
    state = dashboard._record_auth_failure(logs_dir, now)
    assert state["attempts"] == 5
    assert state["locked_until"] == now + dashboard.AUTH_LOCK_SECONDS
    assert dashboard._is_locked(state, now)
    assert dashboard._is_locked(dashboard._read_auth_state(logs_dir), now)

    reset = dashboard._reset_auth_state(logs_dir, now + 1)
    assert reset == {"attempts": 0, "locked_until": 0.0, "updated_at": now + 1}
    assert not dashboard._is_locked(dashboard._read_auth_state(logs_dir), now)


def test_auth_state_file_has_only_safe_keys(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    path = dashboard._auth_state_path(logs_dir)
    path.write_text(
        json.dumps(
            {
                "attempts": 1,
                "locked_until": 0,
                "updated_at": 0,
                "password": "supersecretpassword123",
                "ip": "127.0.0.1",
                "token": "secret-token",
            }
        ),
        encoding="utf-8",
    )
    assert set(dashboard._read_auth_state(logs_dir)) == set(dashboard.AUTH_STATE_KEYS)

    dashboard._record_auth_failure(logs_dir, time.time())
    raw = path.read_text(encoding="utf-8")
    assert set(json.loads(raw)) == set(dashboard.AUTH_STATE_KEYS)
    for secret in ("supersecretpassword123", "127.0.0.1", "secret-token"):
        assert secret not in raw


def test_auth_lock_contention_fails_open_with_last_read(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    now = time.time()
    original = {"attempts": 2, "locked_until": 0.0, "updated_at": now}
    dashboard._write_auth_state(logs_dir, original)
    path = dashboard._auth_state_path(logs_dir)
    lock = path.with_name(path.name + ".lock")
    lock.write_text("held", encoding="utf-8")
    try:
        result = dashboard._record_auth_failure(logs_dir, now)
        assert result == original
        assert json.loads(path.read_text(encoding="utf-8")) == original
    finally:
        lock.unlink()


def test_auth_stale_lock_is_reclaimed(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    now = time.time()
    path = dashboard._auth_state_path(logs_dir)
    lock = path.with_name(path.name + ".lock")
    lock.parent.mkdir(parents=True)
    lock.write_text("stale", encoding="utf-8")
    stale = now - dashboard.AUTH_LOCK_STALE_SECONDS - 1
    os.utime(lock, (stale, stale))

    result = dashboard._record_auth_failure(logs_dir, now)

    assert result["attempts"] == 1
    assert dashboard._read_auth_state(logs_dir)["attempts"] == 1
    assert not lock.exists()


def test_graph_projection_summary_closes_driver_on_success_and_failure(
    monkeypatch: Any,
) -> None:
    class FakeSession:
        def __enter__(self) -> FakeSession:
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

        def run(self, query: str) -> Any:
            key = "node_count" if "count(n)" in query else "rel_count"
            return SimpleNamespace(data=lambda: [{key: 7}])

    class FakeDriver:
        def __init__(self) -> None:
            self.closed = 0

        def close(self) -> None:
            self.closed += 1

        def session(self, database: str | None = None) -> FakeSession:
            return FakeSession()

    settings = Settings(_env_file=None, sync_to_neo4j=True, neo4j_password=SecretStr("secret"))
    good = FakeDriver()
    monkeypatch.setattr("media_quotes.graph.get_driver", lambda _: good)
    monkeypatch.setattr(
        "media_quotes.graph.graph_session", lambda driver, database: driver.session(database)
    )
    monkeypatch.setattr("media_quotes.graph.check_graph_connectivity", lambda _: True)
    monkeypatch.setattr("media_quotes.graph.fetch_sync_metadata", lambda _: {})

    result = dashboard.graph_projection_summary(settings)
    assert result["status"] == "connected"
    assert result["node_count"] == 7
    assert good.closed == 1

    bad = FakeDriver()
    monkeypatch.setattr("media_quotes.graph.get_driver", lambda _: bad)

    def fail_metadata(_session: object) -> object:
        raise RuntimeError("metadata failed")

    monkeypatch.setattr("media_quotes.graph.fetch_sync_metadata", fail_metadata)
    failed = dashboard.graph_projection_summary(settings)
    assert failed["status"] == "error"
    assert bad.closed == 1
