"""Unit tests for application entrypoint."""

import asyncio
from argparse import Namespace
from types import SimpleNamespace

from unittest.mock import AsyncMock, Mock

import main as app_main


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

    cli_called = Mock()
    monkeypatch.setattr(app_main, "cli_mode", cli_called)

    app_main.main()

    cli_called.assert_called_once_with(args)


def test_main_starts_bot_in_non_cli_mode(monkeypatch):
    args = Namespace(cli=False, url=None, list_formats=False, format=None, audio_only=False, audio_quality="192")
    app = Mock()
    app.add_handler = Mock()
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
    monkeypatch.setattr(app_main, "ApplicationBuilder", lambda: builder)
    monkeypatch.setattr(app_main, "monitor_disk_space", Mock())
    monkeypatch.setattr(app_main, "periodic_cleanup", Mock())

    # Avoid Telegram-specific filter operations in handler registration
    filters = SimpleNamespace(
        TEXT=DummyFilter(),
        COMMAND=DummyFilter(),
        VOICE="VOICE",
        AUDIO="AUDIO",
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
    app.run_polling.assert_called_once()
    assert app.add_handler.call_count >= 7
