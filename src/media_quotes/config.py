"""Central configuration.

Secrets come from the environment / .env, never from code. All paths are resolved
relative to PROJECT_ROOT so the CLI, bootstrap and scripts share one convention.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Final

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Canonical defaults from docs/01 §6 #4 and the version manifest.
BACKFILL_START: Final = date(2026, 10, 1)
REPORT_WINDOW_DAYS: Final = 14
GOLD_SET_VERSION: Final = "synthetic-v1"

# DEC-10 licence-limit policy — enforced by the renderer, never by the frozen schema.
MAX_QUOTE_WORDS: Final = 30
ATTRIBUTION_FORMAT: Final = "Outlet — Named person (role), Article, YYYY-MM-DD, URL"
LICENCE_TERRITORY: Final = "internal worldwide"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    database_url: SecretStr = Field(
        default=SecretStr("postgresql+psycopg://news:news@localhost:5432/news")
    )
    log_level: str = "INFO"
    log_json: bool = True
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str | None = None

    digest_dir: Path = Path("digests")
    blob_dir: Path = Path("var/blobs")
    sources_csv: Path = Path("data/sources.csv")
    sections_csv: Path = Path("data/sections.csv")
    schema_path: Path = Path("data/quotes.schema.json")
    fixtures_dir: Path = Path("data/fixtures")
    gold_set_dir: Path = Path("data/gold_set")
    audit_dir: Path = Path("var/audit")
    export_dir: Path = Path("var/export")
    logs_dir: Path = Path("var/logs")
    alembic_ini: Path = Path("alembic.ini")

    # REPORT_DATE defaults to today but is normally pinned by --date for replay.
    report_date: date = Field(default_factory=date.today)
    backfill_start: date = BACKFILL_START
    report_window_days: int = REPORT_WINDOW_DAYS
    gold_set_version: str = GOLD_SET_VERSION

    # DEC-10 (docs/04): renderer-enforced rights limits; no change to quotes.schema.json.
    max_quote_words: int = Field(default=MAX_QUOTE_WORDS, gt=0)
    attribution_format: str = Field(default=ATTRIBUTION_FORMAT, min_length=1)
    licence_territory: str = Field(default=LICENCE_TERRITORY, min_length=1)
    external_publication: bool = False  # DEC-02: internal-only by default
    persist_to_postgres: bool = False  # C4: Dagster persists run/audit rows only when enabled

    # T4 will pin these; kept here so provenance columns have a value from day one.
    llm_model_snapshot: str = "unset"
    prompt_version: str = "standalone-inference-v1"

    @field_validator(
        "digest_dir",
        "blob_dir",
        "sources_csv",
        "sections_csv",
        "schema_path",
        "fixtures_dir",
        "gold_set_dir",
        "audit_dir",
        "export_dir",
        "logs_dir",
        "alembic_ini",
        mode="before",
    )
    @classmethod
    def _resolve_path(cls, value: object) -> Path:
        path = Path(str(value))
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def database_url_value(self) -> str:
        """Raw connection string; never log this value."""
        return self.database_url.get_secret_value()

    # Uppercase aliases are the names used in docs/01 §6 #4; env vars are case-insensitive.
    @property
    def REPORT_DATE(self) -> date:
        return self.report_date

    @property
    def BACKFILL_START(self) -> date:
        return self.backfill_start

    @property
    def REPORT_WINDOW_DAYS(self) -> int:
        return self.report_window_days

    @property
    def GOLD_SET_VERSION(self) -> str:
        return self.gold_set_version

    @property
    def MAX_QUOTE_WORDS(self) -> int:
        return self.max_quote_words

    @property
    def ATTRIBUTION_FORMAT(self) -> str:
        return self.attribution_format

    @property
    def LICENCE_TERRITORY(self) -> str:
        return self.licence_territory

    def quote_within_limits(self, quote_text: str) -> bool:
        """DEC-10 word-count gate; strict counting on Unicode whitespace."""
        return len(quote_text.split()) <= self.max_quote_words


@lru_cache
def get_settings() -> Settings:
    return Settings()
