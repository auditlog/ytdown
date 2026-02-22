"""
Unit tests for helper utilities in bot.telegram_callbacks.
"""

import asyncio

from unittest.mock import AsyncMock, Mock
import pytest
from telegram.error import BadRequest

from bot import telegram_callbacks as tc


def test_format_bytes_formats_values():
    assert tc.format_bytes(None) == "?"
    assert tc.format_bytes(512) == "512.0 B"
    assert tc.format_bytes(2 * 1024) == "2.0 KB"
    assert tc.format_bytes(3 * 1024 * 1024) == "3.0 MB"
    assert tc.format_bytes(5 * 1024 * 1024 * 1024) == "5.0 GB"
    assert tc.format_bytes(6 * 1024 * 1024 * 1024 * 1024) == "6.0 TB"


def test_format_eta_formats():
    assert tc.format_eta(None) == "?"
    assert tc.format_eta(-1) == "?"
    assert tc.format_eta(45) == "45s"
    assert tc.format_eta(125) == "2m 5s"
    assert tc.format_eta(3661) == "1h 1m"


def test_create_progress_hook_stores_downloading_status():
    hook = tc.create_progress_hook(101)
    hook({
        "status": "downloading",
        "_percent_str": "45%",
        "downloaded_bytes": 1024,
        "total_bytes": 2048,
        "speed": 256,
        "eta": 12,
        "filename": "file.mp3",
    })
    state = tc._download_progress[101]
    assert state["status"] == "downloading"
    assert state["percent"] == "45%"
    assert state["downloaded"] == 1024
    assert state["total"] == 2048


def test_create_progress_hook_stores_finished_and_error():
    hook = tc.create_progress_hook(202)
    hook({"status": "finished", "downloaded_bytes": 100, "total_bytes": 100, "filename": "a"})
    assert tc._download_progress[202]["status"] == "finished"
    assert tc._download_progress[202]["percent"] == "100%"

    hook({"status": "error", "error": "boom"})
    assert tc._download_progress[202]["status"] == "error"


def test_safe_edit_message_ignores_not_modified():
    query = Mock()
    query.edit_message_text = AsyncMock(side_effect=BadRequest("Message is not modified"))
    asyncio.run(tc.safe_edit_message(query, "text"))
    query.edit_message_text.assert_awaited_once()


def test_safe_edit_message_raises_other_bad_request():
    query = Mock()
    query.edit_message_text = AsyncMock(side_effect=BadRequest("Other error"))
    with pytest.raises(BadRequest):
        asyncio.run(tc.safe_edit_message(query, "text"))


def test_send_long_message_splits_large_text():
    bot = Mock()
    bot.send_message = AsyncMock()

    long_text = "A" * 10000
    header = "Header\n\n"
    asyncio.run(tc.send_long_message(bot, 123, long_text, header=header, parse_mode="Markdown"))

    assert bot.send_message.await_count >= 3
    first_call_text = bot.send_message.await_args_list[0].kwargs["text"]
    assert first_call_text.startswith(header)


def _make_update(data: str, chat_id: int = 123):
    update = Mock()
    update.effective_chat.id = chat_id
    query = Mock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    update.callback_query = query
    return update


def _make_context():
    context = Mock()
    context.user_data = {}
    context.bot = Mock()
    context.bot.send_document = AsyncMock()
    context.bot.send_message = AsyncMock()
    return context


def test_handle_callback_audio_transcribe_starts_directly(monkeypatch):
    """audio_transcribe callback invokes transcribe_audio_file directly (no language selection)."""
    tc.user_urls.pop(123, None)
    update = _make_update("audio_transcribe", chat_id=123)
    context = _make_context()

    called = {}

    async def fake_transcribe(update_arg, context_arg, **kwargs):
        called["invoked"] = True

    monkeypatch.setattr(tc, "transcribe_audio_file", fake_transcribe)
    asyncio.run(tc.handle_callback(update, context))

    update.callback_query.answer.assert_awaited_once()
    assert called.get("invoked") is True


def test_handle_callback_transcribe_starts_download(monkeypatch):
    """transcribe callback downloads audio with transcribe=True (auto-detect language)."""
    tc.user_urls[123] = "https://www.youtube.com/watch?v=abc"
    update = _make_update("transcribe", chat_id=123)
    context = _make_context()

    called = {}

    async def fake_download_file(update_arg, context_arg, type_arg, format_arg, url, **kwargs):
        called["type"] = type_arg
        called["format"] = format_arg
        called["url"] = url
        called["kwargs"] = kwargs

    monkeypatch.setattr(tc, "download_file", fake_download_file)
    asyncio.run(tc.handle_callback(update, context))

    update.callback_query.answer.assert_awaited_once()
    assert called["type"] == "audio"
    assert called["format"] == "mp3"
    assert called["url"] == "https://www.youtube.com/watch?v=abc"
    assert called["kwargs"]["transcribe"] is True


