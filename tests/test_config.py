from receptionist.config import Settings


def test_defaults_without_env_file(monkeypatch):
    for name in ("RECEPTIONIST_BOT_TOKEN", "RECEPTIONIST_ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)
    assert settings.bot_token is None
    assert settings.anthropic_api_key is None
    assert settings.model == "claude-opus-5-5"
    assert settings.emergency_number == "103"
    assert settings.database_url.endswith("@localhost:5433/receptionist")


def test_global_anthropic_base_url_is_ignored(monkeypatch):
    # Claude Code exports ANTHROPIC_BASE_URL for its local proxy; the app must not use it.
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8787")
    monkeypatch.delenv("RECEPTIONIST_ANTHROPIC_BASE_URL", raising=False)
    assert Settings(_env_file=None).anthropic_base_url == "https://api.anthropic.com"


def test_empty_values_mean_not_set(monkeypatch):
    monkeypatch.setenv("RECEPTIONIST_ADMIN_CHAT_ID", "")
    monkeypatch.setenv("RECEPTIONIST_BOT_TOKEN", "")
    settings = Settings(_env_file=None)
    assert settings.admin_chat_id is None
    assert settings.bot_token is None


def test_admin_chat_id_parses_group_id(monkeypatch):
    monkeypatch.setenv("RECEPTIONIST_ADMIN_CHAT_ID", "-1001234567890")
    assert Settings(_env_file=None).admin_chat_id == -1001234567890


def test_secrets_are_masked(monkeypatch):
    monkeypatch.setenv("RECEPTIONIST_BOT_TOKEN", "123456:SECRET")
    settings = Settings(_env_file=None)
    assert "SECRET" not in repr(settings)
    assert settings.bot_token.get_secret_value() == "123456:SECRET"
