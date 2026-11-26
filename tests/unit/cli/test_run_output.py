"""Operator-facing ``mq run`` output is friendly but keeps every fact."""

from __future__ import annotations

from types import SimpleNamespace

from media_quotes.cli.run import _format_run_summary


def _gap(**overrides: object) -> dict[str, object]:
    gap: dict[str, object] = {
        "source_id": "Reuters",
        "rights_status": "licensed",
        "gap_reason": "no articles",
        "detail": "page returned nothing",
        "mandatory": True,
    }
    gap.update(overrides)
    return gap


def _result(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "dry_run": False,
        "run_id": "run-42",
        "topic_id": "ukraine-war",
        "records": [object(), object()],
        "rejected": 3,
        "gaps": (_gap(),),
        "status": "ok",
        "provenance": "fixture",
        "persistence": {"articles": 5, "stored_quotes": 2},
        "digest_path": "/out/digest.md",
        "store_path": "/out/quotes.jsonl",
        "logs_path": "/out/logs.jsonl",
        "audit_md_path": "/out/audit.md",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_normal_run_keeps_all_facts() -> None:
    lines = _format_run_summary(_result(), verbose=True)
    text = "\n".join(lines)

    assert any("run-42" in line and "ukraine-war" in line for line in lines)
    assert "2 verified quotes from fixture pages" in text
    assert "Rejected: 3" in text
    assert "Coverage gaps: 1" in text
    assert "5 article(s)" in text and "2 quote(s) stored" in text
    assert any("Digest:" in line and "/out/digest.md" in line for line in lines)
    assert any("Store:" in line and "/out/quotes.jsonl" in line for line in lines)
    assert any("Logs:" in line and "/out/logs.jsonl" in line for line in lines)
    assert any("Audit:" in line and "/out/audit.md" in line for line in lines)
    assert "Where to find it:" in lines


def test_gap_line_stays_searchable_with_details() -> None:
    lines = _format_run_summary(_result(), verbose=False)
    gap_lines = [line for line in lines if line.startswith("COVERAGE GAP:")]
    assert len(gap_lines) == 1
    line = gap_lines[0]
    for fact in ("Reuters", "licensed", "no articles", "fixture replay logged, no live contact"):
        assert fact in line
    assert any("Audit:" not in line for line in lines)


def test_mandatory_gaps_are_deduplicated() -> None:
    gaps = (_gap(), _gap(), _gap(source_id="AP", gap_reason="blocked"))
    lines = _format_run_summary(_result(gaps=gaps), verbose=False)
    gap_lines = [line for line in lines if line.startswith("COVERAGE GAP:")]
    assert len(gap_lines) == 2
    assert any("Reuters" in line for line in gap_lines)
    assert any("AP" in line for line in gap_lines)


def test_live_provenance_is_reported() -> None:
    lines = _format_run_summary(_result(provenance="live_feed"), verbose=False)
    assert any("verified quotes from live_feed" in line for line in lines)
    assert any("no synthetic replay; live collection only" in line for line in lines)


def test_dry_run_note_and_gap_details() -> None:
    lines = _format_run_summary(_result(dry_run=True), verbose=False)
    text = "\n".join(lines)

    assert any("Dry run" in line and "run-42" in line for line in lines)
    assert any("Topic: ukraine-war" in line for line in lines)
    assert "2 quote(s) that would be saved" in text
    assert "3 candidate(s) rejected" in text
    assert "1 coverage gap(s)" in text
    assert "No files were written (practice only)." in lines
    assert any(
        line.startswith("  gap:") and "Reuters" in line and "page returned nothing" in line
        for line in lines
    )


def test_failed_run_keeps_failure_message() -> None:
    lines = _format_run_summary(_result(status="failed"), verbose=False)
    assert "Run failed: mandatory live sources yielded no quotes; no digest published." in lines
