# Cyrillic stopwords below are intentional linguistic data, not lookalike Latin characters.
# ruff: noqa: RUF001
"""Deterministic, offline language detection for quote text.

Script alone is not enough for Ukrainian versus Russian, so distinctive letters
and stopwords are scored together. Ambiguity returns ``und`` rather than a guess.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_CYRILLIC = re.compile(r"[\u0400-\u052f]")
_LATIN = re.compile(r"[A-Za-z\u00c0-\u024f]")
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)

_UK_LETTERS = frozenset("іїєґ")
_RU_LETTERS = frozenset("ыъэё")

_UK_WORDS = frozenset(
    {
        "україна",
        "україни",
        "україні",
        "украіна",
        "українська",
        "український",
        "українські",
        "українці",
        "росія",
        "росії",
        "російська",
        "російський",
        "київ",
        "києва",
        "війна",
        "війни",
        "війні",
        "привіт",
        "світ",
        "сьогодні",
        "добре",
        "ні",
        "так",
        "що",
        "і",
        "ї",
        "є",
        "але",
        "коли",
        "де",
        "був",
        "була",
        "було",
        "не",
        "на",
        "за",
        "від",
        "до",
        "під",
        "над",
        "об",
    }
)
_RU_WORDS = frozenset(
    {
        "украина",
        "украины",
        "украине",
        "украинский",
        "украинская",
        "украинцы",
        "россия",
        "россии",
        "российский",
        "российская",
        "киев",
        "война",
        "войны",
        "войне",
        "привет",
        "мир",
        "сегодня",
        "хорошо",
        "что",
        "и",
        "ё",
        "но",
        "да",
        "нет",
        "когда",
        "где",
        "был",
        "была",
        "было",
        "не",
        "на",
        "за",
        "от",
        "до",
        "под",
        "над",
        "об",
    }
)
_EN_WORDS = frozenset(
    {
        "the",
        "and",
        "of",
        "to",
        "in",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "for",
        "with",
        "from",
        "on",
        "at",
        "by",
        "as",
        "but",
        "or",
        "not",
        "we",
        "our",
        "they",
        "their",
        "i",
        "you",
        "he",
        "she",
        "his",
        "her",
        "said",
        "says",
        "say",
        "war",
        "ukraine",
        "russia",
        "peace",
        "talks",
        "support",
        "will",
        "has",
        "have",
        "had",
        "do",
        "does",
        "did",
        "can",
        "could",
        "would",
        "should",
        "know",
        "think",
        "now",
        "more",
        "than",
        "after",
        "before",
    }
)


@dataclass(frozen=True, slots=True)
class LanguageDecision:
    """Detected language (ISO 639-1 or ``und``) with bounded confidence."""

    language: str
    confidence: float
    evidence: tuple[str, ...]


def _tokens(text: str) -> set[str]:
    normalised = unicodedata.normalize("NFC", text).casefold()
    return {token for token in _WORD.findall(normalised) if token}


def _script_counts(text: str) -> tuple[int, int]:
    cyrillic = sum(1 for char in text if _CYRILLIC.fullmatch(char))
    latin = sum(1 for char in text if _LATIN.fullmatch(char))
    return cyrillic, latin


def _cyrillic_decision(text: str, tokens: set[str], cyrillic: int) -> LanguageDecision:
    uk_score = 0.0
    ru_score = 0.0
    evidence: list[str] = ["script:cyrillic"]
    letters = set(text.casefold())

    for letter in sorted(_UK_LETTERS & letters):
        uk_score += 2.0
        evidence.append(f"letter:uk:{letter}")
    for letter in sorted(_RU_LETTERS & letters):
        ru_score += 2.0
        evidence.append(f"letter:ru:{letter}")
    for word in sorted(tokens & _UK_WORDS):
        uk_score += 1.5
        evidence.append(f"word:uk:{word}")
    for word in sorted(tokens & _RU_WORDS):
        ru_score += 1.5
        evidence.append(f"word:ru:{word}")

    if uk_score == 0.0 and ru_score == 0.0:
        evidence.append("no_distinctive_markers")
        return LanguageDecision("und", 0.0, tuple(evidence))
    margin = abs(uk_score - ru_score)
    if margin < 0.5:
        evidence.append("ambiguous_slavic_markers")
        return LanguageDecision("und", 0.25, tuple(evidence))

    language = "uk" if uk_score > ru_score else "ru"
    total = uk_score + ru_score + max(cyrillic, 1)
    confidence = round(min(0.99, 0.55 + margin / total), 4)
    return LanguageDecision(language, confidence, tuple(evidence))


def _latin_decision(text: str, tokens: set[str]) -> LanguageDecision:
    matches = sorted(tokens & _EN_WORDS)
    accents = sorted({char for char in text if char.isalpha() and ord(char) > 127})
    evidence: list[str] = ["script:latin"]
    evidence.extend(f"word:en:{word}" for word in matches)
    evidence.extend(f"diacritic:{char}" for char in accents)

    if not matches:
        evidence.append("no_english_markers")
        return LanguageDecision("und", 0.0, tuple(evidence))
    # Accented Latin without English markers is left unknown rather than called English.
    if accents and len(matches) < 2:
        evidence.append("accented_ambiguous")
        return LanguageDecision("und", 0.25, tuple(evidence))
    confidence = round(min(0.99, 0.5 + 0.12 * len(matches)), 4)
    return LanguageDecision("en", confidence, tuple(evidence))


def detect_language(text: str) -> LanguageDecision:
    """Detect en/uk/ru from quote text; return ``und`` when evidence is insufficient."""
    normalised = unicodedata.normalize("NFC", text)
    if not normalised.strip():
        return LanguageDecision("und", 0.0, ("empty_text",))

    cyrillic, latin = _script_counts(normalised)
    tokens = _tokens(normalised)
    if cyrillic == 0:
        if latin == 0:
            return LanguageDecision("und", 0.0, ("no_letters",))
        return _latin_decision(normalised, tokens)
    if latin > cyrillic * 2:
        return _latin_decision(normalised, tokens)
    return _cyrillic_decision(normalised, tokens, cyrillic)
