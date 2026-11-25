"""Unit tests for the modular file-per-topic and file-per-source architecture."""

from __future__ import annotations

import importlib.util
import json
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from scripts import test_scraper as scraper

from media_quotes.adapters import (
    SourceAdapter,
    build_adapter,
    parse_newspaper_html,
    parse_rss_feed,
    register_adapter,
    register_source_adapter,
    registered_adapters,
    registered_source_adapters,
)
from media_quotes.adapters import registry as adapter_registry
from media_quotes.cli import main as cli_main
from media_quotes.config import get_settings
from media_quotes.curation import allowed_sections, assign_section, group_by_section
from media_quotes.digest import render_digest
from media_quotes.export import content_hash, quote_record
from media_quotes.llm import LocalStandInProvider
from media_quotes.source_configs import (
    ExtractorHints,
    SourceConfig,
    load_registry_rows,
    load_routes_from_sources,
    load_source_config,
    load_source_configs,
    scaffold_source_config,
    sync_sources_to_csv,
)
from media_quotes.topics import (
    TopicPack,
    TopicRelevanceConfig,
    TopicSection,
    list_topic_packs,
    load_all_topic_rules,
    load_topic_pack,
    scaffold_topic_pack,
)
from media_quotes.validation import validate_data_dir

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"


def test_builtin_topic_packs_load_and_evaluate() -> None:
    packs = {pack.topic_id: pack for pack in list_topic_packs(DATA_DIR / "topics")}
    assert {"ukraine-war", "ai-policy"}.issubset(packs)

    ukraine = packs["ukraine-war"]
    assert len(ukraine.sections) == 6
    assert ukraine.default_section == "other"
    assert ukraine.is_relevant("Air defence deliveries to Kyiv accelerated.", title="Ukraine war")
    assert not ukraine.is_relevant("Local bakery launches sourdough loaf.", title="Food review")

    ai_policy = packs["ai-policy"]
    assert "regulation" in ai_policy.section_ids()
    assert "safety_alignment" in ai_policy.section_ids()
    assert ai_policy.is_relevant(
        "Regulators finalized EU AI Act compliance rules for foundation models.",
        title="AI safety governance",
    )
    assert not ai_policy.is_relevant(
        "Artillery shell deliveries arrived at the eastern frontline in Ukraine.",
        title="Frontline update",
    )
    assert ai_policy.infer_section("Frontier model alignment red-teaming audit") == (
        "safety_alignment"
    )
    assert ai_policy.infer_section("EU AI Act licensing compliance enforcement") == "regulation"

    rules = load_all_topic_rules(DATA_DIR / "topics")
    assert "ukraine-war-v1" in rules
    assert "ai-policy-v1" in rules


def test_builtin_source_configs_and_multi_topic_feed_resolution() -> None:
    configs = load_source_configs(DATA_DIR / "sources")
    assert len(configs) == 28

    guardian = load_source_config("guardian", DATA_DIR / "sources")
    assert guardian.discovery_feed_url("ukraine-war") == (
        "https://www.theguardian.com/world/ukraine/rss"
    )
    assert guardian.discovery_feed_url("ai-policy") == (
        "https://www.theguardian.com/technology/artificialintelligenceai/rss"
    )
    assert len(guardian.routes_for_topic("ukraine-war")) == 1
    assert len(guardian.routes_for_topic("ai-policy")) == 1

    rows = load_registry_rows(DATA_DIR / "sources", fallback_csv=DATA_DIR / "sources.csv")
    assert len(rows) == 28
    routes = load_routes_from_sources(DATA_DIR / "sources")
    assert "guardian" in routes


