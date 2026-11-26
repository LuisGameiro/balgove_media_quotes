"""Shared word-count helper used by the config gate and the rights guard."""

from __future__ import annotations

from media_quotes.text_utils import word_count


def test_word_count_splits_on_unicode_whitespace() -> None:
    assert word_count("one two three") == 3
    assert word_count("  one\n\ttwo\r\nthree  ") == 3
    assert word_count("") == 0
    assert word_count("résumé test") == 2


def test_word_count_agrees_with_config_gate() -> None:
    from media_quotes.config import Settings

    settings = Settings(_env_file=None)
    text = " ".join(["word"] * settings.max_quote_words)
    assert word_count(text) == settings.max_quote_words
    assert settings.quote_within_limits(text)
    assert not settings.quote_within_limits(text + " extra")
