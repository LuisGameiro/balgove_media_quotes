"""F2 — restore integrity and retention readiness (offline)."""

from __future__ import annotations

from datetime import date

from media_quotes.config import Settings
from media_quotes.restore_drill import retention_readiness, verify_export_integrity
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