def test_zero_code_extensibility_custom_topic_and_source_end_to_end(tmp_path: Path) -> None:
    """Prove adding a brand-new topic JSON + source JSON works end-to-end with zero code edits."""
    topics_dir = tmp_path / "topics"
    sources_dir = tmp_path / "sources"

    scaffold_path = scaffold_topic_pack(
        "energy-grid",
        name="Clean Energy & Grid Modernization",
        out_dir=topics_dir,
    )
    assert scaffold_path.is_file()

    # Customize the JSON file directly (the operator workflow — zero Python changes)
    custom_pack = TopicPack(
        topic_id="energy-grid",
        name="Clean Energy & Grid Modernization",
        description="Grid storage, transmission interconnection, and clean power policy.",
        default_section="other",
        sections=[
            TopicSection(
                section_id="grid_storage",
                section_name="Grid storage & batteries",
                keywords=["battery", "storage", "megawatt", "substation"],
                standin_summary="Storage deployments expanded grid resilience.",
            ),
            TopicSection(
                section_id="transmission",
                section_name="Transmission & interconnection",
                keywords=["transmission", "interconnection", "grid queue"],
                standin_summary="Operators streamlined transmission queues.",
            ),
            TopicSection(
                section_id="other",
                section_name="Other grid developments",
                keywords=[],
                standin_summary="Additional grid modernization statements.",
            ),
        ],
        relevance=TopicRelevanceConfig(
            rule_id="energy-grid-v1",
            anchors={"en": ["power grid", "grid storage", "transmission line", "interconnection"]},
            supporting_terms={"en": ["battery", "megawatt", "substation", "renewable"]},
            exclusions={"en": ["football", "celebrity", "recipe"]},
        ),
        quick_keywords=["power grid", "grid storage", "transmission line", "interconnection"],
    )
    scaffold_path.write_text(
        json.dumps(custom_pack.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8"
    )
    loaded_pack = load_topic_pack("energy-grid", topics_dir)

    src_path = scaffold_source_config(
        "gridnews",
        source_name="Grid News Daily",
        homepage_url="https://gridnews.example",
        feed_url="https://gridnews.example/rss.xml",
        topic_id="energy-grid",
        out_dir=sources_dir,
    )
    assert src_path.is_file()
    loaded_src = load_source_config("gridnews", sources_dir)
    assert loaded_src.enabled is False  # T0-safe by default
    assert loaded_src.discovery_feed_url("energy-grid") == "https://gridnews.example/rss.xml"

    # RSS screening under custom topic pack
    rss_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel>
      <item>
        <title>New 500 megawatt battery enters power grid storage service</title>
        <link>https://gridnews.example/2026/11/19/grid-battery</link>
        <pubDate>Thu, 19 Nov 2026 09:00:00 +0000</pubDate>
      </item>
      <item>
        <title>Local football club wins weekend derby</title>
        <link>https://gridnews.example/2026/11/19/football</link>
        <pubDate>Thu, 19 Nov 2026 09:00:00 +0000</pubDate>
      </item>
    </channel></rss>"""
    discovered = parse_rss_feed(rss_xml, topic_pack=loaded_pack, require_topic=True)
    assert len(discovered) == 1
    assert discovered[0]["url"].endswith("/grid-battery")

    # HTML quote extraction + section inference + curation + stand-in summary + digest
    article_html = """<!doctype html>
    <html lang="en"><head>
      <title>Power grid storage milestone reached</title>
      <meta property="og:site_name" content="Grid News Daily">
    </head><body><article>
      <h1>Power grid storage milestone reached</h1>
      <p>Regional operators commissioned a major power grid battery facility this week. The
      substation serves more than forty thousand households across the northern region and is
      the first of three storage projects scheduled for completion before the end of the year.
      Engineers described the commissioning tests as thorough and said the facility would help
      balance demand during the winter peak. Local officials attended the opening ceremony and
      noted that the project created jobs during construction and will support maintenance work
      for decades. Additional battery units are planned once the first phase is validated.</p>
      <p>Grid Director Elena Vance said "Utility-scale battery storage added four hundred
      megawatt hours of peak capacity to the regional substation overnight."</p>
    </article></body></html>"""
    parsed = parse_newspaper_html(
        article_html,
        source_url="https://gridnews.example/2026/11/19/grid-battery",
        topic_pack=loaded_pack,
        require_topic=True,
        extractor_hints=loaded_src.extractor_hints,
    )
    assert parsed["topic_relevant"] is True
    assert len(parsed["quote_spans"]) == 1
    span = parsed["quote_spans"][0]
    assert span["section_id"] == "grid_storage"
    assert parsed["article_text"][span["char_start"] : span["char_end"]] == span["quote_text"]

    # Off-topic HTML is rejected cleanly under the custom topic pack
    off_topic_html = """<!doctype html><html><head><title>Football derby</title></head>
    <body><article>
      <p>Coach Sam said "Our football team played with real passion today."</p>
    </article></body></html>"""
    off_parsed = parse_newspaper_html(
        off_topic_html,
        source_url="https://gridnews.example/2026/11/19/football",
        topic_pack=loaded_pack,
        require_topic=True,
    )
    assert off_parsed["topic_relevant"] is False
    assert off_parsed["quote_spans"] == []

    # Export record + curation + stand-in summary + Markdown digest rendering with custom sections
    record = quote_record(
        quote_id="Q-20261119-0001",
        quote_text_original=str(span["quote_text"]),
        source_id=loaded_src.source_id,
        source_name=loaded_src.source_name,
        publication_name=loaded_src.source_name,
        source_tier=loaded_src.tier,
        source_country_region=loaded_src.country_region,
        source_type=str(loaded_src.source_type),
        rights_status="license_held",
        access_mode="public_rss_permitted",
        license_reference="TEST-LIC-001",
        discovery_method="rss",
        claim_only=False,
        original_reporting_flag="first-hand",
        source_language="en",
        publication_date="2026-11-19",
        article_name="Power grid storage milestone reached",
        article_url="https://gridnews.example/2026/11/19/grid-battery",
        extraction_date="2026-11-19T12:00:00Z",
        section_id=str(span["section_id"]),
        is_obsolete=False,
        content_hash=content_hash([loaded_src.source_id, str(span["quote_text"])]),
        verification={
            "verbatim_match": True,
            "metadata_verified": True,
            "method": "deterministic_offset_v1",
            "verified_at": "2026-11-19T12:00:00Z",
        },
        topic_id=loaded_pack.topic_id,
    )
    sec_id = assign_section(
        record,
        allowed=allowed_sections(topic_pack=loaded_pack),
        default_section=loaded_pack.default_section,
    )
    assert sec_id == "grid_storage"
    provider = LocalStandInProvider(topic_pack=loaded_pack)
    grouped = group_by_section([record], topic_pack=loaded_pack)
    summary = provider.infer(
        section_id=sec_id,
        section_name="Grid storage & batteries",
        quote_texts=[record.quote_text_original],
    )
    assert "Storage deployments" in summary
    digest_md = render_digest(
        grouped[sec_id],
        report_date=date(2026, 11, 19),
        window_start=date(2026, 11, 13),
        window_end=date(2026, 11, 19),
        topic_pack=loaded_pack,
        provider=provider,
    )
    assert "Clean Energy & Grid Modernization" in digest_md
    assert "## Grid storage & batteries" in digest_md


def test_sync_sources_to_csv_and_modular_validation(tmp_path: Path) -> None:
    data = tmp_path / "data"
    shutil.copytree(DATA_DIR / "topics", data / "topics")
    shutil.copytree(DATA_DIR / "sources", data / "sources")
    for name in ("sections.csv", "quotes.schema.json"):
        shutil.copyfile(DATA_DIR / name, data / name)

    source_count, route_count = sync_sources_to_csv(
        data / "sources",
        sources_csv=data / "sources.csv",
        routes_csv=data / "source_topic_routes.csv",
    )
    assert source_count == 28
    assert route_count >= 28
    assert validate_data_dir(data) == []

    # Introduce drift between JSON and CSV -> validation catches it
    guardian_path = data / "sources" / "guardian.json"
    payload = json.loads(guardian_path.read_text(encoding="utf-8"))
    payload["enabled"] = False
    guardian_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    drift_errors = validate_data_dir(data)
    assert any("differs from sources.csv" in err for err in drift_errors)

    payload["enabled"] = True
    for field, value in (
        ("feed_url", "https://evil.example/rss"),
        ("license_reference", "changed-licence"),
        ("notes", "changed notes"),
    ):
        original = payload[field]
        payload[field] = value
        guardian_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        field_errors = validate_data_dir(data)
        assert any(f"{field}=" in err and "differs from sources.csv" in err for err in field_errors)
        payload[field] = original

    # Introduce unknown topic_id on a route -> validation catches it
    payload["routes"][0]["topic_id"] = "nonexistent-topic"
    guardian_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    topic_errors = validate_data_dir(data)
    assert any("unknown topic_id" in err for err in topic_errors)


def test_adapter_plugin_registry_hooks() -> None:
    class CustomPluginAdapter(SourceAdapter):
        def fetch(self, start: date, end: date) -> list[dict[str, Any]]:
            return []

    guardian_cfg = load_source_config("guardian", DATA_DIR / "sources")
    guardian_row = guardian_cfg.to_registry_row()

    # Cleanly register and restore global plugin hooks
    orig_adapters = dict(adapter_registry._ADAPTERS)
    orig_sources = dict(adapter_registry._SOURCE_ADAPTERS)
    try:
        register_adapter("custom_test_plugin")(CustomPluginAdapter)
        assert "custom_test_plugin" in registered_adapters()
        hinted_cfg = guardian_cfg.model_copy(
            update={"extractor_hints": ExtractorHints(adapter="custom_test_plugin")}
        )
        built = build_adapter(guardian_row, source_config=hinted_cfg)
        assert isinstance(built, CustomPluginAdapter)

        register_source_adapter("guardian")(CustomPluginAdapter)
        assert "guardian" in registered_source_adapters()
        built_by_source = build_adapter(guardian_row)
        assert isinstance(built_by_source, CustomPluginAdapter)
    finally:
        adapter_registry._ADAPTERS.clear()
        adapter_registry._ADAPTERS.update(orig_adapters)
        adapter_registry._SOURCE_ADAPTERS.clear()
        adapter_registry._SOURCE_ADAPTERS.update(orig_sources)


def test_migration_0005_topic_packs_metadata() -> None:
    mig_path = REPO_ROOT / "migrations" / "versions" / "0005_topic_packs.py"
    assert mig_path.is_file()
    spec = importlib.util.spec_from_file_location("mig_0005_topic_packs", mig_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0005_topic_packs"
    assert module.down_revision == "0004_topic_language_judge"
    # schema.sql is the single DDL source; 0005 is a no-op revision placeholder.
    sql = (REPO_ROOT / "src/media_quotes/db/schema.sql").read_text(encoding="utf-8")
    assert "topic_id" in sql


def test_cli_topics_and_sources_scaffolding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    get_settings.cache_clear()
    try:
        # List & show built-in topic packs
        assert cli_main(["topics", "--action", "list"]) == 0
        out = capsys.readouterr().out
        assert "ukraine-war" in out
        assert "ai-policy" in out

        assert cli_main(["topics", "--action", "show", "--topic", "ai-policy"]) == 0
        shown = json.loads(capsys.readouterr().out)
        assert shown["topic_id"] == "ai-policy"

        # Scaffold new topic pack & source in isolated temp dirs
        topics_dir = tmp_path / "topics"
        sources_dir = tmp_path / "sources"
        monkeypatch.setenv("TOPICS_DIR", str(topics_dir))
        monkeypatch.setenv("SOURCES_DIR", str(sources_dir))
        monkeypatch.setenv("SOURCES_CSV", str(tmp_path / "sources.csv"))
        monkeypatch.setenv("SOURCE_TOPICS_CSV", str(tmp_path / "source_topic_routes.csv"))
        get_settings.cache_clear()

        assert (
            cli_main(
                ["topics", "--action", "init", "--topic", "clean-tech", "--name", "Clean Tech"]
            )
            == 0
        )
        capsys.readouterr()
        assert (topics_dir / "clean-tech.json").is_file()

        assert (
            cli_main(
                [
                    "sources",
                    "--action",
                    "add",
                    "--source-id",
                    "techwire",
                    "--name",
                    "Tech Wire",
                    "--homepage-url",
                    "https://techwire.example",
                    "--feed-url",
                    "https://techwire.example/rss.xml",
                    "--topic",
                    "clean-tech",
                ]
            )
            == 0
        )
        capsys.readouterr()
        added = SourceConfig.model_validate_json(
            (sources_dir / "techwire.json").read_text(encoding="utf-8")
        )
        assert added.source_id == "techwire"
        assert added.enabled is False

        assert cli_main(["sources", "--action", "sync-csv"]) == 0
        capsys.readouterr()
        assert (tmp_path / "sources.csv").is_file()
        assert (tmp_path / "source_topic_routes.csv").is_file()
    finally:
        get_settings.cache_clear()


def test_scraper_inspect_html_pipeline_supports_ai_policy_topic() -> None:
    guardian_row = load_source_config("guardian", DATA_DIR / "sources").to_registry_row()
    ai_ctx = scraper.load_topic_configuration(topic_id="ai-policy")
    assert ai_ctx.topic_pack is not None
    assert ai_ctx.topic_pack.topic_id == "ai-policy"

    ai_html = """<!doctype html>
    <html lang="en"><head>
      <title>EU AI Act enforcement begins for frontier foundation models</title>
      <meta property="og:site_name" content="The Guardian">
      <meta name="author" content="Elena Rossi">
      <meta name="dc.date" content="2026-11-19">
      <link rel="canonical" href="https://www.theguardian.com/technology/2026/nov/19/eu-ai-act">
      <script type="application/ld+json">
      {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": "EU AI Act enforcement begins for frontier foundation models",
        "datePublished": "2026-11-19T10:00:00Z",
        "url": "https://www.theguardian.com/technology/2026/nov/19/eu-ai-act",
        "author": [{"@type": "Person", "name": "Elena Rossi"}],
        "publisher": {"@type": "Organization", "name": "The Guardian"}
      }
      </script>
    </head><body><article>
      <h1>EU AI Act enforcement begins for frontier foundation models</h1>
      <p>BRUSSELS — European artificial intelligence regulators announced mandatory safety
      evaluations for frontier models under the EU AI Act on Thursday.</p>
      <p>Commissioner Maria Lindqvist said "Every developer deploying a general-purpose
      artificial intelligence model in Europe must publish transparent safety evaluation audits
      before release."</p>
      <p>Industry compliance teams across Brussels and London have begun submitting technical
      governance documentation under the new statutory framework.</p>
    </article></body></html>"""

    report = scraper.inspect_html_pipeline(
        ai_html,
        source_row=guardian_row,
        source_url="https://www.theguardian.com/technology/2026/nov/19/eu-ai-act",
        window_start=date(2026, 11, 13),
        window_end=date(2026, 11, 19),
        context=ai_ctx,
    )
    assert report["topic_id"] == "ai-policy"
    assert report["topic_status"] == "accepted_ai-policy"
    assert report["quotes_detected"] == 1
    assert report["quotes_verified_publishable"] == 1
    assert report["quotes"][0]["section_id"] in {"regulation", "safety_alignment"}

    # Ukraine-war article is rejected when running under ai-policy topic
    ukraine_report = scraper.inspect_html_pipeline(
        scraper.DEMO_NEWSPAPER_HTML,
        source_row=guardian_row,
        source_url="https://www.theguardian.com/world/2026/nov/19/air-defence-winter",
        window_start=date(2026, 11, 13),
        window_end=date(2026, 11, 19),
        context=ai_ctx,
    )
    assert ukraine_report["topic_status"].startswith("rejected_off_topic")
    assert ukraine_report["quotes_detected"] == 0
