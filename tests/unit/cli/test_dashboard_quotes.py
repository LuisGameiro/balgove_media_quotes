"""Read-only quote browser loading, filtering and widget behavior."""

from __future__ import annotations

import json
from pathlib import Path

from streamlit.testing.v1 import AppTest

from media_quotes.dashboard import browse_quotes, filter_quotes, latest_digests


def _row(
    quote_id: str, *, topic_id: str = "ukraine-war", obsolete: bool = False
) -> dict[str, object]:
    return {
        "quote_id": quote_id,
        "provenance": "live_feed",
        "topic_id": topic_id,
        "publication_date": "2026-11-19",
        "source_id": "guardian",
        "source_name": "Guardian",
        "section_id": "war_intensity",
        "is_obsolete": obsolete,
        "quote_text_original": "Talks have been paused.",
        "quote_text_en": "",
        "article_name": "Ukraine briefing",
        "article_author": "Jane Reporter",
        "article_url": f"https://example.invalid/{quote_id}",
    }


def test_browse_quotes_is_topic_scoped_deduplicated_and_bounded(tmp_path: Path) -> None:
    export_dir = tmp_path / "quotes"
    export_dir.mkdir()
    rows = [
        _row("Q-20261119-0001"),
        _row("Q-20261119-0001"),
        _row("Q-20261119-0002", topic_id="ai-policy"),
    ]
    (export_dir / "quotes-2026-11-19.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\nnot-json\n[]\n",
        encoding="utf-8",
    )
    topic_dir = export_dir / "ai-policy"
    topic_dir.mkdir()
    (topic_dir / "quotes-2026-11-19.jsonl").write_text(
        json.dumps(_row("Q-20261119-0003", topic_id="ai-policy")) + "\n",
        encoding="utf-8",
    )

    assert [row["quote_id"] for row in browse_quotes(export_dir, topic_id="ukraine-war")] == [
        "Q-20261119-0001"
    ]
    assert [row["quote_id"] for row in browse_quotes(export_dir, topic_id="ai-policy")] == [
        "Q-20261119-0003"
    ]
    assert browse_quotes(export_dir, topic_id="missing") == []
    assert browse_quotes(export_dir, topic_id="ukraine-war", limit=0) == []


def test_browse_quotes_loads_all_rows_without_a_cap(tmp_path: Path) -> None:
    export_dir = tmp_path / "quotes"
    export_dir.mkdir()
    rows = [_row(f"Q-{index:04d}") for index in range(501)]
    (export_dir / "quotes-2026-11-19.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    assert len(browse_quotes(export_dir)) == 501
    assert len(browse_quotes(export_dir, limit=10)) == 10


def test_dashboard_readers_prefer_live_artifacts_and_fall_back_to_fixtures(tmp_path: Path) -> None:
    export_dir = tmp_path / "quotes"
    fixture_dir = export_dir / "fixtures"
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "quotes-2026-11-19.jsonl").write_text(
        json.dumps(_row("Q-20261119-0001")) + "\n", encoding="utf-8"
    )
    assert browse_quotes(export_dir)[0]["quote_id"] == "Q-20261119-0001"

    (export_dir / "quotes-2026-11-19.jsonl").write_text(
        json.dumps(_row("Q-20261119-0002")) + "\n", encoding="utf-8"
    )
    assert [row["quote_id"] for row in browse_quotes(export_dir)] == ["Q-20261119-0002"]

    digest_dir = tmp_path / "digests"
    (digest_dir / "fixtures").mkdir(parents=True)
    (digest_dir / "fixtures" / "2026-11-19.md").write_text("fixture", encoding="utf-8")
    assert latest_digests(digest_dir) == [("2026-11-19", "fixture")]
    (digest_dir / "2026-11-19.md").write_text("live", encoding="utf-8")
    assert latest_digests(digest_dir) == [("2026-11-19", "live")]


def test_scoped_run_artifacts_are_not_browsed_as_canonical(tmp_path: Path) -> None:
    export_dir = tmp_path / "quotes"
    scoped_export = export_dir / "runs" / "live-t2"
    scoped_export.mkdir(parents=True)
    (scoped_export / "quotes-2026-11-19.jsonl").write_text(
        json.dumps(_row("Q-SCOPED")) + "\n", encoding="utf-8"
    )
    assert browse_quotes(export_dir) == []

    digest_dir = tmp_path / "digests"
    scoped_digest = digest_dir / "runs" / "archive"
    scoped_digest.mkdir(parents=True)
    (scoped_digest / "2026-11-19.md").write_text("scoped", encoding="utf-8")
    assert latest_digests(digest_dir) == []


