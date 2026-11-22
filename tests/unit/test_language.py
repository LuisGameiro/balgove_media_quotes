# Cyrillic quotes are intentional test data; RUF001 misflags lookalike letters.
# ruff: noqa: RUF001
"""Offline language classification of quote text."""

from __future__ import annotations

from media_quotes.language import LanguageDecision, detect_language


def test_detects_english_with_stopword_evidence() -> None:
    decision = detect_language("The war in Ukraine must end now")

    assert isinstance(decision, LanguageDecision)
    assert decision.language == "en"
    assert 0.0 <= decision.confidence <= 1.0
    assert any(evidence.startswith("word:en:") for evidence in decision.evidence)


def test_distinguishes_ukrainian_from_russian() -> None:
    ukrainian = detect_language("Україна має право на мир і безпеку")
    russian = detect_language("Россия ведёт войну против Украины")

    assert ukrainian.language == "uk"
    assert russian.language == "ru"
    assert any("letter:uk:" in item for item in ukrainian.evidence)
    assert any("letter:ru:" in item for item in russian.evidence)


def test_unknown_text_is_und_not_a_guess() -> None:
    assert detect_language("!!! 123 ???").language == "und"
    assert detect_language("").language == "und"
    ambiguous = detect_language("не на за")
    assert ambiguous.confidence <= 0.5
    assert ambiguous.evidence


def test_detection_is_deterministic_and_returns_bounded_confidence() -> None:
    quote = "Мирні переговори в Україні тривають"
    first = detect_language(quote)
    second = detect_language(quote)

    assert first == second
    assert isinstance(first.evidence, tuple)
    assert 0.0 <= first.confidence <= 1.0
