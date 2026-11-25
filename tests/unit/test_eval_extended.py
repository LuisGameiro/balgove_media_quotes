"""Extended gold-set eval: non-relevant articles, topic TP/FP and thresholds."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from media_quotes.cli import eval as eval_cli
from media_quotes.config import Settings
from media_quotes.evaluation import AcceptanceThresholds, evaluate_metrics
from media_quotes.gold_set import GoldArticle, GoldSet
from media_quotes.pipeline import TopicAssessment
from media_quotes.registry import SourceRegistryRow, load_sources_csv

ROOT = Path(__file__).resolve().parents[2]


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        sources_csv=ROOT / "data/sources.csv",
        sections_csv=ROOT / "data/sections.csv",
        fixtures_dir=ROOT / "data/fixtures",
        gold_set_dir=ROOT / "data/gold_set",
        audit_dir=tmp_path / "audit",
        export_dir=tmp_path / "export",
        persist_to_postgres=False,
    )


@dataclass(frozen=True, slots=True)
class FakeRoute:
    source_id: str = "reuters"
    topic_rule_id: str = "rule-v1"
    approved: bool = True
    translation_rights_confirmed: bool = True


class FakeAdapter:
    mode = "fixture_replay"

    def __init__(self, row: SourceRegistryRow) -> None:
        self._row = row

    def fetch(self, ref: str) -> bytes:
        return b"synthetic"

    def parse_metadata(self, raw: bytes) -> dict[str, Any]:
        return {
            "article_text": "Synthetic fixture article about war and diplomacy.",
            "canonical_url": "https://example.invalid/eval-topic",
            "title": "Fixture headline",
        }

    def policy_flags(self) -> dict[str, Any]:
        return dict(self._row.policy_flags)


def test_eval_counts_topic_false_positives(tmp_path: Path, monkeypatch: Any) -> None:
    settings = _settings(tmp_path)
    row = next(
        item for item in load_sources_csv(ROOT / "data/sources.csv") if item.source_id == "reuters"
    )
    gold = GoldSet(
        version="synthetic-extended",
        articles=[
            GoldArticle(
                article_ref="fixture://relevant",
                source_id="reuters",
                publication_date="2026-11-20",
                relevant=True,
            ),
            GoldArticle(
                article_ref="fixture://false-positive",
                source_id="reuters",
                publication_date="2026-11-20",
                relevant=False,
            ),
            GoldArticle(
                article_ref="fixture://true-negative",
                source_id="reuters",
                publication_date="2026-11-20",
                relevant=False,
            ),
        ],
    )
    monkeypatch.setattr(eval_cli, "load_gold_set", lambda path: gold)
    monkeypatch.setattr(eval_cli, "build_adapter", lambda *a, **k: FakeAdapter(row))
    monkeypatch.setattr(eval_cli, "_topic_gate", lambda settings: object())
    monkeypatch.setattr(eval_cli, "_select_topic_route", lambda gate, source_id: FakeRoute())

    def fake_assessment(settings: Any, gate: Any, *, ref: str, **kwargs: Any) -> Any:
        status = (
            "accepted" if ref in {"fixture://relevant", "fixture://false-positive"} else "rejected"
        )
        outcome = SimpleNamespace(
            assessment=TopicAssessment(
                status=status,
                score=0.8,
                matched_terms=("war",),
                rule_id="rule-v1",
                rule_version="v1",
                reasons=("fixture",),
            )
        )
        return outcome

    monkeypatch.setattr(eval_cli, "_gate_article", fake_assessment)

    metrics = eval_cli.evaluate_gold_set_report(settings, gold_set="synthetic-v1")

    assert metrics["relevant_articles"] == 1
    assert metrics["non_relevant_articles"] == 2
    assert metrics["topic_true_positive"] == 1
    assert metrics["topic_false_negative"] == 0
    assert metrics["topic_false_positive"] == 1
    assert metrics["topic_true_negative"] == 1
    assert metrics["topic_false_positive_rate"] == 0.5
    assert metrics["topic_recall"] == 1.0
    assert len(metrics["topic_decisions"]) == 3
    assert [item["expected_relevant"] for item in metrics["topic_decisions"]] == [
        True,
        False,
        False,
    ]
    assert all(key in metrics for key in AcceptanceThresholds().as_dict())


def test_eval_direct_rule_decisions_are_not_duplicated(tmp_path: Path, monkeypatch: Any) -> None:
    settings = _settings(tmp_path)
    row = next(
        item for item in load_sources_csv(ROOT / "data/sources.csv") if item.source_id == "reuters"
    )
    gold = GoldSet(
        version="synthetic-direct-rule",
        articles=[
            GoldArticle(
                article_ref="fixture://direct-one",
                source_id="reuters",
                publication_date="2026-11-20",
                relevant=False,
            ),
            GoldArticle(
                article_ref="fixture://direct-two",
                source_id="reuters",
                publication_date="2026-11-20",
                relevant=False,
            ),
        ],
    )
    monkeypatch.setattr(eval_cli, "load_gold_set", lambda path: gold)
    monkeypatch.setattr(eval_cli, "build_adapter", lambda *a, **k: FakeAdapter(row))
    # No route -> the direct-rule branch runs (the path that used to double-append).
    monkeypatch.setattr(eval_cli, "_select_topic_route", lambda gate, source_id: None)

    metrics = eval_cli.evaluate_gold_set_report(settings, gold_set="synthetic-v1")
    refs = [item["article_ref"] for item in metrics["topic_decisions"]]
    assert len(refs) == 2
    assert len(set(refs)) == 2


def test_split_eval_keeps_the_canonical_report_untouched(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    eval_cli.evaluate_gold_set_report(settings, gold_set="synthetic-v1", split="train")
    assert (settings.audit_dir / "eval-synthetic-v1-train.json").is_file()
    assert not (settings.audit_dir / "eval-synthetic-v1.json").exists()


def test_full_eval_requires_obsolete_labels(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    metrics = eval_cli.evaluate_gold_set_report(settings, gold_set="synthetic-v1", full=True)
    assert metrics["obsolete_precision"] == 0.0
    assert "obsolete_precision" in metrics["failed_checks"]
    assert metrics["consecutive_failures"] == 0
    assert metrics["obsoletion_allowed"] is True


def test_eval_counts_false_positive_when_gate_accepts_off_topic(
    tmp_path: Path, monkeypatch: Any
) -> None:
    metrics = {
        "article_recall": 1.0,
        "quote_recall": 1.0,
        "span_exact_match": 1.0,
        "metadata_accuracy": 1.0,
        "section_accuracy": 1.0,
        "false_quote_rate": 0.0,
        "obsolete_precision": 1.0,
        "judge_kappa": 1.0,
        "topic_recall": 1.0,
        "topic_precision": 0.5,
        "topic_false_positive_rate": 0.5,
        "language_accuracy": 1.0,
        "category_coverage": 0.5,
    }
    result = evaluate_metrics(metrics, AcceptanceThresholds())
    assert result["failed_checks"] == [
        "category_coverage",
        "topic_false_positive_rate",
        "topic_precision",
    ]
    assert result["pass"] is False
    assert metrics["topic_false_positive_rate"] == 0.5  # input untouched
