"""Unit tests for configuration loading and helpers."""

from bot import config


def _write_file(path, content: str):
    path.write_text(content, encoding="utf-8")


def test_load_config_uses_environment_over_file(tmp_path):
    cfg = tmp_path / "api_key.md"
    _write_file(
        cfg,
        """
        TELEGRAM_BOT_TOKEN=file_token
        GROQ_API_KEY=file_groq
        PIN_CODE=87654321
        CLAUDE_API_KEY=file_claude
        """.strip(),
    )

    loaded = config.load_config(
        str(cfg),
        env={
            "TELEGRAM_BOT_TOKEN": "env_token",
            "GROQ_API_KEY": "env_groq",
            "PIN_CODE": "12341234",
            "CLAUDE_API_KEY": "env_claude",
        },
        load_env_file=False,
    )

    assert loaded["TELEGRAM_BOT_TOKEN"] == "env_token"
    assert loaded["GROQ_API_KEY"] == "env_groq"
    assert loaded["PIN_CODE"] == "12341234"
    assert loaded["CLAUDE_API_KEY"] == "env_claude"


def test_load_config_ignores_invalid_config_lines(tmp_path):
    cfg = tmp_path / "api_key.md"
    _write_file(
        cfg,
        """
        # comment should be ignored
        TELEGRAM_BOT_TOKEN=file_token
        invalid-line
        PIN_CODE=11112222
        CLAUDE_API_KEY=sk-live-abc
        """.strip(),
    )

    loaded = config.load_config(str(cfg), env={}, load_env_file=False)

    assert loaded["TELEGRAM_BOT_TOKEN"] == "file_token"
    assert loaded["PIN_CODE"] == "11112222"
    assert loaded["CLAUDE_API_KEY"] == "sk-live-abc"
    assert loaded["GROQ_API_KEY"] == config.DEFAULT_CONFIG["GROQ_API_KEY"]


def test_load_config_defaults_when_missing_file():
    loaded = config.load_config(
        "definitely_missing_api_key.md",
        env={},
        load_env_file=False,
    )

    assert loaded["TELEGRAM_BOT_TOKEN"] == config.DEFAULT_CONFIG["TELEGRAM_BOT_TOKEN"]
    assert loaded["PIN_CODE"] == config.DEFAULT_CONFIG["PIN_CODE"]
    assert loaded["GROQ_API_KEY"] == config.DEFAULT_CONFIG["GROQ_API_KEY"]


def test_load_config_optional_download_dir_creation(tmp_path, monkeypatch):
    cfg = tmp_path / "api_key.md"
    _write_file(cfg, "TELEGRAM_BOT_TOKEN=token")
    created = []

    def fake_makedirs(path, exist_ok=True):
        created.append((path, exist_ok))

    monkeypatch.setattr(config.os, "makedirs", fake_makedirs)

    config.load_config(str(cfg), env={}, load_env_file=False, ensure_downloads_dir=False)
    assert created == []

    config.load_config(str(cfg), env={}, load_env_file=False, ensure_downloads_dir=True)
    assert created == [(config.DOWNLOAD_PATH, True)]
