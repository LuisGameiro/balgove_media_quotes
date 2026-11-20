"""Minimal SQLAlchemy Core table definitions used by bootstrap loaders and repository writes.

The authoritative DDL lives in db/schema.sql; these definitions exist so SQLAlchemy
can build type-safe INSERT/UPDATE/SELECT statements across all PostgreSQL tables.
"""

from sqlalchemy import (
    ARRAY,
    JSON,
    Boolean,
    Column,
    Date,
    Integer,
    MetaData,
    String,
    Table,
    Uuid,
)

metadata = MetaData()

sources_table = Table(
    "sources",
    metadata,
    Column("source_id", String, primary_key=True),
    Column("source_name", String, nullable=False),
    Column("homepage_url", String, nullable=False),
    Column("feed_url", String, nullable=False, default=""),
    Column("tier", Integer, nullable=False),
    Column("cadence_hours", Integer, nullable=False),
    Column("country_region", String, nullable=False),
    Column("source_type", String, nullable=False),
    Column("claim_only", Boolean, nullable=False),
    Column("language", String, nullable=False),
    Column("requires_translation", Boolean, nullable=False),
    Column("paywalled", Boolean, nullable=True),
    Column("rights_status", String, nullable=False),
    Column("access_mode", String, nullable=False),
    Column("license_reference", String, nullable=False, default=""),
    Column("discovery_method", String, nullable=False),
    Column("archive_start_date", Date, nullable=False),
    Column("enabled", Boolean, nullable=False, default=False),
    Column("notes", String, nullable=False, default=""),
)

articles_table = Table(
    "articles",
    metadata,
    Column("article_id", Uuid(as_uuid=True), primary_key=True),
    Column("source_id", String, nullable=False),
    Column("canonical_url", String, nullable=False),
    Column("title", String, nullable=True),
    Column("publication_date", Date, nullable=True),
    Column("language", String, nullable=True),
    Column("raw_blob_ref", String, nullable=True),
    Column("extracted_text", String, nullable=True),
    Column("extraction_method", String, nullable=True),
    Column("extractor_version", String, nullable=True),
    Column("content_hash", String, nullable=False),
    Column("status", String, nullable=False),
    Column("article_metadata", JSON, nullable=False, default=dict),
)

quotes_table = Table(
    "quotes",
    metadata,
    Column("quote_id", Uuid(as_uuid=True), primary_key=True),
    Column("canonical_quote_id", String, nullable=False),
    Column("article_id", Uuid(as_uuid=True), nullable=False),
    Column("quote_text_raw", String, nullable=False),
    Column("quote_text_normalised", String, nullable=False),
    Column("char_start", Integer, nullable=False),
    Column("char_end", Integer, nullable=False),
    Column("dom_path", String, nullable=True),
    Column("speaker", String, nullable=True),
    Column("attribution_span", String, nullable=True),
    Column("extraction_method", String, nullable=False),
    Column("extractor_version", String, nullable=False),
    Column("verification_status", String, nullable=False),
    Column("verification_details", JSON, nullable=False, default=dict),
    Column("quote_metadata", JSON, nullable=False, default=dict),
)

quote_classifications_table = Table(
    "quote_classifications",
    metadata,
    Column("classification_id", Uuid(as_uuid=True), primary_key=True),
    Column("quote_id", Uuid(as_uuid=True), nullable=False),
    Column("section", String, nullable=False),
    Column("rationale", String, nullable=True),
    Column("decision_id", Uuid(as_uuid=True), nullable=True),
    Column("assigned_by", String, nullable=False, default="rule"),
)

obsolescence_flags_table = Table(
    "obsolescence_flags",
    metadata,
    Column("flag_id", Uuid(as_uuid=True), primary_key=True),
    Column("quote_id", Uuid(as_uuid=True), nullable=False),
    Column("is_obsolete", Boolean, nullable=False),
    Column("reason", String, nullable=True),
    Column("superseded_by", ARRAY(Uuid(as_uuid=True)), nullable=False, default=list),
    Column("decision_id", Uuid(as_uuid=True), nullable=True),
)

digests_table = Table(
    "digests",
    metadata,
    Column("digest_id", Uuid(as_uuid=True), primary_key=True),
    Column("digest_date", Date, nullable=False),
    Column("window_start", Date, nullable=False),
    Column("window_end", Date, nullable=False),
    Column("md_path", String, nullable=False),
    Column("manifest", JSON, nullable=False, default=dict),
)

eval_runs_table = Table(
    "eval_runs",
    metadata,
    Column("eval_id", Uuid(as_uuid=True), primary_key=True),
    Column("gold_set_version", String, nullable=False),
    Column("run_id", String, nullable=False),
    Column("report_date", Date, nullable=False),
    Column("metrics", JSON, nullable=False),
    Column("thresholds", JSON, nullable=False),
    Column("passed", Boolean, nullable=False),
    Column("trace_id", String, nullable=True),
)

pipeline_runs_table = Table(
    "pipeline_runs",
    metadata,
    Column("run_id", Uuid(as_uuid=True), primary_key=True),
    Column("pipeline", String, nullable=False),
    Column("partition_date", Date, nullable=True),
    Column("status", String, nullable=False),
    Column("stats", JSON, nullable=False, default=dict),
)

audit_events_table = Table(
    "audit_events",
    metadata,
    Column("event_id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", Uuid(as_uuid=True), nullable=True),
    Column("trace_id", String, nullable=True),
    Column("step", String, nullable=False),
    Column("entity_type", String, nullable=True),
    Column("entity_id", String, nullable=True),
    Column("status", String, nullable=False),
    Column("details", JSON, nullable=False, default=dict),
)

decision_cache_table = Table(
    "decision_cache",
    metadata,
    Column("cache_key", String, primary_key=True),
    Column("model_snapshot", String, nullable=False),
    Column("prompt_version", String, nullable=False),
    Column("schema_hash", String, nullable=False),
    Column("input_hash", String, nullable=False),
    Column("output", JSON, nullable=False),
    Column("trace_id", String, nullable=True),
)

gold_set_labels_table = Table(
    "gold_set_labels",
    metadata,
    Column("label_id", Uuid(as_uuid=True), primary_key=True),
    Column("gold_set_version", String, nullable=False),
    Column("split", String, nullable=False),
    Column("article_ref", String, nullable=False),
    Column("source_id", String, nullable=False),
    Column("publication_date", Date, nullable=False),
    Column("relevant", Boolean, nullable=False),
    Column("quote_text", String, nullable=True),
    Column("section_id", String, nullable=True),
    Column("human_label", Boolean, nullable=False),
    Column("is_negative_candidate", Boolean, nullable=False, default=False),
)

storage_partitions_table = Table(
    "storage_partitions",
    metadata,
    Column("table_name", String, primary_key=True),
    Column("month", Date, primary_key=True),
    Column("state", String, nullable=False),
)
