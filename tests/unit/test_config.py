from media_quotes.config import Settings


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
