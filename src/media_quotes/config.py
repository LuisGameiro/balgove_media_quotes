"""Central configuration.

Secrets come from the environment / .env, never from code. All paths are resolved
relative to PROJECT_ROOT so the CLI, bootstrap and scripts share one convention.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import date
from difflib import get_close_matches
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE_UNSET = object()

# Canonical defaults from docs/01 §6 #4 and the version manifest.
BACKFILL_START: Final = date(2026, 10, 1)
REPORT_WINDOW_DAYS: Final = 14
GOLD_SET_VERSION: Final = "synthetic-v1"
DEFAULT_TOPIC_ID: Final = "ukraine-war"

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

    def __init__(self, _env_file: Any = _ENV_FILE_UNSET, **values: Any) -> None:
        """Expose pydantic-settings' private init keys to static type checkers."""
        if _env_file is _ENV_FILE_UNSET:
            super().__init__(**values)
        else:
            super().__init__(_env_file=_env_file, **values)

    database_url: SecretStr = Field(
        default=SecretStr("postgresql+psycopg://news:news@127.0.0.1:5432/news")
    )
    log_level: str = "INFO"
    log_json: bool = True
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str | None = None

    output_dir: Path = Path("var/output")
    digest_dir: Path = Path("var/output/digests")
    blob_dir: Path = Path("var/output/raw")
    sources_csv: Path = Path("data/sources.csv")
    sources_dir: Path = Path("data/sources")
    sections_csv: Path = Path("data/sections.csv")
    source_topics_csv: Path = Path("data/source_topic_routes.csv")
    topic_rules_path: Path = Path("data/topic_rules.json")
    topics_dir: Path = Path("data/topics")
    active_topic: str = DEFAULT_TOPIC_ID
    schema_path: Path = Path("data/quotes.schema.json")
    fixtures_dir: Path = Path("data/fixtures")
    gold_set_dir: Path = Path("data/gold_set")
    audit_dir: Path = Path("var/output/audit")
    export_dir: Path = Path("var/output/quotes")
    logs_dir: Path = Path("var/output/logs")
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
    persist_to_postgres: bool = False  # C4: Dagster persists run/audit rows only when enabled

    # Inference provider is selected by environment only. There is no CLI flag.
    # local keeps all runs offline; openrouter uses the OpenAI-compatible API.
    llm_provider: str = "local"
    llm_model_snapshot: str = "unset"
    prompt_version: str = "standalone-inference-v1"
    openrouter_api_key: SecretStr | None = None
    dashboard_password: SecretStr | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_app_url: str = "https://github.com/balgoveinnovations/media-quotes"
    openrouter_app_title: str = "Media Quotes Collection System"
    openrouter_timeout_seconds: float = 30.0
    openrouter_max_retries: int = 2

    # Optional workstreams: classify/translate quotes and AI-assisted review.
    translation_mode: str = "off"
    ai_judge_mode: str = "off"
    ai_judge_model_snapshot: str = "unset"
    judge_prompt_version: str = "judge-v1"
    translation_prompt_version: str = "translation-v1"
    extraction_fallback: str = "off"  # off | static | rendered

    # Batch backfill policy (Opt-in; requires an explicit cost cap on the CLI).
    batch_model_snapshot: str = ""
    batch_endpoint: str = "/v1/responses"

    # Live fetch policy (used by scripts/test_scraper.py and mq fetch).
    fetch_timeout_seconds: float = Field(default=10.0, gt=0)
    fetch_requests_per_second: float = Field(default=1.0, gt=0)
    fetch_max_bytes: int = Field(default=5_000_000, gt=0)
    fetch_max_connections: int = Field(default=10, gt=0)

    # Neo4j derived graph projection (opt-in; Postgres remains system of record)
    sync_to_neo4j: bool = False
    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("password123")
    neo4j_database: str = "neo4j"

    def for_source_mode(self, source_mode: str) -> Settings:
        """Return settings with fixture artifacts isolated from operational output."""
        if source_mode != "fixtures":
            return self
        root = self.output_dir.parent
        if self.audit_dir != self.output_dir / "audit":
            root = self.audit_dir.parent
        test_output = root / "test-output"
        return self.model_copy(
            update={
                "output_dir": test_output,
                "digest_dir": test_output / "digests",
                "blob_dir": test_output / "raw",
                "audit_dir": test_output / "audit",
                "export_dir": test_output / "quotes",
                "logs_dir": test_output / "logs",
            }
        )

    @model_validator(mode="before")
    @classmethod
    def _derive_output_paths(cls, values: object) -> object:
        if not isinstance(values, dict):
            return values
        raw_output = values.get("output_dir") or os.getenv("OUTPUT_DIR", "var/output")
        output = Path(str(raw_output))
        for key, sub in (
            ("digest_dir", "digests"),
            ("blob_dir", "raw"),
            ("audit_dir", "audit"),
            ("export_dir", "quotes"),
            ("logs_dir", "logs"),
        ):
            if not values.get(key):
                values[key] = output / sub
        return values

    @field_validator(
        "output_dir",
        "digest_dir",
        "blob_dir",
        "sources_csv",
        "sources_dir",
        "sections_csv",
        "source_topics_csv",
        "topic_rules_path",
        "topics_dir",
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

    @field_validator("translation_mode", mode="before")
    @classmethod
    def _validate_translation_mode(cls, value: object) -> str:
        mode = str(value).strip().lower()
        if mode not in {"off", "detect_only", "translate"}:
            raise ValueError("translation_mode must be off, detect_only or translate")
        return mode

    @field_validator("batch_model_snapshot", mode="before")
    @classmethod
    def _validate_batch_model_snapshot(cls, value: object) -> str:
        snapshot = str(value).strip()
        if snapshot.lower() in {"unset", "latest"}:
            raise ValueError("batch_model_snapshot must be pinned (no 'unset'/'latest')")
        return snapshot

    @field_validator("extraction_fallback", mode="before")
    @classmethod
    def _validate_extraction_fallback(cls, value: object) -> str:
        mode = str(value).strip().lower()
        if mode not in {"off", "static", "rendered"}:
            raise ValueError("extraction_fallback must be off, static or rendered")
        return mode

    @field_validator("ai_judge_mode", mode="before")
    @classmethod
    def _validate_ai_judge_mode(cls, value: object) -> str:
        mode = str(value).strip().lower()
        if mode not in {"off", "shadow", "advisory", "decision"}:
            raise ValueError("ai_judge_mode must be off, shadow, advisory or decision")
        return mode

    @staticmethod
    def _require_pinned_snapshot(value: str, name: str) -> None:
        snapshot = value.strip()
        if not snapshot or snapshot.lower() in {"unset", "latest"}:
            raise ValueError(f"{name} must be a pinned snapshot id (no 'unset'/'latest')")

    @model_validator(mode="after")
    def _validate_llm_provider(self) -> Settings:
        provider = self.llm_provider.strip().lower()
        if provider not in {"local", "openrouter"}:
            raise ValueError("llm_provider must be 'local' or 'openrouter'")
        self.llm_provider = provider
        if provider != "openrouter":
            return self
        if self.openrouter_api_key is None or not self.openrouter_api_key.get_secret_value():
            raise ValueError("OPENROUTER_API_KEY is required when LLM_PROVIDER=openrouter")
        model = self.llm_model_snapshot.strip()
        if not model or model.lower() in {"unset", "latest"}:
            raise ValueError(
                "LLM_MODEL_SNAPSHOT must be a pinned OpenRouter model id when "
                "LLM_PROVIDER=openrouter"
            )
        return self

    @model_validator(mode="after")
    def _check_translation_mode(self) -> Settings:
        if self.translation_mode != "translate":
            return self
        if self.llm_provider != "openrouter":
            raise ValueError("TRANSLATION_MODE=translate requires LLM_PROVIDER=openrouter")
        self._require_pinned_snapshot(self.llm_model_snapshot, "LLM_MODEL_SNAPSHOT")
        return self

    @model_validator(mode="after")
    def _check_ai_judge_mode(self) -> Settings:
        if self.ai_judge_mode == "off":
            return self
        if self.llm_provider != "openrouter":
            raise ValueError("AI_JUDGE_MODE != off requires LLM_PROVIDER=openrouter")
        if self.openrouter_api_key is None or not self.openrouter_api_key.get_secret_value():
            raise ValueError("OPENROUTER_API_KEY is required when AI_JUDGE_MODE != off")
        self._require_pinned_snapshot(self.ai_judge_model_snapshot, "AI_JUDGE_MODEL_SNAPSHOT")
        return self

    @property
    def database_url_value(self) -> str:
        """Raw connection string; never log this value."""
        return self.database_url.get_secret_value()

    @property
    def neo4j_password_value(self) -> str:
        """Raw Neo4j password; never log this value."""
        return self.neo4j_password.get_secret_value()

    def quote_within_limits(self, quote_text: str) -> bool:
        """DEC-10 word-count gate; strict counting on Unicode whitespace."""
        return len(quote_text.split()) <= self.max_quote_words


def unknown_setting_env_vars(environ: Mapping[str, str]) -> list[str]:
    """Env names close to a Settings field but not equal to one (likely typos)."""
    known = sorted({name.upper() for name in Settings.model_fields})
    return sorted(
        name
        for name in environ
        if "_" in name and name not in known and get_close_matches(name, known, n=1, cutoff=0.85)
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
