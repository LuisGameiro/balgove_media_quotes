"""Modular topic pack configuration (one JSON file per topic under ``data/topics/``).

A topic pack bundles everything that defines a monitoring subject:
- Canonical sections (order, display names, section-assignment keywords, stand-in summaries)
- Deterministic relevance rules (locales, anchors, supporting terms, exclusions, weights)
- Optional quick-match keywords for fast RSS/headline screening

Adding or switching topics requires only dropping a new ``data/topics/<topic_id>.json``
file and passing ``--topic <topic_id>`` (or setting ``ACTIVE_TOPIC``) — zero Python edits.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import PROJECT_ROOT
from .relevance import TopicDecision, _folded, _matches, evaluate_topic, load_topic_rules
from .text_utils import normalise_text

__all__ = [
    "DEFAULT_TOPICS_DIR",
    "DEFAULT_TOPIC_ID",
    "TopicPack",
    "TopicRelevanceConfig",
    "TopicSection",
    "list_topic_packs",
    "load_all_topic_rules",
    "load_topic_pack",
    "load_topic_pack_file",
    "scaffold_topic_pack",
]

DEFAULT_TOPIC_ID = "ukraine-war"
DEFAULT_TOPICS_DIR = PROJECT_ROOT / "data" / "topics"

_TOPIC_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SECTION_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class TopicSection(BaseModel):
    """One digest/classification section belonging to a topic pack."""

    model_config = ConfigDict(extra="forbid")

    section_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    section_name: str = Field(min_length=1)
    description: str = ""
    keywords: list[str] = Field(default_factory=list)
    standin_summary: str = ""


class TopicRelevanceConfig(BaseModel):
    """Deterministic relevance rule payload compatible with ``relevance.evaluate_topic``."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1)
    version: str = "1.0.0"
    locales: list[str] = Field(default_factory=lambda: ["en"])
    anchors: dict[str, list[str]] = Field(default_factory=dict)
    supporting_terms: dict[str, list[str]] = Field(default_factory=dict)
    exclusions: dict[str, list[str]] = Field(default_factory=dict)
    hard_exclusions: dict[str, list[str]] = Field(default_factory=dict)
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "anchor": 3.0,
            "supporting_term": 1.0,
        }
    )
    accept_threshold: float = 2.0
    uncertain_band: dict[str, float] = Field(default_factory=lambda: {"lower": 0.5, "upper": 1.9})

    @model_validator(mode="after")
    def _validate_anchors(self) -> TopicRelevanceConfig:
        has_anchor = any(term.strip() for terms in self.anchors.values() for term in terms)
        if not has_anchor:
            raise ValueError("topic relevance must declare at least one anchor term")
        return self

    def to_rule_dict(self) -> dict[str, Any]:
        """Return the plain dict format expected by ``relevance.evaluate_topic``."""
        return self.model_dump(mode="json")


