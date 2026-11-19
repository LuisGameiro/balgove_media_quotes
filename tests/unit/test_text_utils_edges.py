"""Extra coverage for text span matching edge cases."""

from media_quotes.text_utils import exact_span_match


def test_exact_span_match_rejects_out_of_bounds_end() -> None:
    text = "The ceasefire holds."
    assert not exact_span_match(text, "The ceasefire holds.", 0, len(text) + 10)
    assert not exact_span_match(text, "ceasefire", 4, -1)
    assert not exact_span_match(text, "ceasefire", 5, 5)
