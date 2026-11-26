"""F2 — restore integrity, retention readiness, review queue seed/list/decide, and split eval."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from media_quotes.cli.eval import evaluate_gold_set_report
from media_quotes.cli.maintenance import _do_review
from media_quotes.cli.run import _load_review_flags
from media_quotes.config import Settings
from media_quotes.governance.restore_drill import retention_readiness, verify_export_integrity
from media_quotes.risk_controls.retention import RetentionPolicy


def test_verify_export_integrity() -> None:
    from media_quotes.cli.common import _parse_report_date
    from media_quotes.cli.run import _collect

    settings = Settings(_env_file=None)
    records, *_rest = _collect(settings, run_id="r", report_date=_parse_report_date(None, settings))
    ok, reasons = verify_export_integrity(records, list(records))
    assert ok and reasons == []
    ok, reasons = verify_export_integrity(records, records[:-1])
    assert not ok and any("missing" in reason for reason in reasons)
    changed = [item.model_copy(update={"content_hash": "0" * 64}) for item in records]
    ok, reasons = verify_export_integrity(records, changed)
    assert not ok and any("hash_changed" in reason for reason in reasons)


@pytest.mark.parametrize(
    "field,value",
    [
        ("quote_text_en", "Changed translation."),
        ("section_id", "sanctions"),
        ("is_obsolete", True),
        ("speaker", "Another speaker"),
        ("source_id", "bbc"),
    ],
)
def test_restore_integrity_detects_metadata_changes(field: str, value: object) -> None:
    from media_quotes.cli.run import _collect

    settings = Settings(_env_file=None)
    records, *_rest = _collect(settings, run_id="r", report_date=date(2026, 11, 19))
    if not records:
        pytest.skip("no fixture records in this environment")
    changed = [item.model_copy(update={field: value}) for item in records]
    ok, reasons = verify_export_integrity(list(records), changed)
    assert not ok and any("hash_changed" in reason for reason in reasons)


def test_restore_drill_reads_fixture_isolated_exports(tmp_path: Path, capsys) -> None:
    from media_quotes.cli.maintenance import _do_restore_drill
    from media_quotes.cli.run import _collect
    from media_quotes.processing.export import local_store_path, write_jsonl

    settings = Settings(_env_file=None, export_dir=tmp_path / "export")
    records = _collect(settings, run_id="r", report_date=date(2026, 11, 19))[0]
    current = local_store_path(settings.export_dir, date(2026, 11, 20))
    reference = local_store_path(settings.export_dir, date(2026, 11, 19))
    assert write_jsonl(records, current) == len(records)
    assert write_jsonl(records, reference) == len(records)

    assert _do_restore_drill(settings, report_date=date(2026, 11, 20), reference=reference) == 0
    assert "fixtures" in capsys.readouterr().out


def test_restore_drill_keeps_requested_dated_export(tmp_path: Path, capsys) -> None:
    from media_quotes.cli.maintenance import _do_restore_drill
    from media_quotes.processing.export import local_store_path, write_jsonl

    settings = Settings(_env_file=None, export_dir=tmp_path / "export")
    for day in (18, 19, 20):
        path = local_store_path(settings.export_dir, date(2026, 11, day))
        write_jsonl([], path)
    assert _do_restore_drill(settings, report_date=date(2026, 11, 20)) == 0
    output = capsys.readouterr().out
    assert "quotes-2026-11-20.jsonl" in output
    assert "quotes-2026-11-19.jsonl" in output
    assert "quotes-2026-11-18.jsonl" not in output


def test_retention_readiness() -> None:
    report = retention_readiness(
        RetentionPolicy(),
        date(2026, 11, 20),
        [date(2026, 10, 1), date(2026, 11, 1)],
        longest_txn_seconds=10,
    )
    assert report["longest_txn_ok"] is True
    assert report["audit_rows_dropped"] is False
    assert report["blob_cutoff"] == "2026-05-24"
    assert any(name.endswith("2026_10") for name in report["partitions_to_detach"])
    assert any(name.endswith("2026_11") for name in report["partitions_hot"])


def test_review_seed_list_and_decide_round_trip(tmp_path: Path, capsys) -> None:
    settings = Settings(_env_file=None, audit_dir=tmp_path / "audit")

    # 1. Seed a sample pending flag
    assert _do_review(settings, action="seed", quote_id="Q-20261119-0001") == 0
    assert "review flag seeded: Q-20261119-0001 -> pending" in capsys.readouterr().out

    # Calling seed again is idempotent
    assert _do_review(settings, action="seed", quote_id="Q-20261119-0001") == 0
    assert "review flag already exists" in capsys.readouterr().out

    # 2. _load_review_flags parses the JSON ISO timestamps without dropping the flag
    loaded = _load_review_flags(settings)
    assert len(loaded) == 1
    assert loaded[0].evidence.old_quote_id == "Q-20261119-0001"
    assert loaded[0].state == "pending"

    # 3. List pending flags via CLI handler
    assert _do_review(settings, action="list") == 0
    listed = json.loads(capsys.readouterr().out)
    assert len(listed) == 1
    assert listed[0]["evidence"]["old_quote_id"] == "Q-20261119-0001"

    # 4. Decide the flag
    assert (
        _do_review(
            settings,
            action="decide",
            quote_id="Q-20261119-0001",
            verdict="confirmed",
            reason_code="superseded",
        )
        == 0
    )
    assert "review recorded: Q-20261119-0001 -> confirmed" in capsys.readouterr().out

    # 5. Now pending list is empty
    assert _do_review(settings, action="list") == 0
    assert json.loads(capsys.readouterr().out) == []


def test_evaluate_gold_set_report_with_split_filter(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        audit_dir=tmp_path / "audit",
        persist_to_postgres=False,
    )
    for split_name in ("all", "train", "dev", "test"):
        metrics = evaluate_gold_set_report(
            settings,
            gold_set="synthetic-v1",
            report_date=date(2026, 11, 19),
            split=split_name,
        )
        assert metrics["evaluated_split"] == split_name
        assert metrics["pass"] is True
        assert metrics["false_quote_rate"] == 0.0
