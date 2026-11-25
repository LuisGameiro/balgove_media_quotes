"""`mq sources --action add` must not claim a successful offline admission probe."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from media_quotes.cli.ops import _do_sources
from media_quotes.config import Settings


def test_sources_add_reports_probe_as_not_run_offline(tmp_path: Path, capsys) -> None:
    settings = Settings(
        _env_file=None,
        report_date=date(2026, 11, 19),
        sources_dir=tmp_path / "sources",
    )
    assert (
        _do_sources(
            settings,
            tier=None,
            rights_status=None,
            action="add",
            source_id="gridnews",
            source_name="Grid News",
            homepage_url="https://gridnews.example",
            feed_url="https://gridnews.example/rss.xml",
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "not run in offline mode" in out
    assert "Admission probe: robots_txt=OK" not in out
    config = json.loads((tmp_path / "sources" / "gridnews.json").read_text(encoding="utf-8"))
    assert "probe_ready" not in config["notes"]
