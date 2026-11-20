"""F1/CHK-19 — licence-limit guard withheld records, never rewrites text."""

from __future__ import annotations

from media_quotes.config import Settings
from media_quotes.rights_guard import apply_rights_limits


def test_limits_are_strict() -> None:
    import pytest

    with pytest.raises(ValueError):
        apply_rights_limits([], max_quote_words=0)


def test_real_records_pass_default_limit() -> None:
    from media_quotes.cli.common import _parse_report_date
    from media_quotes.cli.run import _collect

    settings = Settings(_env_file=None)
    records, *_rest = _collect(settings, run_id="r", report_date=_parse_report_date(None, settings))
    decision = apply_rights_limits(records, max_quote_words=settings.max_quote_words)
    assert len(decision.kept) + len(decision.rejected) == len(records)


def test_overlimit_and_missing_attribution_are_rejected() -> None:
    from media_quotes.cli.common import _parse_report_date
    from media_quotes.cli.run import _collect

    settings = Settings(_env_file=None)
    records, *_rest = _collect(settings, run_id="r", report_date=_parse_report_date(None, settings))
    assert records
    strict = apply_rights_limits(records, max_quote_words=1)
    assert strict.kept == []
    assert all("over_word_limit" in item["reason"] for item in strict.rejected)

    no_author = [
        record.model_copy(update={"article_author": "", "source_name": ""}) for record in records
    ]
    missing = apply_rights_limits(no_author, max_quote_words=30, attribution_required=True)
    assert all("missing_attribution" in item["reason"] for item in missing.rejected)