def test_handle_callback_transcribe_summary_shows_options(monkeypatch):
    """transcribe_summary callback shows summary options directly (no language selection)."""
    tc.user_urls[333] = "https://www.youtube.com/watch?v=abc"
    update = _make_update("transcribe_summary", chat_id=333)
    context = _make_context()

    shown = {}

    async def fake_show_summary_options(update_arg, context_arg, url):
        shown["url"] = url

    monkeypatch.setattr(tc, "show_summary_options", fake_show_summary_options)
    asyncio.run(tc.handle_callback(update, context))

    update.callback_query.answer.assert_awaited_once()
    assert shown["url"] == "https://www.youtube.com/watch?v=abc"


def test_handle_callback_audio_summary_option_invokes_transcription(monkeypatch):
    update = _make_update("audio_summary_option_2", chat_id=444)
    context = _make_context()

    called = {}

    async def fake_transcribe_audio_file(update_arg, context_arg, summary=False, summary_type=None):
        called["summary"] = summary
        called["summary_type"] = summary_type

    monkeypatch.setattr(tc, "transcribe_audio_file", fake_transcribe_audio_file)
    asyncio.run(tc.handle_callback(update, context))

    update.callback_query.answer.assert_awaited_once()
    assert called["summary"] is True
    assert called["summary_type"] == 2


def test_handle_callback_audio_summary_option_invalid_shows_warning(monkeypatch):
    update = _make_update("audio_summary_option_x", chat_id=555)
    context = _make_context()

    called = {}

    async def fake_transcribe_audio_file(update_arg, context_arg, summary=False, summary_type=None):
        called["called"] = True

    monkeypatch.setattr(tc, "transcribe_audio_file", fake_transcribe_audio_file)
    asyncio.run(tc.handle_callback(update, context))

    update.callback_query.edit_message_text.assert_awaited_once_with("Nieobsługiwana opcja podsumowania.")
    assert "called" not in called


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


def test_parse_summary_option():
    assert tc.parse_summary_option("summary_option_1") == 1
    assert tc.parse_summary_option("audio_summary_option_4") == 4
    assert tc.parse_summary_option("summary_option_0") is None
    assert tc.parse_summary_option("audio_summary_option_bad") is None
    assert tc.parse_summary_option("other_option_2") is None


def test_parse_download_callback_parses_known_payloads():
    assert tc.parse_download_callback("dl_video_720p") == {
        "media_type": "video",
        "mode": "format_id",
        "format": "720p",
    }
    assert tc.parse_download_callback("dl_audio_mp3") == {
        "media_type": "audio",
        "mode": "codec",
        "format": "mp3",
    }
    assert tc.parse_download_callback("dl_audio_format_140") == {
        "media_type": "audio",
        "mode": "format_id",
        "format": "140",
    }


def test_parse_download_callback_returns_none_for_unknown_payload():
    assert tc.parse_download_callback("formats") is None
    assert tc.parse_download_callback("dl_unknown_720p") is None
    assert tc.parse_download_callback("dl_audio_format") is None


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


def test_show_audio_summary_options_builds_menu():
    update = _make_update("audio_summary_options", chat_id=444)
    context = _make_context()
    context.user_data["audio_file_title"] = "Voice Note"

    asyncio.run(tc.show_audio_summary_options(update, context))

    text = update.callback_query.edit_message_text.await_args.args[0]
    buttons = [
        button.text
        for row in update.callback_query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard
        for button in row
    ]

    assert "*Voice Note*" in text
    assert "Wybierz rodzaj podsumowania" in text
    assert buttons == [
        "1. Krótkie podsumowanie",
        "2. Szczegółowe podsumowanie",
        "3. Podsumowanie w punktach",
        "4. Podział zadań na osoby",
    ]


def test_transcribe_audio_file_reports_missing_file():
    update = _make_update("audio_summary", chat_id=555)
    context = _make_context()
    context.user_data["audio_file_path"] = "/tmp/does-not-exist.mp3"

    asyncio.run(tc.transcribe_audio_file(update, context))

    update.callback_query.edit_message_text.assert_awaited_once_with(
        "Plik audio nie został znaleziony. Wyślij go ponownie."
    )


def test_transcribe_audio_file_requires_groq_api_key(tmp_path, monkeypatch):
    audio_file = tmp_path / "audio.mp3"
    audio_file.write_bytes(b"fake mp3 content")

    update = _make_update("audio_summary", chat_id=666)
    context = _make_context()
    context.user_data["audio_file_path"] = str(audio_file)
    context.user_data["audio_file_title"] = "Recording"

    monkeypatch.setitem(tc.CONFIG, "GROQ_API_KEY", "")
    asyncio.run(tc.transcribe_audio_file(update, context))

    messages = [call.args[0] for call in update.callback_query.edit_message_text.await_args_list]
    assert any("brak klucza api" in msg.lower() for msg in messages)


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
