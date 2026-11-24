"""Regression tests for quote boundaries and speaker attribution."""

from __future__ import annotations

from media_quotes.adapters.quote_extractor import extract_quotes


def test_quote_regex_does_not_join_paragraphs() -> None:
    text = (
        '"This opening quote has no closing delimiter in this paragraph.\n\n'
        'A later paragraph closes it," said Jane Reporter.'
    )

    assert extract_quotes(text) == []


def test_weekday_is_not_mistaken_for_speaker() -> None:
    text = (
        "Ryabkov stressed that the proposal was unacceptable. "
        '"Even this option is unacceptable for us," he said. '
        "The next statement came on Thursday."
    )

    quotes = extract_quotes(text)

    assert len(quotes) == 1
    assert quotes[0]["speaker"] == "Ryabkov"
