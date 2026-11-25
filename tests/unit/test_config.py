import pytest
from pydantic import SecretStr, ValidationError

from media_quotes.config import PROJECT_ROOT, Settings, unknown_setting_env_vars


def test_env_file_is_anchored_to_project_root():
    assert Settings.model_config["env_file"] == str(PROJECT_ROOT / ".env")


def test_defaults_are_safe(monkeypatch):
    monkeypatch.delenv("LOG_JSON", raising=False)
    settings = Settings(_env_file=None)
    assert settings.otel_enabled is False
    assert settings.digest_dir.name == "digests"
    assert settings.digest_dir.is_absolute()
    assert "psycopg" in settings.database_url_value


def test_log_json_env_override(monkeypatch):
    monkeypatch.setenv("LOG_JSON", "false")
    settings = Settings(_env_file=None)
    assert settings.log_json is False


def test_database_url_is_secret():
    settings = Settings(_env_file=None, database_url="sqlite://")
    assert "sqlite://" not in repr(settings.database_url)
    assert settings.database_url_value == "sqlite://"


def test_dec10_licence_limits_defaults():
    settings = Settings(_env_file=None)
    assert settings.max_quote_words == 30
    assert settings.attribution_format
    assert settings.licence_territory


def test_dec10_limits_accept_environment_override(monkeypatch):
    monkeypatch.setenv("MAX_QUOTE_WORDS", "12")
    settings = Settings(_env_file=None)
    assert settings.max_quote_words == 12
    assert settings.batch_model_snapshot == ""


def test_live_article_limit_default_and_override(monkeypatch):
    assert Settings(_env_file=None).live_article_limit == 5
    monkeypatch.setenv("LIVE_ARTICLE_LIMIT", "12")
    assert Settings(_env_file=None).live_article_limit == 12
    with pytest.raises(ValidationError):
        Settings(_env_file=None, live_article_limit=0)


def test_archive_article_limit_default_and_override(monkeypatch):
    assert Settings(_env_file=None).archive_article_limit == 500
    monkeypatch.setenv("ARCHIVE_ARTICLE_LIMIT", "12")
    assert Settings(_env_file=None).archive_article_limit == 12
    with pytest.raises(ValidationError):
        Settings(_env_file=None, archive_article_limit=0)


def test_batch_model_snapshot_rejects_placeholders():
    for value in ("unset", "latest", "UNSET"):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, batch_model_snapshot=value)


def test_extraction_fallback_accepts_rendered_but_rejects_typos():
    assert (
        Settings(_env_file=None, extraction_fallback="rendered").extraction_fallback == "rendered"
    )
    with pytest.raises(ValidationError):
        Settings(_env_file=None, extraction_fallback="browser")


def test_unknown_setting_env_vars_flags_typos():
    env = {
        "EXTRACTION_FALLBACKS": "static",
        "EXTRACTION_FALLBACK": "off",
        "PATH": "/bin",
    }
    assert unknown_setting_env_vars(env) == ["EXTRACTION_FALLBACKS"]


def test_shared_session_factory_is_cached():
    from media_quotes.pipeline import _shared_session_factory

    url = "postgresql+psycopg://news:news@127.0.0.1:5432/news"
    assert _shared_session_factory(url) is _shared_session_factory(url)


def test_quote_within_limits_counts_words():
    settings = Settings(_env_file=None)
    assert settings.quote_within_limits(" ".join(["word"] * 30))
    assert not settings.quote_within_limits(" ".join(["word"] * 31))
    assert settings.quote_within_limits("r\u00e9sum\u00e9 test")


def test_neo4j_password_required_only_when_sync_enabled():
    supplied = SecretStr("secret")
    settings = Settings(_env_file=None)
    assert settings.neo4j_password is None
    assert settings.neo4j_password_value == ""  # safe for callers when sync is disabled
    assert (
        Settings(_env_file=None, neo4j_password=supplied).neo4j_password_value
        == supplied.get_secret_value()
    )

    with pytest.raises(ValidationError, match="NEO4J_PASSWORD is required"):
        Settings(_env_file=None, sync_to_neo4j=True)
    with pytest.raises(ValidationError, match="NEO4J_PASSWORD is required"):
        Settings(_env_file=None, sync_to_neo4j=True, neo4j_password=SecretStr(""))

    enabled = Settings(_env_file=None, sync_to_neo4j=True, neo4j_password=supplied)
    assert enabled.neo4j_password_value == supplied.get_secret_value()
    assert supplied.get_secret_value() not in repr(enabled.neo4j_password)
