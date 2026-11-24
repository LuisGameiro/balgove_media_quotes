"""Text normalisation used by the deterministic exact-span verifier.

The verifier compares a proposed quote to the stored article text after Unicode NFC
normalisation, smart-quote/dash folding and whitespace collapsing. The original text is
never altered in storage or in the MD digest.
"""

import re
import unicodedata

_SMART_MAP = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u00a0": " ",
        "\u2009": " ",
        "\u202f": " ",
    }
)

_WS = re.compile(r"\s+")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


def require_safe_id(value: str, name: str) -> str:
    """Accept only simple ids: these values are used as filenames in artifact paths."""
    if not _SAFE_ID.fullmatch(value):
        raise ValueError(f"invalid {name}: {value!r}")
    return value


def normalise_text(value: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFC", value).translate(_SMART_MAP)).strip()


def exact_span_match(article_text: str, quote_text: str, char_start: int, char_end: int) -> bool:
    """Return True only when the offsets delimit an exact occurrence of the quote."""
    if char_start < 0 or char_end <= char_start or char_end > len(article_text):
        return False
    span = article_text[char_start:char_end]
    return normalise_text(span) == normalise_text(quote_text)
