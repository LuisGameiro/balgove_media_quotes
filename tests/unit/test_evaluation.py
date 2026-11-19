"""A3 — shared acceptance-threshold contract for file and Postgres eval runs."""

import pytest
from pydantic import ValidationError

from media_quotes.evaluation import AcceptanceThresholds, evaluate_metrics, obsolete_precision

PERFECT = {
    "article_recall": 1.0,
    "quote_recall": 1.0,
    "span_exact_match": 1.0,
    "metadata_accuracy": 1.0,
    "section_accuracy": 1.0,
    "false_quote_rate": 0.0,
    "obsolete_precision": 1.0,
    "judge_kappa": 1.0,
}


def test_defaults_match_plan() -> None:
    t = AcceptanceThresholds()
    assert t.article_recall == 0.90
    assert t.span_exact_match == 0.95
    assert t.judge_kappa == 0.70
    assert t.false_quote_rate == 0.0


def test_perfect_metrics_pass_and_do_not_mutate_input() -> None:
    original = dict(PERFECT)
    result = evaluate_metrics(PERFECT, AcceptanceThresholds())
    assert original == PERFECT
    assert result["pass"] is True
    assert result["failed_checks"] == []


def test_failing_metrics_list_checks() -> None:
    metrics = dict(PERFECT, article_recall=0.80, false_quote_rate=0.02)
    result = evaluate_metrics(metrics, AcceptanceThresholds())
    assert result["pass"] is False
    assert result["failed_checks"] == ["article_recall", "false_quote_rate"]


def test_obsolete_precision_against_held_out_labels() -> None:
    assert obsolete_precision(["Q1", "Q2"], ["Q1"]) == 0.5
    assert obsolete_precision(["Q1"], ["Q1", "Q2"]) == 1.0
    assert obsolete_precision([], ["Q1"]) == 0.0


def test_thresholds_strict_mode_rejects_coercion() -> None:
    with pytest.raises(ValidationError):
        AcceptanceThresholds(article_recall="0.90")  # type: ignore[arg-type]