class TopicPack(BaseModel):
    """Complete, self-contained definition of one monitored news topic."""

    model_config = ConfigDict(extra="forbid")

    topic_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1)
    description: str = ""
    version: str = "1.0.0"
    default_section: str = Field(default="other", pattern=r"^[a-z][a-z0-9_]*$")
    sections: list[TopicSection] = Field(min_length=1)
    relevance: TopicRelevanceConfig
    quick_keywords: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_sections(self) -> TopicPack:
        seen: set[str] = set()
        for section in self.sections:
            if section.section_id in seen:
                raise ValueError(
                    f"duplicate section_id {section.section_id!r} in topic {self.topic_id!r}"
                )
            seen.add(section.section_id)
        if self.default_section not in seen:
            raise ValueError(
                f"default_section {self.default_section!r} must be listed in sections "
                f"for topic {self.topic_id!r}"
            )
        return self

    def section_pairs(self) -> list[tuple[str, str]]:
        """Ordered ``(section_id, section_name)`` pairs for digest rendering."""
        return [(section.section_id, section.section_name) for section in self.sections]

    def section_ids(self) -> tuple[str, ...]:
        """Ordered section identifiers declared by this topic."""
        return tuple(section.section_id for section in self.sections)

    def standin_map(self) -> dict[str, str]:
        """Mapping of ``section_id -> standin_summary`` for offline local summaries."""
        return {
            section.section_id: section.standin_summary
            for section in self.sections
            if section.standin_summary
        }

    def all_quick_keywords(self) -> tuple[str, ...]:
        """Return deduplicated lowercase screening keywords (explicit + anchors + supporting)."""
        collected: list[str] = []
        for term in self.quick_keywords:
            cleaned = term.strip().lower()
            if cleaned:
                collected.append(cleaned)
        for mapping in (self.relevance.anchors, self.relevance.supporting_terms):
            for terms in mapping.values():
                for term in terms:
                    cleaned = term.strip().lower()
                    if cleaned:
                        collected.append(cleaned)
        return tuple(dict.fromkeys(collected))

    def infer_section(self, text: str) -> str:
        """Deterministic weighted-phrase fallback section assignment for a quote.

        Each matched keyword scores by its word count (longer phrases are more specific);
        the highest score wins and section declaration order breaks ties.
        """
        lowered = normalise_text(text).casefold()
        folded_text = _folded(text)
        best_section = self.default_section
        best_score = 0
        for section in self.sections:
            if section.section_id == self.default_section:
                continue
            score = sum(
                len(keyword.split())
                for keyword in section.keywords
                if keyword and _matches(keyword, lowered, folded_text)
            )
            if score > best_score:
                best_score = score
                best_section = section.section_id
        return best_section

    def evaluate_article(self, article: Mapping[str, Any]) -> TopicDecision:
        """Run the deterministic relevance rule engine against an article payload."""
        return evaluate_topic(self.relevance.to_rule_dict(), article)

    def is_relevant(
        self,
        text: str,
        *,
        title: str = "",
        url: str = "",
        tags: Sequence[str] = (),
    ) -> bool:
        """Check whether an article, headline, URL, or quote matches this topic.

        Exclusions reject off-topic text when no anchor is present; otherwise either an
        accepted ``evaluate_topic`` decision or a quick-keyword hit matches.
        """
        combined = normalise_text(f"{url} {title} {text} {' '.join(tags)}").casefold()
        folded_combined = _folded(combined)
        exclusions = [
            term.strip()
            for mapping in (self.relevance.exclusions, self.relevance.hard_exclusions)
            for terms in mapping.values()
            for term in terms
            if term.strip()
        ]
        anchors = [
            term.strip()
            for terms in self.relevance.anchors.values()
            for term in terms
            if term.strip()
        ]
        has_anchor = any(_matches(term, combined, folded_combined) for term in anchors)
        has_exclusion = any(_matches(term, combined, folded_combined) for term in exclusions)
        if has_exclusion and not has_anchor:
            return False
        if has_anchor and not has_exclusion:
            return True
        if (
            self.quick_keywords
            and not has_exclusion
            and any(
                _matches(term, combined, folded_combined)
                for term in self.quick_keywords
                if term.strip()
            )
        ):
            return True
        decision = self.evaluate_article(
            {"title": title, "article_text": text, "canonical_url": url, "tags": list(tags)}
        )
        return decision.status == "accepted"

    def to_rule_dict(self) -> dict[str, Any]:
        """Expose the underlying relevance rule dict for pipeline/relevance callers."""
        return self.relevance.to_rule_dict()


def _resolve_topics_dir(topics_dir: Path | None) -> Path:
    return Path(topics_dir) if topics_dir is not None else DEFAULT_TOPICS_DIR


