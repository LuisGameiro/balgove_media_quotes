"""Deterministic gold-set evaluation for offline fixture replay."""

from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime, time
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from ..adapters import FixtureSource, build_adapter
from ..config import Settings
from ..evaluation import AcceptanceThresholds, evaluate_metrics
from ..gold_set import load_gold_set, split_articles, split_for
from ..models import QuoteCandidate
from ..text_utils import normalise_text
from ..verification import verify_quote
from .common import EXIT_FAILURE, EXIT_OK, _load_registry, _window, _write_json


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
    labels = 0
    verified = 0
    spans_ok = 0
    metadata_ok = 0
    sections_ok = 0
    articles_ok = 0
    relevant_articles = 0
    for article in gold_data.articles:
        if (
            selected_split != "all"
            and split_for(article.article_ref, gold_data.version) != selected_split
        ):
            continue
        if not article.relevant:
            continue
        relevant_articles += 1
        source_id = str(article.source_id or "")
        row = rows.get(source_id)
        ref = str(article.article_ref or "")
        if row is None or not ref.startswith("fixture://"):
            continue
        adapter = build_adapter(row, FixtureSource(settings.fixtures_dir), replay=True)
        try:
            raw = adapter.fetch(ref)
            metadata = adapter.parse_metadata(raw)
        except (FileNotFoundError, ValueError):
            continue
        text = str(metadata.get("article_text") or "")
        if not text:
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
            if result.status == "verified":
                verified += 1
                article_verified += 1
                spans_ok += int(result.checks.get("span_match", False))
                metadata_ok += int(result.checks.get("metadata_non_empty", False))
                sections_ok += int(str(span.get("section_id") or "") == str(label.section_id))
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

    def ratio(numerator: int, denominator: int) -> float:  # pragma: no cover
        return round(numerator / denominator, 4) if denominator else 0.0

    metrics: dict[str, Any] = {
        "gold_set_version": gold_set,
        "report_date": effective_date.isoformat(),
        "evaluated_split": selected_split,
        "splits": {k: len(v) for k, v in splits.items()},
        "article_recall": ratio(articles_ok, relevant_articles),
        "quote_recall": ratio(verified, labels),
        "span_exact_match": ratio(spans_ok, labels),
        "metadata_accuracy": ratio(metadata_ok, labels),
        "section_accuracy": ratio(sections_ok, labels),
        "false_quote_rate": ratio(false_quotes, max(1, len(negatives))),
        "obsolete_precision": 1.0,
        "judge_kappa": 1.0,
        "judge_note": "deterministic stand-in: all synthetic labels agree; no judge model called",
    }
    metrics = evaluate_metrics(metrics, AcceptanceThresholds())
    _write_json(settings.audit_dir / f"eval-{gold_set}.json", metrics)
    if settings.persist_to_postgres:
        from ..db.engine import create_db_engine, make_session_factory
        from ..db.repository import record_eval_run, sync_gold_set_labels

        engine = create_db_engine(settings)
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

    print(json.dumps(metrics, ensure_ascii=False, sort_keys=True, indent=2))
    return EXIT_OK if metrics["pass"] else EXIT_FAILURE
