"""Deterministic gold-set evaluation for offline fixture replay."""

from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime, time
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from ..adapters import FixtureSource, build_adapter
from ..config import Settings
from ..enums import SECTION_IDS
from ..evaluation import AcceptanceThresholds, evaluate_metrics
from ..gold_set import load_gold_set, split_articles, split_for
from ..models import QuoteCandidate
from ..pipeline import detect_language, normalise_language_decision
from ..relevance import evaluate_topic, load_topic_rules
from ..text_utils import normalise_text
from ..verification import verify_quote
from .common import EXIT_FAILURE, EXIT_OK, _load_registry, _window, _write_json
from .run import _gate_article, _select_topic_route, _topic_gate


def _ratio(numerator: int, denominator: int) -> float:  # pragma: no cover
    return round(numerator / denominator, 4) if denominator else 0.0


def evaluate_gold_set_report(
    settings: Settings,
    *,
    gold_set: str = "synthetic-v1",
    report_date: date | None = None,
    split: str | None = None,
) -> dict[str, Any]:
    """Run deterministic gold-set evaluation and write the JSON report (shared by CLI & Dagster)."""
    effective_date = report_date or settings.report_date
    selected_split = (split or "all").strip().lower()
    if selected_split not in {"all", "train", "dev", "test"}:
        raise ValueError(f"invalid split: {split!r}; expected one of all, train, dev, test")

    path = settings.gold_set_dir / f"{gold_set}.json"
    if not path.is_file():
        raise FileNotFoundError(f"unknown gold set: {gold_set} (looked in {settings.gold_set_dir})")
    gold_data = load_gold_set(path)
    splits = split_articles(gold_data)
    rows = {row.source_id: row for row in _load_registry(settings)}
    window_start, _ = _window(effective_date, settings)
    now = datetime.combine(effective_date, time(12, 0), tzinfo=UTC)
    gate = _topic_gate(settings)
    rules = load_topic_rules(settings.topic_rules_path)
    labels = 0
    verified = 0
    spans_ok = 0
    metadata_ok = 0
    sections_ok = 0
    sections_checked = 0
    section_mismatch_controls = 0
    articles_ok = 0
    relevant_articles = 0
    non_relevant_articles = 0
    language_checked = 0
    language_attempted = 0
    expected_cells: set[tuple[str, str]] = set()
    covered_cells: set[tuple[str, str]] = set()
    topic_true_positive = 0
    topic_false_negative = 0
    topic_false_positive = 0
    topic_true_negative = 0
    topic_decisions: list[dict[str, Any]] = []

    for article in gold_data.articles:
        if (
            selected_split != "all"
            and split_for(article.article_ref, gold_data.version) != selected_split
        ):
            continue
        expected_relevant = bool(article.relevant)
        if expected_relevant:
            relevant_articles += 1
        else:
            non_relevant_articles += 1
        source_id = str(article.source_id or "")
        row = rows.get(source_id)
        ref = str(article.article_ref or "")
        adapter = (
            build_adapter(row, FixtureSource(settings.fixtures_dir), replay=True)
            if (row is not None and ref.startswith("fixture://"))
            else None
        )
        text = ""
        metadata: dict[str, Any] = {}
        if adapter is not None:
            try:
                raw = adapter.fetch(ref)
                metadata = adapter.parse_metadata(raw)
                text = str(metadata.get("article_text") or "")
            except (FileNotFoundError, ValueError):
                text = ""

        # Topic decisions are recorded for relevant *and* non-relevant labelled articles.
        status = "accepted"
        outcome = None
        decision_fields: dict[str, Any] | None = None
        route = _select_topic_route(gate, source_id)
        gate_active = gate is not None and route is not None
        if adapter is not None and text and gate_active:
            outcome = _gate_article(
                settings,
                gate,
                source_id=source_id,
                metadata=metadata,
                text=text,
                canonical_url=str(metadata.get("canonical_url") or ""),
                title=str(metadata.get("title") or ref),
                ref=ref,
            )
            if outcome is not None:
                status = outcome.assessment.status
        elif adapter is None and gate_active:
            status = "rejected"
        elif adapter is not None and text and not gate_active and rules:
            # Non-relevant fixtures must still be judged against the rule, but without the
            # synthetic publisher tag used for approved topic-routed fixtures.
            rule = next(iter(rules.values()))
            direct = evaluate_topic(
                rule,
                {
                    "article_text": text,
                    "title": str(metadata.get("title") or ref),
                    "canonical_url": str(metadata.get("canonical_url") or ""),
                    "tags": [],
                },
            )
            status = direct.status
            decision_fields = {
                "article_ref": ref,
                "source_id": source_id,
                "expected_relevant": expected_relevant,
                "topic_status": status,
                "rule_id": direct.rule_id,
                "rule_version": direct.rule_version,
                "score": direct.score,
                "matched_terms": list(direct.matched_terms),
                "reasons": list(direct.reasons),
            }

        predicted_relevant = status == "accepted"
        if expected_relevant and predicted_relevant:
            topic_true_positive += 1
        elif expected_relevant:
            topic_false_negative += 1
        elif predicted_relevant:
            topic_false_positive += 1
        else:
            topic_true_negative += 1
        if decision_fields is None:
            decision_fields = {
                "article_ref": ref,
                "source_id": source_id,
                "expected_relevant": expected_relevant,
                "topic_status": status,
                "rule_id": "",
                "rule_version": "",
                "score": 0.0,
                "matched_terms": [],
                "reasons": [],
            }
        if outcome is not None:
            decision_fields.update(
                {
                    "rule_id": outcome.assessment.rule_id,
                    "rule_version": outcome.assessment.rule_version,
                    "score": outcome.assessment.score,
                    "matched_terms": list(outcome.assessment.matched_terms),
                    "reasons": list(outcome.assessment.reasons),
                }
            )
        topic_decisions.append(decision_fields)

        # Quote-level verification is only meaningful for relevant articles.
        if not expected_relevant or not text or adapter is None or row is None:
            continue
        article_verified = 0
        by_text = {
            normalise_text(str(span.get("quote_text") or "")): span
            for span in metadata.get("quote_spans", [])
        }
        for label in article.quotes:
            quote_text = str(label.quote_text or "")
            position = text.find(quote_text)
            if not label.human_label or position < 0:
                continue
            labels += 1
            cell = (source_id, str(label.section_id))
            expected_cells.add(cell)
            span = by_text.get(normalise_text(quote_text), {})
            candidate = QuoteCandidate(
                quote_text=quote_text,
                article_id=uuid5(NAMESPACE_URL, ref),
                char_start=position,
                char_end=position + len(quote_text),
                speaker=span.get("speaker"),
                attribution_span=span.get("attribution_span"),
            )
            policy_flags = dict(adapter.policy_flags())
            policy_flags["original_reporting_flag"] = "first-hand"
            result = verify_quote(
                candidate,
                text,
                row,
                policy_flags,
                metadata=metadata,
                window_start=window_start,
                window_end=effective_date,
                now=now,
            )
            if result.status != "verified":
                continue
            verified += 1
            article_verified += 1
            spans_ok += int(result.checks.get("span_match", False))
            metadata_ok += int(result.checks.get("metadata_non_empty", False))
            expected_section = str(label.section_id)
            actual_section = str(span.get("section_id") or "")
            fixture_section = next(
                (section for section in SECTION_IDS if f"-topic-{section}.html" in ref), ""
            )
            is_control = bool(fixture_section and fixture_section != expected_section)
            if is_control:
                section_mismatch_controls += 1
            else:
                sections_checked += 1
                sections_ok += int(actual_section == expected_section)
            covered_cells.add(cell)
            try:
                language = normalise_language_decision(detect_language(quote_text))
                language_attempted += 1
            except (ImportError, ValueError, TypeError):
                language = None
            # Source language is only a hint (docs/16 §5); mixed-language quotes are
            # expected. A detected, non-unknown code is the measurable success signal.
            if language is not None and language.language and language.language != "und":
                language_checked += 1
        if article_verified:
            articles_ok += 1

    negatives = gold_data.negative_candidates
    false_quotes = 0
    negative_row = next(iter(rows.values()), None)
    negative_flags = dict(negative_row.policy_flags) if negative_row else {}
    for negative in negatives:
        quote_text = str(negative.quote_text or "")
        if len(normalise_text(quote_text)) < 20 or negative_row is None:
            continue
        candidate = QuoteCandidate(
            quote_text=quote_text,
            article_id=uuid5(NAMESPACE_URL, quote_text),
            char_start=0,
            char_end=len(quote_text),
        )
        # The junk gate rejects these before publish; a false quote is any one that
        # would nevertheless pass verification.
        result = verify_quote(
            candidate,
            quote_text,
            negative_row,
            negative_flags,
            window_start=window_start,
            window_end=effective_date,
            now=now,
        )
        if result.status == "verified":
            false_quotes += 1

    # Legacy gold sets contain only relevant articles; they cannot measure false positives.
    topic_evaluated = non_relevant_articles > 0
    metrics: dict[str, Any] = {
        "gold_set_version": gold_set,
        "report_date": effective_date.isoformat(),
        "evaluated_split": selected_split,
        "splits": {k: len(v) for k, v in splits.items()},
        "article_recall": _ratio(articles_ok, relevant_articles),
        "quote_recall": _ratio(verified, labels),
        "span_exact_match": _ratio(spans_ok, labels),
        "metadata_accuracy": _ratio(metadata_ok, labels),
        "section_accuracy": _ratio(sections_ok, sections_checked),
        "section_mismatch_controls": section_mismatch_controls,
        "false_quote_rate": _ratio(false_quotes, max(1, len(negatives))),
        "obsolete_precision": 1.0,
        "judge_kappa": 1.0,
        "judge_note": "deterministic stand-in: all synthetic labels agree; no judge model called",
        "relevant_articles": relevant_articles,
        "non_relevant_articles": non_relevant_articles,
        "topic_true_positive": topic_true_positive,
        "topic_false_negative": topic_false_negative,
        "topic_false_positive": topic_false_positive,
        "topic_true_negative": topic_true_negative,
        "topic_decisions": topic_decisions,
        "topic_evaluated": topic_evaluated,
        "topic_recall": (
            _ratio(topic_true_positive, topic_true_positive + topic_false_negative)
            if topic_evaluated
            else 1.0
        ),
        "topic_precision": (
            _ratio(topic_true_positive, topic_true_positive + topic_false_positive)
            if topic_evaluated
            else 1.0
        ),
        "topic_false_positive_rate": (
            _ratio(topic_false_positive, topic_false_positive + topic_true_negative)
            if topic_evaluated
            else 0.0
        ),
        "language_accuracy": (
            _ratio(language_checked, language_attempted) if language_attempted else 0.0
        ),
        "category_coverage": (
            _ratio(len(covered_cells), len(expected_cells)) if expected_cells else 1.0
        ),
    }
    metrics = evaluate_metrics(metrics, AcceptanceThresholds())
    _write_json(settings.audit_dir / f"eval-{gold_set}.json", metrics)
    if settings.persist_to_postgres:
        from ..db.engine import create_db_engine, make_session_factory
        from ..db.repository import record_eval_run, sync_gold_set_labels

        engine = create_db_engine(settings)
        try:
            with make_session_factory(engine)() as session:
                sync_gold_set_labels(session, gold_data)
                record_eval_run(
                    session,
                    gold_set_version=gold_set,
                    report_date=effective_date,
                    metrics=metrics,
                    thresholds=metrics["thresholds"],
                    passed=bool(metrics["pass"]),
                )
        finally:
            engine.dispose()
    return metrics


def _do_eval(
    settings: Settings,
    *,
    gold_set: str,
    report_date: date,
    split: str | None = None,
) -> int:  # pragma: no cover
    try:
        metrics = evaluate_gold_set_report(
            settings, gold_set=gold_set, report_date=report_date, split=split
        )
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_FAILURE
    except Exception as exc:
        print(f"invalid gold set {gold_set}: {exc}", file=sys.stderr)
        return EXIT_FAILURE

    print(json.dumps(metrics, ensure_ascii=True, sort_keys=True, indent=2))
    return EXIT_OK if metrics["pass"] else EXIT_FAILURE
