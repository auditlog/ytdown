"""Focused integration tests for Telegram command/callback module boundaries."""

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock, Mock

import pytest

from bot import telegram_callbacks as callbacks
from bot import telegram_commands as commands
from bot.handlers import time_range_callbacks as _trc
from tests.telegram_callbacks_support import _make_context as _make_callback_context
from tests.telegram_callbacks_support import _make_update as _make_callback_update
from tests.telegram_commands_support import (
    _attach_runtime,
    _make_context as _make_command_context,
    _make_update as _make_command_update,
)


@pytest.mark.integration
def test_runtime_auth_flow_continues_pending_url_after_successful_pin(monkeypatch):
    update = _make_command_update(text="12345678", user_id=222, chat_id=222)
    context = _make_command_context()
    runtime = _attach_runtime(context, authorized_users=set())
    runtime.session_store.set_field(222, "awaiting_pin", True)
    runtime.session_store.set_field(
        222,
        "pending_action",
        {"kind": "url", "payload": "https://youtube.com/watch?v=abc"},
    )

    monkeypatch.setattr(commands, "get_runtime_value", lambda key, default=None: "12345678" if key == "PIN_CODE" else default)
    monkeypatch.setattr(commands, "failed_attempts", defaultdict(int))

    resumed = {}

    async def fake_process_youtube_link(update_arg, context_arg, url):
        resumed["url"] = url

    monkeypatch.setattr(commands, "process_youtube_link", fake_process_youtube_link)

    handled = asyncio.run(commands.handle_pin(update, context))

    assert handled is True
    assert 222 in runtime.authorized_users_set
    assert resumed["url"] == "https://youtube.com/watch?v=abc"
    assert runtime.session_store.get_field(222, "awaiting_pin") is None
    assert runtime.session_store.get_field(222, "pending_url") is None


@pytest.mark.integration
def test_callback_time_range_preset_updates_session_and_returns_to_menu(monkeypatch):
    chat_id = 333
    url = "https://www.youtube.com/watch?v=abc"
    update = _make_callback_update("time_range_preset_first_10", chat_id=chat_id)
    context = _make_callback_context()
    runtime = _attach_runtime(context, authorized_users=set())
    runtime.session_store.set_field(chat_id, "current_url", url)

    monkeypatch.setattr(_trc, "get_video_info", lambda *_: {"duration": 900, "title": "Sample"})

    returned = {}

    async def fake_back_to_main_menu(update_arg, context_arg, back_url):
        returned["url"] = back_url

    monkeypatch.setattr(_trc, "back_to_main_menu", fake_back_to_main_menu)

    asyncio.run(callbacks.handle_callback(update, context))

    assert runtime.session_store.get_field(chat_id, "time_range") == {
        "start": "0:00",
        "end": "10:00",
        "start_sec": 0,
        "end_sec": 600,
    }
    assert returned["url"] == url


@pytest.mark.integration
def test_callback_spotify_summary_route_uses_runtime_session_state(monkeypatch):
    chat_id = 444
    update = _make_callback_update("summary_option_3", chat_id=chat_id)
    context = _make_callback_context()
    runtime = _attach_runtime(context, authorized_users=set())
    runtime.session_store.set_field(chat_id, "current_url", "https://open.spotify.com/episode/test")
    runtime.session_store.set_field(chat_id, "platform", "spotify")
    runtime.session_store.set_field(
        chat_id,
        "spotify_resolved",
        {"source": "youtube", "youtube_url": "https://youtube.com/watch?v=xyz", "title": "Episode"},
    )

    called = {}

    async def fake_download_spotify_resolved(update_arg, context_arg, resolved, fmt, **kwargs):
        called["resolved"] = resolved
        called["fmt"] = fmt
        called["summary"] = kwargs.get("summary")
        called["summary_type"] = kwargs.get("summary_type")

    monkeypatch.setattr(callbacks, "download_spotify_resolved", fake_download_spotify_resolved)

    asyncio.run(callbacks.handle_callback(update, context))

    assert called["resolved"]["title"] == "Episode"
    assert called["fmt"] == "mp3"
    assert called["summary"] is True
    assert called["summary_type"] == 3
