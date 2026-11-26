"""F1/CHK-19 — licence-limit guard withheld records, never rewrites text."""

from __future__ import annotations

from datetime import date

from media_quotes.config import Settings
from media_quotes.governance.rights_guard import apply_rights_limits


def test_limits_are_strict() -> None:
    import pytest

    with pytest.raises(ValueError):
        apply_rights_limits([], max_quote_words=0)


def test_real_records_pass_default_limit() -> None:
    from media_quotes.cli.run import _collect

    settings = Settings(_env_file=None)
    fixed_date = date(2026, 11, 19)
    records, *_rest = _collect(settings, run_id="r", report_date=fixed_date)
    decision = apply_rights_limits(records, max_quote_words=settings.max_quote_words)
    assert len(decision.kept) + len(decision.rejected) == len(records)


def test_overlimit_and_missing_attribution_are_rejected() -> None:
    from media_quotes.cli.run import _collect

    settings = Settings(_env_file=None)
    fixed_date = date(2026, 11, 19)
    records, *_rest = _collect(settings, run_id="r", report_date=fixed_date)
    assert records
    strict = apply_rights_limits(records, max_quote_words=1)
    assert strict.kept == []
    assert all("over_word_limit" in item["reason"] for item in strict.rejected)

    no_author = [
        record.model_copy(update={"article_author": "", "source_name": ""}) for record in records
    ]
    missing = apply_rights_limits(no_author, max_quote_words=30, attribution_required=True)
    assert all("missing_attribution" in item["reason"] for item in missing.rejected)


def test_longer_translation_rejects_in_limit_original() -> None:
    from media_quotes.cli.run import _collect

    settings = Settings(_env_file=None)
    records, *_rest = _collect(settings, run_id="r", report_date=date(2026, 11, 19))
    assert records
    record = records[0]
    original_words = len(record.quote_text_original.split())
    assert original_words > 0
    inflated = record.model_copy(
        update={"quote_text_en": " ".join(["translation"] * (original_words + 50))}
    )
    decision = apply_rights_limits([inflated], max_quote_words=original_words)
    assert decision.kept == []
    assert len(decision.rejected) == 1
    assert "over_word_limit:translation:" in decision.rejected[0]["reason"]

    short = record.model_copy(update={"quote_text_en": "brief"})
    decision = apply_rights_limits([short], max_quote_words=original_words)
    assert decision.kept == [short]
    assert decision.rejected == []


def test_rights_guard_uses_shared_word_count_semantics() -> None:
    from media_quotes.cli.run import _collect
    from media_quotes.text_utils import word_count

    settings = Settings(_env_file=None)
    records, *_rest = _collect(settings, run_id="r", report_date=date(2026, 11, 19))
    record = records[0].model_copy(
        update={"quote_text_original": "one\t\ntwo", "quote_text_en": ""}
    )
    decision = apply_rights_limits([record], max_quote_words=word_count("one two"))
    assert decision.kept == [record]
    assert decision.rejected == []


def test_cjk_quote_without_spaces_is_not_char_counted() -> None:
    from media_quotes.cli.run import _collect

    settings = Settings(_env_file=None)
    records, *_rest = _collect(settings, run_id="r", report_date=date(2026, 11, 19))
    assert records
    cjk = "乌克兰防空系统将在冬季加速交付并扩大覆盖范围"
    updated = [
        record.model_copy(update={"quote_text_original": cjk, "quote_text_en": cjk})
        for record in records
    ]
    decision = apply_rights_limits(updated, max_quote_words=1)
    assert decision.kept
    assert all("over_word_limit" not in item["reason"] for item in decision.rejected)
