"""Unit tests for application entrypoint."""

import asyncio
from argparse import Namespace
from types import SimpleNamespace

from unittest.mock import AsyncMock, Mock

import main as app_main
from bot.runtime import AppRuntime
from bot.session_store import SecurityStore, SessionStore


def test_set_bot_commands_configures_menu():
    app = Mock()
    app.bot = Mock()
    app.bot.set_my_commands = AsyncMock()

    asyncio.run(app_main.set_bot_commands(app))

    app.bot.set_my_commands.assert_called_once()


class DummyFilter:
    def __and__(self, other):
        return self

    def __invert__(self):
        return self

    def __or__(self, other):
        return self


def test_main_cli_mode_calls_cli(monkeypatch):
    args = Namespace(cli=True, url=None, list_formats=False, format=None, audio_only=False, audio_quality="192")
    monkeypatch.setattr(app_main, "parse_arguments", lambda: args)
    monkeypatch.setattr(app_main, "initialize_runtime", Mock())

    cli_called = Mock()
    monkeypatch.setattr(app_main, "cli_mode", cli_called)

    app_main.main()

    cli_called.assert_called_once_with(args)


def test_main_starts_bot_in_non_cli_mode(monkeypatch):
    args = Namespace(cli=False, url=None, list_formats=False, format=None, audio_only=False, audio_quality="192")
    app = Mock()
    app.add_handler = Mock()
    app.bot_data = {}
    app.job_queue = Mock()
    app.job_queue.run_once = Mock()
    app.run_polling = Mock()

    builder = Mock()
    builder.token.return_value = builder
    builder.connect_timeout.return_value = builder
    builder.read_timeout.return_value = builder
    builder.write_timeout.return_value = builder
    builder.build.return_value = app

    monkeypatch.setattr(app_main, "parse_arguments", lambda: args)
    monkeypatch.setattr(app_main, "initialize_runtime", Mock())
    monkeypatch.setattr(app_main, "ApplicationBuilder", lambda: builder)
    monkeypatch.setattr(app_main, "monitor_disk_space", Mock())
    monkeypatch.setattr(app_main, "periodic_cleanup", Mock())
    monkeypatch.setattr(
        app_main,
        "build_app_runtime",
        lambda: AppRuntime(
            config={"TELEGRAM_BOT_TOKEN": "test-token"},
            session_store=SessionStore(),
            security_store=SecurityStore(),
            services=Mock(),
            authorized_users_repository=Mock(),
            download_history_repository=Mock(),
            authorized_users_set=set(),
        ),
    )

    # Avoid Telegram-specific filter operations in handler registration
    filters = SimpleNamespace(
        TEXT=DummyFilter(),
        COMMAND=DummyFilter(),
        VOICE="VOICE",
        AUDIO="AUDIO",
        VIDEO=DummyFilter(),
        Document=SimpleNamespace(MimeType=lambda *_: DummyFilter()),
    )
    monkeypatch.setattr(app_main, "filters", filters)

    monkeypatch.setattr(app_main, "CommandHandler", lambda *args, **kwargs: ("command_handler", args, kwargs))
    monkeypatch.setattr(app_main, "MessageHandler", lambda *args, **kwargs: ("message_handler", args, kwargs))
    monkeypatch.setattr(app_main, "CallbackQueryHandler", lambda *args, **kwargs: ("callback_handler", args, kwargs))

    class DummyThread:
        def __init__(self, target=None, daemon=False):
            self.target = target

        def start(self):
            # Keep tests side-effect free: don't run cleanup thread
            pass

    monkeypatch.setattr(app_main.threading, "Thread", DummyThread)

    app_main.main()

    assert builder.token.called
    assert "app_runtime" in app.bot_data
    app.run_polling.assert_called_once()
    assert app.add_handler.call_count >= 7


def test_build_application_reads_token_from_runtime_config(monkeypatch):
    app = Mock()
    app.bot_data = {}
    app.job_queue = Mock()
    app.job_queue.run_once = Mock()

    builder = Mock()
    builder.token.return_value = builder
    builder.connect_timeout.return_value = builder
    builder.read_timeout.return_value = builder
    builder.write_timeout.return_value = builder
    builder.build.return_value = app

    monkeypatch.setattr(app_main, "ApplicationBuilder", lambda: builder)

    runtime = AppRuntime(
        config={"TELEGRAM_BOT_TOKEN": "runtime-token"},
        session_store=SessionStore(),
        security_store=SecurityStore(),
        services=Mock(),
        authorized_users_repository=Mock(),
        download_history_repository=Mock(),
        authorized_users_set=set(),
    )
    built_app = app_main.build_application(runtime=runtime)

    assert built_app is app
    builder.token.assert_called_once_with("runtime-token")
    assert app.bot_data["app_runtime"] is runtime