def test_filter_quotes_matches_text_source_section_and_obsolete_state() -> None:
    rows = [
        _row("Q-20261119-0001"),
        _row("Q-20261119-0002", obsolete=True),
        {**_row("Q-20261119-0003"), "source_id": "reuters", "section_id": "other"},
    ]
    rows[1]["quote_text_original"] = "The earlier position has changed."

    assert len(filter_quotes(rows)) == 2
    assert [row["quote_id"] for row in filter_quotes(rows, text="earlier")] == []
    assert [
        row["quote_id"] for row in filter_quotes(rows, text="earlier", include_obsolete=True)
    ] == ["Q-20261119-0002"]
    assert [row["quote_id"] for row in filter_quotes(rows, source_id="reut")] == ["Q-20261119-0003"]
    assert [row["quote_id"] for row in filter_quotes(rows, section_id="war")] == ["Q-20261119-0001"]


def test_quotes_tab_filters_rendered_table(tmp_path: Path) -> None:
    export_dir = tmp_path / "quotes"
    export_dir.mkdir()
    rows = [_row("Q-20261119-0001"), _row("Q-20261119-0002", obsolete=True)]
    rows[1]["quote_text_original"] = "The earlier position has changed."
    (export_dir / "quotes-2026-11-19.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    script = (
        "from pathlib import Path\n"
        "import media_quotes.dashboard as dashboard\n"
        f"dashboard._render_quotes(Path({str(export_dir)!r}), 'ukraine-war')\n"
    )

    app = AppTest.from_string(script).run(timeout=15)
    assert not app.exception
    assert len(app.dataframe[0].value) == 1

    app.text_input(key="qb_text").set_value("earlier").run(timeout=15)
    assert not app.exception
    assert len(app.dataframe[0].value) == 0

    app.checkbox(key="qb_obsolete").check().run(timeout=15)
    assert not app.exception
    assert len(app.dataframe[0].value) == 1
    assert app.dataframe[0].value.iloc[0]["quote_id"] == "Q-20261119-0002"


def test_browse_quotes_skips_legacy_rows_in_canonical_tree(tmp_path: Path) -> None:
    export_dir = tmp_path / "quotes"
    export_dir.mkdir()
    live = _row("Q-LIVE")
    fixture = {**_row("Q-FIXTURE"), "provenance": "fixture"}
    legacy = {key: value for key, value in _row("Q-LEGACY").items() if key != "provenance"}
    (export_dir / "quotes-2026-11-19.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in (legacy, fixture, live)), encoding="utf-8"
    )
    assert [row["quote_id"] for row in browse_quotes(export_dir)] == ["Q-LIVE"]


def test_browse_quotes_fixture_tree_still_accepts_legacy_rows(tmp_path: Path) -> None:
    export_dir = tmp_path / "quotes"
    fixture_dir = export_dir / "fixtures"
    fixture_dir.mkdir(parents=True)
    legacy = {key: value for key, value in _row("Q-LEGACY").items() if key != "provenance"}
    (fixture_dir / "quotes-2026-11-19.jsonl").write_text(
        json.dumps(legacy) + "\n", encoding="utf-8"
    )
    assert [row["quote_id"] for row in browse_quotes(export_dir)] == ["Q-LEGACY"]


def test_latest_digests_skips_fixture_replay_in_canonical_tree(tmp_path: Path) -> None:
    digest_dir = tmp_path / "digests"
    digest_dir.mkdir()
    (digest_dir / "2026-11-19.md").write_text("# Digest\nOFFLINE FIXTURE REPLAY", encoding="utf-8")
    (digest_dir / "2026-11-20.md").write_text("live digest", encoding="utf-8")
    assert latest_digests(digest_dir) == [("2026-11-20", "live digest")]

    # A fixture-only canonical tree falls back to the fixtures tree.
    (digest_dir / "2026-11-20.md").unlink()
    (digest_dir / "fixtures").mkdir()
    (digest_dir / "fixtures" / "2026-11-19.md").write_text(
        "OFFLINE FIXTURE REPLAY", encoding="utf-8"
    )
    assert latest_digests(digest_dir) == [("2026-11-19", "OFFLINE FIXTURE REPLAY")]