def load_topic_pack_file(path: Path) -> TopicPack:
    """Load and validate a single topic pack JSON file."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    pack = TopicPack.model_validate(payload)
    stem = Path(path).stem
    if stem != pack.topic_id and not stem.endswith(".example"):
        raise ValueError(
            f"topic file {Path(path).name!r} stem {stem!r} does not match "
            f"topic_id {pack.topic_id!r}"
        )
    return pack


def load_topic_pack(topic_id: str, topics_dir: Path | None = None) -> TopicPack:
    """Load a topic pack by ``topic_id`` from ``data/topics/<topic_id>.json``."""
    cleaned = topic_id.strip()
    if not _TOPIC_ID_RE.fullmatch(cleaned):
        raise ValueError(f"invalid topic_id {topic_id!r}")
    root = _resolve_topics_dir(topics_dir)
    path = root / f"{cleaned}.json"
    if not path.is_file():
        available = sorted(p.stem for p in root.glob("*.json")) if root.is_dir() else []
        raise FileNotFoundError(
            f"topic pack {cleaned!r} not found at {path}; available={available}"
        )
    return load_topic_pack_file(path)


def list_topic_packs(topics_dir: Path | None = None) -> list[TopicPack]:
    """Discover and load all valid ``*.json`` topic packs in ``topics_dir``."""
    root = _resolve_topics_dir(topics_dir)
    if not root.is_dir():
        return []
    packs: list[TopicPack] = []
    for path in sorted(root.glob("*.json")):
        if path.name.endswith(".schema.json"):
            continue
        packs.append(load_topic_pack_file(path))
    return packs


def load_all_topic_rules(
    topics_dir: Path | None = None,
    *,
    fallback_rules_path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Collect relevance rule dicts from all topic packs (keyed by ``rule_id`` and ``topic_id``).

    Also merges ``fallback_rules_path`` (e.g. ``data/topic_rules.json``) when present so
    legacy rule files continue to work seamlessly.
    """
    rules: dict[str, dict[str, Any]] = {}
    if fallback_rules_path is not None and Path(fallback_rules_path).is_file():
        rules.update(load_topic_rules(Path(fallback_rules_path)))
    for pack in list_topic_packs(topics_dir):
        rule_dict = pack.to_rule_dict()
        rules[pack.relevance.rule_id] = rule_dict
        rules.setdefault(pack.topic_id, rule_dict)
    return rules


def scaffold_topic_pack(
    topic_id: str,
    *,
    name: str = "",
    description: str = "",
    out_dir: Path | None = None,
) -> Path:
    """Create a starter ``<topic_id>.json`` topic pack template ready for editing."""
    cleaned = topic_id.strip().lower()
    if not _TOPIC_ID_RE.fullmatch(cleaned):
        raise ValueError(
            f"invalid topic_id {topic_id!r}; use lowercase letters, digits, '-' or '_'"
        )
    display = name.strip() or cleaned.replace("-", " ").replace("_", " ").title()
    root = _resolve_topics_dir(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{cleaned}.json"
    if target.exists():
        raise FileExistsError(f"topic pack already exists: {target}")
    pack = TopicPack(
        topic_id=cleaned,
        name=display,
        description=description.strip() or f"News quote monitoring pack for {display}.",
        version="1.0.0",
        default_section="other",
        sections=[
            TopicSection(
                section_id="policy",
                section_name="Policy & Regulation",
                description=(
                    f"Official policy announcements and governance debates around {display}."
                ),
                keywords=["policy", "regulation", "law", "minister", "government"],
                standin_summary=(
                    f"The selected quotes cover policy developments related to {display}."
                ),
            ),
            TopicSection(
                section_id="market_impact",
                section_name="Market & Operations",
                description=f"Industry, economic, and operational developments around {display}.",
                keywords=["market", "industry", "investment", "supply", "cost"],
                standin_summary=(
                    f"The selected quotes highlight market and operational impacts of {display}."
                ),
            ),
            TopicSection(
                section_id="other",
                section_name="Other / Cross-cutting",
                description=f"Material quotes relevant to {display} that span multiple sections.",
                keywords=[],
                standin_summary=(
                    f"The selected quotes raise cross-cutting issues related to {display}."
                ),
            ),
        ],
        relevance=TopicRelevanceConfig(
            rule_id=f"{cleaned}-v1",
            version="1.0.0",
            locales=["en"],
            anchors={"en": [display.lower()]},
            supporting_terms={"en": ["policy", "regulation", "report", "statement"]},
            exclusions={"en": ["premier league", "champions league", "box office", "reality show"]},
        ),
        quick_keywords=[display.lower()],
    )
    target.write_text(
        json.dumps(pack.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return target
