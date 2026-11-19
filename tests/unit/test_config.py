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


def test_dec10_licence_limits_defaults():
    settings = Settings(_env_file=None)
    assert settings.max_quote_words == 30
    assert settings.attribution_format == settings.ATTRIBUTION_FORMAT
    assert settings.licence_territory == settings.LICENCE_TERRITORY
    assert settings.external_publication is False  # DEC-02 internal-only


def test_dec10_limits_accept_environment_override(monkeypatch):
    monkeypatch.setenv("MAX_QUOTE_WORDS", "12")
    monkeypatch.setenv("EXTERNAL_PUBLICATION", "true")
    settings = Settings(_env_file=None)
    assert settings.max_quote_words == 12
    assert settings.external_publication is True


def test_quote_within_limits_counts_words():
    settings = Settings(_env_file=None)
    assert settings.quote_within_limits(" ".join(["word"] * 30))
    assert not settings.quote_within_limits(" ".join(["word"] * 31))
    assert settings.quote_within_limits("r\u00e9sum\u00e9 test")
