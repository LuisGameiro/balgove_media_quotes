"""Multi-format quote detection, speaker attribution and topic-aware section inference."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from ..text_utils import normalise_text

if TYPE_CHECKING:
    from ..topics import TopicPack

__all__ = [
    "UKRAINE_WAR_KEYWORDS",
    "extract_quotes",
    "infer_section",
    "is_topic_relevant",
    "is_ukraine_war_relevant",
]


def extract_quotes(
    text: str,
    *,
    require_ukraine_topic: bool = False,
    topic_pack: TopicPack | None = None,
    require_topic: bool = False,
) -> list[dict[str, Any]]:
    """Public alias for _extract_quotes."""
    return _extract_quotes(
        text,
        require_ukraine_topic=require_ukraine_topic,
        topic_pack=topic_pack,
        require_topic=require_topic,
    )


# Match curly quotes (“...”), straight quotes ("..."), or guillemets («...» / „...“)
_QUOTE_RE = re.compile(
    r"(?:[\u201c\"]([^\u201d\"]{20,4000})[\u201d\"]"
    r"|[\u00ab]([^\u00bb]{20,4000})[\u00bb]"
    r"|[\u201e]([^\u201c\u201d]{20,4000})[\u201c\u201d])"
)

_ATTRIBUTION_VERBS = (
    r"said|says|added|told|stated|noted|warned|announced|stressed|explained|declared"
    r"|according to|reported|заявив|заявила|сказав|сказала|зазначив|зазначила|повідомив|наголосив"
)
_NAME_TOKEN = (
    r"[A-Z\u0410-\u042f\u0404\u0406\u0407\u0490]"  # noqa: S105 - name regex
    r"[A-Za-z\u0410-\u044f\u0404\u0454\u0406\u0456\u0407\u0457\u0490\u0491'-]+"
)
_SPEAKER_NAME = rf"({_NAME_TOKEN}(?:\s+{_NAME_TOKEN}){{0,3}})"

# 1) Verb-first: "said Volodymyr Zelenskyy", "according to Denys Shmyhal", "— Spokesperson".
_ATTRIBUTION_VERB_FIRST_RE = re.compile(rf"(?:{_ATTRIBUTION_VERBS}|—|-)\s+{_SPEAKER_NAME}")

# 2) Speaker-first: "Volodymyr Zelenskyy said", "President Biden told", "Dmytro Kuleba stated"
_ATTRIBUTION_SPEAKER_FIRST_RE = re.compile(rf"{_SPEAKER_NAME}\s+(?:{_ATTRIBUTION_VERBS})\b")

# Fallback section keywords for default ukraine-war topic when no TopicPack is passed.
_SECTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "war_intensity": (
        "offensive",
        "shelling",
        "fighting",
        "front line",
        "casualt",
        "strike",
        "drone",
        "missile",
    ),
    "international_support": (
        "support",
        "air defence",
        "partners",
        "aid",
        "assistance",
        "coalition",
        "package",
    ),
    "peace_initiatives": (
        "ceasefire",
        "negotiation",
        "talks",
        "peace",
        "halt in strikes",
        "diploma",
        "summit",
    ),
    "sanctions": ("sanction", "restriction", "enforcement", "dual-use", "embargo", "asset freeze"),
    "resources": (
        "ammunition",
        "production",
        "personnel",
        "supplies",
        "budget",
        "mobilisation",
        "defense industry",
    ),
}

UKRAINE_WAR_KEYWORDS: tuple[str, ...] = (
    # English terms
    "ukraine",
    "ukrainian",
    "kyiv",
    "kiev",
    "russia",
    "russian",
    "moscow",
    "putin",
    "zelenskiy",
    "zelensky",
    "kremlin",
    "donbas",
    "donetsk",
    "luhansk",
    "kharkiv",
    "kherson",
    "zaporizhzhia",
    "crimea",
    "kursk",
    "black sea",
    "odesa",
    "front line",
    "frontline",
    "air defence",
    "interceptor",
    "drone",
    "missile",
    "shahed",
    "shelling",
    "ceasefire",
    "mobilisation",
    "artillery",
    "ammunition",
    # Ukrainian terms
    "україна",
    "українськ",
    "київ",
    "росія",
    "російськ",
    "москва",
    "путін",
    "зеленськ",
    "кремль",
    "донбас",
    "донеччин",
    "луганськ",
    "харків",
    "херсон",
    "запоріжж",
    "крим",
    "курськ",
    "чорне море",
    "одеса",
    "фронт",
    "ппо",
    "перехоплювач",
    "шахед",
    "обстріл",
    "ракета",
    "дрон",
    "перемир'я",
    "мобілізація",
    "артилерія",
    "боєприпас",
    # Russian terms
    "украина",
    "украинск",
    "киев",
    "россия",
    "российск",
    "москва",
    "путин",
    "зеленск",
    "кремль",
    "донбасс",
    "перемирие",
    "боеприпас",
)


def is_ukraine_war_relevant(
    text: str,
    *,
    title: str = "",
    url: str = "",
) -> bool:
    """Check if an article, headline, URL, or quote belongs to the Russia-Ukraine war domain."""
    combined = f"{url} {title} {text}".lower()
    return any(keyword in combined for keyword in UKRAINE_WAR_KEYWORDS)


def is_topic_relevant(
    text: str,
    *,
    topic_pack: TopicPack | None = None,
    title: str = "",
    url: str = "",
    tags: Sequence[str] = (),
) -> bool:
    """Check topic relevance using ``topic_pack`` when supplied, else default Ukraine-war rules."""
    if topic_pack is not None:
        return topic_pack.is_relevant(text, title=title, url=url, tags=tags)
    return is_ukraine_war_relevant(text, title=title, url=url)


def infer_section(text: str, *, topic_pack: TopicPack | None = None) -> str:
    """Deterministic fallback section assignment for unlabelled quotes."""
    if topic_pack is not None:
        return topic_pack.infer_section(text)
    lowered = text.lower()
    for section_id, keywords in _SECTION_KEYWORDS.items():
        for keyword in keywords:
            if len(keyword) <= 4 and " " not in keyword:
                if re.search(rf"\b{re.escape(keyword)}\b", lowered):
                    return section_id
            else:
                if keyword in lowered:
                    return section_id
    return "other"


def _match_quote_group(match: re.Match[str]) -> tuple[str, int, int]:
    """Return (inner_quote_text, start_offset, end_offset) for whichever quote delimiter matched."""
    for group_idx in (1, 2, 3):
        val = match.group(group_idx)
        if val is not None:
            return val, match.start(group_idx), match.end(group_idx)
    return "", match.start(), match.end()


def _find_attribution(prefix: str, suffix: str) -> tuple[str | None, str]:
    """Search suffix then prefix for verb-first or speaker-first attribution."""
    match = _ATTRIBUTION_VERB_FIRST_RE.search(suffix) or _ATTRIBUTION_SPEAKER_FIRST_RE.search(
        suffix
    )
    if match:
        return match.group(1), match.group(0)
    matches: list[re.Match[str]] = list(_ATTRIBUTION_VERB_FIRST_RE.finditer(prefix)) + list(
        _ATTRIBUTION_SPEAKER_FIRST_RE.finditer(prefix)
    )
    if matches:
        closest = max(matches, key=lambda m: m.end())
        return closest.group(1), closest.group(0)
    return None, ""


def _extract_quotes(
    text: str,
    *,
    require_ukraine_topic: bool = False,
    topic_pack: TopicPack | None = None,
    require_topic: bool = False,
) -> list[dict[str, Any]]:
    """Find attributed, quoted spans in article text with exact offsets into that text."""
    enforce_topic = require_topic or require_ukraine_topic
    default_section = topic_pack.default_section if topic_pack is not None else "other"
    found: list[dict[str, Any]] = []
    for index, match in enumerate(_QUOTE_RE.finditer(text), start=1):
        quote_text, q_start, q_end = _match_quote_group(match)
        if not normalise_text(quote_text):
            continue
        section_id = infer_section(quote_text, topic_pack=topic_pack)
        if (
            enforce_topic
            and section_id == default_section
            and not is_topic_relevant(quote_text, topic_pack=topic_pack)
        ):
            continue
        suffix = text[match.end() : match.end() + 160]
        prefix = text[max(0, match.start() - 160) : match.start()]
        speaker, attr_span = _find_attribution(prefix, suffix)
        found.append(
            {
                "quote_text": quote_text,
                "char_start": q_start,
                "char_end": q_end,
                "dom_path": f"text://span[{index}]",
                "speaker": speaker,
                "attribution_span": attr_span,
                "section_id": section_id,
            }
        )
    return found
