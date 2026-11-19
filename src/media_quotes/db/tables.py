"""Minimal SQLAlchemy Core table definitions used by bootstrap loaders.

The authoritative DDL lives in db/schema.sql; these definitions exist only so SQLAlchemy
can build INSERT statements for the registry.
"""

from sqlalchemy import Boolean, Column, Date, Integer, MetaData, String, Table

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
