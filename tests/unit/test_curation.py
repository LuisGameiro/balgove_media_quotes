"""D3 — section allow-list, cross-outlet dedup, provisional-hold badges."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from media_quotes.config import Settings
from media_quotes.curation import (
    allowed_sections,
    dedupe_quotes,
    group_by_section,
    rank_within_section,
    review_badge,
)
from media_quotes.digest import load_sections, render_digest, write_digest
from media_quotes.export import content_hash, quote_record
from media_quotes.llm import LocalStandInProvider
from media_quotes.models import ExportQuoteRecord
from media_quotes.risk_controls.models import ObsolescenceEvidence, ObsolescenceFlag
from media_quotes.topics import TopicPack, TopicRelevanceConfig, TopicSection


def _record(
    *,
    quote_id: str = "Q-20261119-0001",
    text: str = "Official said talks continued.",
    source_id: str = "reuters",
    source_name: str = "Reuters",
    publication_name: str = "",
    article_name: str = "Grid policy announced",
    article_author: str = "",
    speaker: str = "",
    section: str = "other",
    claim_only: bool = False,
    flag: str = "first-hand",
    topic_score: float = 0.0,
    is_obsolete: bool = False,
) -> ExportQuoteRecord:
    record = quote_record(
        quote_id=quote_id,
        quote_text_original=text,
        source_id=source_id,
        source_name=source_name,
        publication_name=publication_name,
        source_tier=1,
        source_country_region="international",
        source_type="independent",
        rights_status="license_held",
        access_mode="public_rss_permitted",
        discovery_method="rss",
        claim_only=claim_only,
        original_reporting_flag=flag,
        source_language="en",
        publication_date="2026-11-19",
        article_name=article_name,
        article_url="https://news.example/story",
        extraction_date="2026-11-19T12:00:00Z",
        section_id=section,
        is_obsolete=is_obsolete,
        content_hash=content_hash([source_id, text]),
        verification={
            "verbatim_match": True,
            "metadata_verified": True,
            "method": "deterministic_offset_v1",
            "verified_at": "2026-11-19T12:00:00Z",
        },
        article_author=article_author,
        speaker=speaker,
    )
    return record.model_copy(update={"topic_score": topic_score})


def _topic_pack() -> TopicPack:
    return TopicPack(
        topic_id="clean-energy",
        name="Clean Energy",
        default_section="other",
        sections=[
            TopicSection(
                section_id="policy",
                section_name="Policy",
                keywords=["policy"],
                standin_summary="Policy quotes.",
            ),
            TopicSection(section_id="other", section_name="Other"),
        ],
        relevance=TopicRelevanceConfig(rule_id="clean-energy-v1", anchors={"en": ["energy"]}),
    )


class _CountingProvider:
    def __init__(self) -> None:
        self.sections: list[str] = []

    def infer(self, *, section_id: str, section_name: str, quote_texts: Sequence[str]) -> str:
        self.sections.append(section_id)
        return "inference"


def test_allow_list_and_grouping() -> None:
    from media_quotes.cli.common import _parse_report_date
    from media_quotes.cli.run import _collect

    settings = Settings(_env_file=None, report_date=date(2026, 11, 19))
    records, *_rest = _collect(settings, run_id="r", report_date=_parse_report_date(None, settings))
    grouped = group_by_section(records)
    assert set(grouped) == set(allowed_sections())
    assert sum(len(values) for values in grouped.values()) == len(records)


def test_dedupe_prefers_first_hand_and_earliest() -> None:
    from media_quotes.cli.common import _parse_report_date
    from media_quotes.cli.run import _collect

    settings = Settings(_env_file=None, report_date=date(2026, 11, 19))
    records, *_rest = _collect(settings, run_id="r", report_date=_parse_report_date(None, settings))
    deduped = dedupe_quotes(records)
    assert len(deduped) <= len(records)
    assert len({item.quote_id for item in deduped}) == len(deduped)


def test_review_badge_lifecycle() -> None:
    evidence = ObsolescenceEvidence(
        old_quote_id="Q-20261101-0001",
        old_content_hash="a" * 64,
        newer_article_id="art",
        newer_content_hash="b" * 64,
        newer_is_first_hand=True,
        newer_rights_cleared=True,
        superseding_span="new fact",
        decision_id="d1",
        rationale="superseded",
    )
    now = datetime.now(UTC)
    pending = ObsolescenceFlag(evidence=evidence, confidence=0.9)
    assert review_badge(pending, now=now) == "[Under Review]"
    confirmed = pending.model_copy(
        update={"state": "confirmed", "reviewer": "BI", "reviewed_at": now}
    )
    assert review_badge(confirmed, now=now) == ""


def test_digest_renders_status_line_without_changing_quote() -> None:
    from media_quotes.cli.common import _parse_report_date
    from media_quotes.cli.run import _collect

    settings = Settings(_env_file=None, report_date=date(2026, 11, 19))
    records, *_rest = _collect(settings, run_id="r", report_date=_parse_report_date(None, settings))
    assert records
    quote_id = records[0].quote_id
    markdown = render_digest(
        records,
        report_date=settings.report_date,
        window_start=settings.report_date,
        window_end=settings.report_date,
        sections_csv=settings.sections_csv,
        provider=LocalStandInProvider(),
        review_badges={quote_id: "[Under Review]"},
    )
    assert records[0].quote_text_original in markdown
    assert "Status: [Under Review] (provisional hold)" in markdown


def test_digest_quote_newlines_cannot_create_headings() -> None:
    from media_quotes.cli.common import _parse_report_date
    from media_quotes.cli.run import _collect

    settings = Settings(_env_file=None, report_date=date(2026, 11, 19))
    records, *_rest = _collect(settings, run_id="r", report_date=_parse_report_date(None, settings))
    record = records[0]
    record.quote_text_original = "Official said talks continued.\n## Fake heading"
    markdown = render_digest(
        [record],
        report_date=settings.report_date,
        window_start=settings.report_date,
        window_end=settings.report_date,
        sections_csv=settings.sections_csv,
        provider=LocalStandInProvider(),
    )
    assert "\n## Fake heading" not in markdown
    assert "Official said talks continued." in markdown


def test_write_digest_and_load_sections(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, report_date=date(2026, 11, 19))
    sections = load_sections(settings.sections_csv)
    assert len(sections) >= 5
    out_file = tmp_path / "test-digest.md"
    written = write_digest(
        [],
        report_date=settings.report_date,
        window_start=settings.report_date,
        window_end=settings.report_date,
        out_path=out_file,
        sections_csv=settings.sections_csv,
    )
    assert written.is_file()
    assert "_No verified quotes in this window._" in written.read_text(encoding="utf-8")


def test_digest_multiline_quote_is_blockquoted() -> None:
    record = _record(text="Official said talks continued.\n## Fake heading\n- fake list")
    markdown = render_digest(
        [record],
        report_date=date(2026, 11, 19),
        window_start=date(2026, 11, 13),
        window_end=date(2026, 11, 19),
        sections=[("other", "Other")],
    )
    assert "> Official said talks continued." in markdown
    assert "> ## Fake heading" in markdown
    assert "> - fake list" in markdown
    assert "\n## Fake heading" not in markdown
    assert "\n- fake list" not in markdown


def test_digest_escapes_metadata_markdown() -> None:
    record = _record(
        source_name="[Reuters](http://evil.example) <script>",
        publication_name="Pub [x](y)",
        article_name="A <b>bold</b> [story](z)",
        article_author="Jane [link](http://evil.example)",
    )
    markdown = render_digest(
        [record],
        report_date=date(2026, 11, 19),
        window_start=date(2026, 11, 13),
        window_end=date(2026, 11, 19),
        sections=[("other", "Other")],
    )
    assert r"\[Reuters\]" in markdown
    assert r"\[link\]" in markdown
    assert "<script>" not in markdown
    assert "<b>bold</b>" not in markdown


def test_digest_groups_quotes_under_one_article_heading() -> None:
    records = [
        _record(quote_id="Q-20261119-0001", text="First quoted statement from the same article."),
        _record(quote_id="Q-20261119-0002", text="Second quoted statement from the same article."),
    ]
    markdown = render_digest(
        records,
        report_date=date(2026, 11, 19),
        window_start=date(2026, 11, 19),
        window_end=date(2026, 11, 19),
        topic_pack=_topic_pack(),
        provider=_CountingProvider(),
    )

    assert markdown.count("### Grid policy announced") == 1
    assert markdown.count("- Source: Reuters; publication: Reuters") == 1
    assert "Q-20261119-0001" in markdown
    assert "Q-20261119-0002" in markdown


def test_digest_shows_speaker_and_readable_byline() -> None:
    record = _record(
        speaker="Ms. Jane Doe",
        article_author="https://news.example/people/jane-doe",
    )
    markdown = render_digest(
        [record],
        report_date=date(2026, 11, 19),
        window_start=date(2026, 11, 13),
        window_end=date(2026, 11, 19),
        sections=[("other", "Other")],
    )
    assert "- Speaker: Ms. Jane Doe" in markdown
    assert "- Byline: jane doe" in markdown
    assert "https://news.example/people/jane-doe" not in markdown


def test_rank_within_section_is_stable_total_order() -> None:
    first = _record(quote_id="Q-20261119-0004", topic_score=5.0)
    second = _record(quote_id="Q-20261119-0001", topic_score=5.0)
    claim = _record(quote_id="Q-20261119-0003", topic_score=9.0, claim_only=True)
    relayed = _record(quote_id="Q-20261119-0002", topic_score=9.0, flag="relayed wire copy")
    records = [first, second, claim, relayed]
    ranked = rank_within_section(records)
    assert [item.quote_id for item in ranked] == [
        "Q-20261119-0001",
        "Q-20261119-0004",
        "Q-20261119-0003",
        "Q-20261119-0002",
    ]
    assert rank_within_section(list(reversed(records))) == ranked
    assert records == [first, second, claim, relayed]


def test_digest_skips_inference_for_empty_sections() -> None:
    provider = _CountingProvider()
    record = _record(section="war_intensity", text="Official said talks continued.")
    markdown = render_digest(
        [record],
        report_date=date(2026, 11, 19),
        window_start=date(2026, 11, 13),
        window_end=date(2026, 11, 19),
        sections=[("war_intensity", "War intensity"), ("other", "Other")],
        provider=provider,
    )
    assert provider.sections == ["war_intensity"]
    assert "_No verified quotes in this window._" in markdown


def test_digest_reclassifies_default_section_at_render_time() -> None:
    pack = _topic_pack()
    record = _record(section="other", article_name="Grid policy announced")
    original_section = record.section_id
    markdown = render_digest(
        [record],
        report_date=date(2026, 11, 19),
        window_start=date(2026, 11, 13),
        window_end=date(2026, 11, 19),
        topic_pack=pack,
        provider=_CountingProvider(),
    )
    policy_block = markdown.split("## Policy", 1)[1].split("## Other", 1)[0]
    assert record.quote_text_original in policy_block
    assert record.section_id == original_section == "other"


def test_digest_footer_reports_counts_and_provider() -> None:
    record = _record()
    obsolete = _record(quote_id="Q-20261119-0009", text="Old quote", is_obsolete=True)
    markdown = render_digest(
        [record, obsolete],
        report_date=date(2026, 11, 19),
        window_start=date(2026, 11, 13),
        window_end=date(2026, 11, 19),
        sections=[("other", "Other")],
        provenance={
            "topic_id": "custom-topic",
            "provider_label": "OpenRouter v1",
            "source_count": 3,
            "quote_count": 7,
        },
        gaps=[{"source_id": "bbc", "gap_reason": "live_fetch_failed"}],
        review_badges={record.quote_id: "[Under Review]"},
    )
    footer = markdown.split("---")[-1]
    assert "window 2026-11-13 to 2026-11-19" in footer
    assert "topic custom-topic" in footer
    assert "sources 3; quotes 7" in footer
    assert "disclosed gaps 1 (bbc:live_fetch_failed)" in footer
    assert "obsolete 1; under review 1" in footer
    assert "provider OpenRouter v1" in footer


def test_digest_footer_counts_hidden_review_flags_as_obsolete() -> None:
    now = datetime(2026, 11, 19, 12, tzinfo=UTC)
    record = _record()
    flag = ObsolescenceFlag(
        evidence=ObsolescenceEvidence(
            old_quote_id=record.quote_id,
            old_content_hash="a" * 64,
            newer_article_id="art-2",
            newer_content_hash="b" * 64,
            newer_is_first_hand=True,
            newer_rights_cleared=True,
            superseding_span="Updated statement",
            decision_id="d-hidden",
            rationale="superseded",
        ),
        confidence=0.95,
        state="confirmed",
        reviewer="BI",
        reviewed_at=now - timedelta(hours=48),
    )
    assert flag.may_hide_quote(now=now)
    obsolete = _record(quote_id="Q-20261119-0009", text="Old quote", is_obsolete=True)
    markdown = render_digest(
        [record, obsolete],
        report_date=date(2026, 11, 19),
        window_start=date(2026, 11, 13),
        window_end=date(2026, 11, 19),
        sections=[("other", "Other")],
        obsolescence_flags=[flag],
        now=now,
    )
    footer = markdown.split("---")[-1]
    assert "obsolete 2; under review 0" in footer
