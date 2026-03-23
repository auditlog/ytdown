"""Download- and routing-oriented tests for Telegram callbacks."""

import asyncio

import pytest

from bot import telegram_callbacks as tc
from tests.telegram_callbacks_support import _make_context, _make_update


def test_handle_callback_video_and_audio_download_data_dispatch():
    tc.user_urls[555] = "https://www.youtube.com/watch?v=abc"

    audio_update = _make_update("dl_audio_format_140", chat_id=555)
    video_update = _make_update("dl_video_720p", chat_id=555)
    context = _make_context()

    calls = []

    async def fake_download_file(update_arg, context_arg, type_arg, format_arg, url, **kwargs):
        calls.append((type_arg, format_arg, url, kwargs))

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tc, "download_file", fake_download_file)
    try:
        asyncio.run(tc.handle_callback(audio_update, context))
        asyncio.run(tc.handle_callback(video_update, context))
    finally:
        monkeypatch.undo()

    assert ("audio", "140", "https://www.youtube.com/watch?v=abc", {"use_format_id": True}) in calls
    assert ("video", "720p", "https://www.youtube.com/watch?v=abc", {}) in calls


def test_handle_callback_invalid_format_id_does_not_download(monkeypatch):
    tc.user_urls[555] = "https://www.youtube.com/watch?v=abc"
    invalid_update = _make_update("dl_video_bad", chat_id=555)
    context = _make_context()

    called = False

    async def fake_download_file(update_arg, context_arg, type_arg, format_arg, url, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(tc, "download_file", fake_download_file)
    asyncio.run(tc.handle_callback(invalid_update, context))

    assert called is False
    invalid_update.callback_query.edit_message_text.assert_awaited_once_with(
        "Nieobsługiwany format. Spróbuj wybrać format ponownie."
    )


def test_handle_callback_formats_and_summary_option_routes():
    tc.user_urls[777] = "https://www.youtube.com/watch?v=abc"
    context = _make_context()

    format_update = _make_update("formats", chat_id=777)
    summary_update = _make_update("summary_option_4", chat_id=777)

    shown = {}
    transcribed = {}

    async def fake_handle_formats_list(update_arg, context_arg, url):
        shown["formats_url"] = url

    async def fake_download_file(update_arg, context_arg, type_arg, format_arg, url, transcribe=False, summary=False, summary_type=None):
        transcribed["type"] = type_arg
        transcribed["format"] = format_arg
        transcribed["url"] = url
        transcribed["summary"] = summary
        transcribed["summary_type"] = summary_type

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tc, "handle_formats_list", fake_handle_formats_list)
    monkeypatch.setattr(tc, "download_file", fake_download_file)
    try:
        asyncio.run(tc.handle_callback(format_update, context))
        asyncio.run(tc.handle_callback(summary_update, context))
    finally:
        monkeypatch.undo()

    assert shown["formats_url"] == "https://www.youtube.com/watch?v=abc"
    assert transcribed["summary"] is True
    assert transcribed["summary_type"] == 4


def test_handle_callback_summary_option_invalid_shows_warning():
    tc.user_urls[555] = "https://www.youtube.com/watch?v=abc"
    update = _make_update("summary_option_999", chat_id=555)
    context = _make_context()

    called = {}

    async def fake_download_file(update_arg, context_arg, type_arg, format_arg, url, **kwargs):
        called["called"] = True

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tc, "download_file", fake_download_file)
    try:
        asyncio.run(tc.handle_callback(update, context))
    finally:
        monkeypatch.undo()

    update.callback_query.edit_message_text.assert_awaited_once_with("Nieobsługiwana opcja podsumowania.")
    assert "called" not in called


def test_handle_callback_time_range_preset_dispatch(monkeypatch):
    tc.user_urls[999] = "https://www.youtube.com/watch?v=abc"
    update = _make_update("time_range_preset_first_10", chat_id=999)
    context = _make_context()

    dispatched = {}

    async def fake_apply_time_range_preset(update_arg, context_arg, url, preset):
        dispatched["url"] = url
        dispatched["preset"] = preset

    monkeypatch.setattr(tc, "apply_time_range_preset", fake_apply_time_range_preset)
    asyncio.run(tc.handle_callback(update, context))

    update.callback_query.answer.assert_awaited_once()
    assert dispatched["url"] == "https://www.youtube.com/watch?v=abc"
    assert dispatched["preset"] == "first_10"


def test_apply_time_range_preset_first_5_sets_range(monkeypatch):
    chat_id = 111
    update = _make_update("time_range_preset_first_5", chat_id=chat_id)
    context = _make_context()
    url = "https://www.youtube.com/watch?v=abc"
    tc.user_urls[chat_id] = url

    back_calls = {}

    async def fake_back(update_arg, context_arg, back_url):
        back_calls["url"] = back_url

    monkeypatch.setattr(tc, "get_video_info", lambda *_: {"duration": 370, "title": "Sample"})
    monkeypatch.setattr(tc, "back_to_main_menu", fake_back)

    asyncio.run(tc.apply_time_range_preset(update, context, url, "first_5"))

    assert tc.user_time_ranges[chat_id] == {
        "start": "0:00",
        "end": "5:00",
        "start_sec": 0,
        "end_sec": 300,
    }
    assert back_calls["url"] == url


def test_apply_time_range_preset_zero_duration_shows_error(monkeypatch):
    chat_id = 222
    update = _make_update("time_range_preset_last_5", chat_id=chat_id)
    context = _make_context()
    tc.user_urls[chat_id] = "https://www.youtube.com/watch?v=abc"

    monkeypatch.setattr(tc, "get_video_info", lambda *_: {"duration": 0})
    asyncio.run(tc.apply_time_range_preset(update, context, tc.user_urls[chat_id], "last_5"))

    update.callback_query.edit_message_text.assert_awaited_once_with(
        "Nie można określić czasu trwania filmu."
    )


def test_handle_callback_time_range_options_and_clear():
    tc.user_urls[888] = "https://www.youtube.com/watch?v=abc"
    tc.user_time_ranges[888] = {"start": "0:10", "end": "1:00", "start_sec": 10, "end_sec": 60}
    context = _make_context()

    shown = {}
    back_called = {}

    async def fake_show_time_range_options(update_arg, context_arg, url):
        shown["time_range_url"] = url

    async def fake_back(update_arg, context_arg, url):
        back_called["url"] = url

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tc, "show_time_range_options", fake_show_time_range_options)
    monkeypatch.setattr(tc, "back_to_main_menu", fake_back)
    try:
        asyncio.run(tc.handle_callback(_make_update("time_range", chat_id=888), context))
        asyncio.run(tc.handle_callback(_make_update("time_range_clear", chat_id=888), context))
    finally:
        monkeypatch.undo()

    assert shown["time_range_url"] == "https://www.youtube.com/watch?v=abc"
    assert back_called["url"] == "https://www.youtube.com/watch?v=abc"
    assert 888 not in tc.user_time_ranges
