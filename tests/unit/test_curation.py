"""D3 — section allow-list, cross-outlet dedup, provisional-hold badges."""

from __future__ import annotations

from datetime import UTC, datetime

from media_quotes.config import Settings
from media_quotes.curation import allowed_sections, dedupe_quotes, group_by_section, review_badge
from media_quotes.digest import render_digest
from media_quotes.llm import LocalStandInProvider
from media_quotes.risk_controls.models import ObsolescenceEvidence, ObsolescenceFlag


def test_allow_list_and_grouping() -> None:
    from media_quotes.cli.common import _parse_report_date
    from media_quotes.cli.run import _collect

    settings = Settings(_env_file=None)
    records, *_rest = _collect(settings, run_id="r", report_date=_parse_report_date(None, settings))
    grouped = group_by_section(records)
    assert set(grouped) == set(allowed_sections())
    assert sum(len(values) for values in grouped.values()) == len(records)


def test_dedupe_prefers_first_hand_and_earliest() -> None:
    from media_quotes.cli.common import _parse_report_date
    from media_quotes.cli.run import _collect

    settings = Settings(_env_file=None)
    records, *_rest = _collect(settings, run_id="r", report_date=_parse_report_date(None, settings))
    deduped = dedupe_quotes(records)
    assert len(deduped) <= len(records)
    assert len({item.quote_id for item in deduped}) == len(deduped)


def test_review_badge_lifecycle() -> None:
    evidence = ObsolescenceEvidence(
        old_quote_id="Q-20261101-0001",
        old_content_hash="a" * 64,
        newer_article_id="art",
        newer_content_hash="b" * 64,
        newer_is_first_hand=True,
        newer_rights_cleared=True,
        superseding_span="new fact",
        decision_id="d1",
        rationale="superseded",
    )
    now = datetime.now(UTC)
    pending = ObsolescenceFlag(evidence=evidence, confidence=0.9)
    assert review_badge(pending, now=now) == "[Under Review]"
    confirmed = pending.model_copy(
        update={"state": "confirmed", "reviewer": "BI", "reviewed_at": now}
    )
    assert review_badge(confirmed, now=now) == ""


def test_digest_renders_status_line_without_changing_quote(tmp_path: object) -> None:
    from media_quotes.cli.common import _parse_report_date
    from media_quotes.cli.run import _collect

    settings = Settings(_env_file=None)
    records, *_rest = _collect(settings, run_id="r", report_date=_parse_report_date(None, settings))
    assert records
    quote_id = records[0].quote_id
    markdown = render_digest(
        records,
        report_date=settings.report_date,
        window_start=settings.report_date,
        window_end=settings.report_date,
        sections_csv=settings.sections_csv,
        provider=LocalStandInProvider(),
        review_badges={quote_id: "[Under Review]"},
    )
    assert records[0].quote_text_original in markdown
    assert "Status: [Under Review] (provisional hold)" in markdown
