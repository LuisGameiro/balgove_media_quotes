"""Pure, deterministic article relevance rules.

No network, database or LLM is consulted here. Rules are versioned JSON documents;
an article is published only when a subject anchor and war-context support agree.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ..text_utils import normalise_text

TopicStatus = Literal["accepted", "uncertain", "rejected"]

_FIELD_ORDER: tuple[str, ...] = ("tag", "title", "body", "url")
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "tag": ("tag", "tags", "source_tags", "publisher_tag"),
    "title": ("title", "headline"),
    "body": ("article_text", "text", "body", "extracted_text"),
    "url": ("canonical_url", "url", "link"),
}
_DEFAULT_FIELD_WEIGHTS: dict[str, float] = {"tag": 1.5, "title": 1.0, "body": 0.7, "url": 0.3}


@dataclass(frozen=True, slots=True)
class TopicDecision:
    """Explainable outcome of one rule evaluation."""

    status: TopicStatus
    score: float
    matched_terms: tuple[str, ...]
    rule_id: str
    rule_version: str
    reasons: tuple[str, ...]


def load_topic_rules(path: Path) -> dict[str, dict[str, Any]]:
    """Load versioned rules keyed by ``rule_id``; reject malformed rule files."""
    payload: object = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, Mapping) and "rule_id" in payload:
        # A single versioned rule file is also a valid rule source.
        rule_id = payload["rule_id"]
        if not isinstance(rule_id, str) or not rule_id:
            raise ValueError("topic rule rule_id must be a non-empty string")
        return {rule_id: dict(payload)}
    if isinstance(payload, Mapping) and "rules" in payload:
        payload = payload["rules"]
    if isinstance(payload, Mapping):
        items: Sequence[tuple[object, object]] = list(payload.items())
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        items = []
        for rule in payload:
            rule_id = rule.get("rule_id") if isinstance(rule, Mapping) else None
            items.append((rule_id, rule))
    else:
        raise ValueError("topic rules must be a JSON object or a list of rules")

    rules: dict[str, dict[str, Any]] = {}
    for key, value in items:
        if not isinstance(value, Mapping):
            raise ValueError("each topic rule must be a JSON object")
        rule = dict(value)
        rule_id = str(rule.get("rule_id", key))
        if not rule_id or not isinstance(rule.get("rule_id", rule_id), str):
            raise ValueError("each topic rule needs a string rule_id")
        if rule_id != key:
            raise ValueError(f"topic rule key {key!r} does not match rule_id {rule_id!r}")
        rules[rule_id] = rule
    return rules


def _term_strings(value: object) -> tuple[str, ...]:
    """Flatten locale-keyed maps and nested term lists into a stable term tuple."""
    terms: list[str] = []
    if isinstance(value, str):
        if value.strip():
            terms.append(value.strip())
    elif isinstance(value, Mapping):
        for name in ("terms", "patterns", "variants", "words"):
            if name in value:
                terms.extend(_term_strings(value[name]))
        if not terms:
            for item in value.values():
                terms.extend(_term_strings(item))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            terms.extend(_term_strings(item))
    return tuple(dict.fromkeys(terms))


def _folded(value: str) -> str:
    """NFC + casefold, plus a diacritic-folded variant for accented term forms."""
    value = normalise_text(value).casefold()
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _fields(article: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    raw_fields: dict[str, str] = {}
    for field, aliases in _FIELD_ALIASES.items():
        parts: list[str] = []
        for alias in aliases:
            value = article.get(alias)
            if value is None:
                continue
            if field == "tag" and isinstance(value, (list, tuple, set, frozenset)):
                parts.extend(str(item) for item in value)
            else:
                parts.append(str(value))
        raw_fields[field] = " ".join(parts)
    normalised = {field: normalise_text(text).casefold() for field, text in raw_fields.items()}
    folded = {field: _folded(text) for field, text in raw_fields.items()}
    return normalised, folded


def _matches(term: str, text: str, folded_text: str) -> bool:
    """Word-boundary phrase match, with and without diacritics."""
    phrase = normalise_text(term).casefold()
    folded_phrase = _folded(term)
    for candidate in dict.fromkeys((phrase, folded_phrase)):
        if not candidate:
            continue
        pattern = (
            r"(?<!\w)" + r"\s+".join(re.escape(part) for part in candidate.split()) + r"(?!\w)"
        )
        if re.search(pattern, text) or re.search(pattern, folded_text):
            return True
    return False


def _float(value: object, default: float) -> float:
    if isinstance(value, (bool, int, float, str)):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _weight(weights: Mapping[str, Any], names: Sequence[str], default: float) -> float:
    for name in names:
        if name in weights:
            return _float(weights[name], default)
    return default


def _accept_band(value: object) -> tuple[float, float]:
    """Return the uncertain-band lower bound and the acceptance threshold."""
    threshold = 3.0
    lower = 1.0
    if isinstance(value, Mapping):
        threshold = _float(
            value.get("accept_threshold", value.get("upper", value.get("high", threshold))),
            threshold,
        )
        lower = _float(value.get("lower", value.get("low", value.get("min", lower))), lower)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) == 2:
        lower = _float(value[0], lower)
        threshold = _float(value[1], threshold)
    elif value is not None:
        lower = _float(value, lower)
    return lower, threshold


def evaluate_topic(rule: Mapping[str, Any], article: Mapping[str, Any]) -> TopicDecision:
    """Evaluate one article against one versioned rule; never guess on weak evidence."""
    anchors = _term_strings(rule.get("anchors", ()))
    supporting = _term_strings(rule.get("supporting_terms", rule.get("support", ())))
    exclusions = _term_strings(rule.get("exclusions", ()))
    hard_exclusions = _term_strings(rule.get("hard_exclusions", ()))
    weights = rule.get("weights")
    weight_map: Mapping[str, Any] = weights if isinstance(weights, Mapping) else {}
    anchor_weight = _weight(weight_map, ("anchor", "anchors"), 2.0)
    support_weight = _weight(
        weight_map, ("support", "supporting", "supporting_term", "supporting_terms"), 1.0
    )
    field_weights = {
        field: _weight(weight_map, (field, *_FIELD_ALIASES[field]), default)
        for field, default in _DEFAULT_FIELD_WEIGHTS.items()
    }
    uncertain_low, band_threshold = _accept_band(rule.get("uncertain_band"))
    accept_threshold = (
        _float(rule["accept_threshold"], 3.0) if "accept_threshold" in rule else band_threshold
    )
    uncertain_low = min(uncertain_low, accept_threshold)

    text_fields, folded_fields = _fields(article)

    # Hard exclusions veto before scoring, even when anchors are present.
    hard_matches: list[str] = []
    for field in _FIELD_ORDER:
        for term in hard_exclusions:
            if (
                _matches(term, text_fields[field], folded_fields[field])
                and term not in hard_matches
            ):
                hard_matches.append(term)
    if hard_matches:
        return TopicDecision(
            status="rejected",
            score=0.0,
            matched_terms=tuple(hard_matches),
            rule_id=str(rule.get("rule_id", "")),
            rule_version=str(rule.get("version", rule.get("rule_version", ""))),
            reasons=("hard_exclusion",),
        )

    score = 0.0
    anchor_hits = 0
    support_hits = 0
    exclusion_hits = 0
    matched: list[str] = []
    reasons: list[str] = []

    def record(term: str, field: str, category: str) -> None:
        if term not in matched:
            matched.append(term)
        reasons.append(f"{category}:{field}:{term}")

    for field in _FIELD_ORDER:
        text = text_fields[field]
        folded_text = folded_fields[field]
        multiplier = field_weights[field]
        for term in anchors:
            if _matches(term, text, folded_text):
                anchor_hits += 1
                score += anchor_weight * multiplier
                record(term, field, "anchor")
        for term in supporting:
            if _matches(term, text, folded_text):
                support_hits += 1
                score += support_weight * multiplier
                record(term, field, "support")
        for term in exclusions:
            if _matches(term, text, folded_text):
                exclusion_hits += 1
                record(term, field, "exclusion")

    score = round(score, 4)
    if exclusion_hits and (anchor_hits == 0 or score < accept_threshold):
        status: TopicStatus = "rejected"
        reasons.append("exclusion_rejects")
    elif exclusion_hits:
        status = "uncertain"
        reasons.append("exclusion_downgrades")
    elif anchor_hits == 0:
        status = "rejected"
        reasons.append("no_anchor")
    elif support_hits == 0:
        status = "uncertain"
        reasons.append("no_supporting_term")
    elif score < accept_threshold:
        if score >= uncertain_low:
            status = "uncertain"
            reasons.append("score_in_uncertain_band")
        else:
            status = "rejected"
            reasons.append("score_below_uncertain_band")
    else:
        status = "accepted"
        reasons.append("score_at_or_above_threshold")

    return TopicDecision(
        status=status,
        score=score,
        matched_terms=tuple(matched),
        rule_id=str(rule.get("rule_id", "")),
        rule_version=str(rule.get("version", rule.get("rule_version", ""))),
        reasons=tuple(reasons),
    )
