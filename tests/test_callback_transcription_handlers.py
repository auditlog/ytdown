"""Transcription-, audio-, and Spotify-oriented tests for Telegram callbacks."""

import asyncio

from bot import telegram_callbacks as tc
from tests.telegram_callbacks_support import _attach_runtime, _make_context, _make_update


def test_handle_callback_audio_transcribe_starts_directly(monkeypatch):
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


def test_show_audio_summary_options_reads_title_from_runtime_session():
    update = _make_update("audio_transcribe_summary", chat_id=321)
    context = _make_context()
    runtime = _attach_runtime(context)
    runtime.session_store.set_field(321, "audio_file_title", "Runtime Recording")

    asyncio.run(tc.show_audio_summary_options(update, context))

    update.callback_query.edit_message_text.assert_awaited_once()
    assert "Runtime Recording" in update.callback_query.edit_message_text.await_args.args[0]


def test_handle_callback_transcribe_shows_subtitle_menu(monkeypatch):
    tc.user_urls[123] = "https://www.youtube.com/watch?v=abc"
    update = _make_update("transcribe", chat_id=123)
    context = _make_context()

    called = {}

    async def fake_show_subtitle_source_menu(update_arg, context_arg, url, with_summary=False):
        called["url"] = url
        called["with_summary"] = with_summary

    monkeypatch.setattr(tc, "show_subtitle_source_menu", fake_show_subtitle_source_menu)
    asyncio.run(tc.handle_callback(update, context))

    update.callback_query.answer.assert_awaited_once()
    assert called["url"] == "https://www.youtube.com/watch?v=abc"
    assert called["with_summary"] is False


def test_handle_callback_transcribe_summary_shows_subtitle_menu(monkeypatch):
    tc.user_urls[333] = "https://www.youtube.com/watch?v=abc"
    update = _make_update("transcribe_summary", chat_id=333)
    context = _make_context()

    called = {}

    async def fake_show_subtitle_source_menu(update_arg, context_arg, url, with_summary=False):
        called["url"] = url
        called["with_summary"] = with_summary

    monkeypatch.setattr(tc, "show_subtitle_source_menu", fake_show_subtitle_source_menu)
    asyncio.run(tc.handle_callback(update, context))

    update.callback_query.answer.assert_awaited_once()
    assert called["url"] == "https://www.youtube.com/watch?v=abc"
    assert called["with_summary"] is True


def test_handle_callback_sub_src_ai_starts_download(monkeypatch):
    tc.user_urls[123] = "https://www.youtube.com/watch?v=abc"
    update = _make_update("sub_src_ai", chat_id=123)
    context = _make_context()

    called = {}

    async def fake_download_file(update_arg, context_arg, type_arg, format_arg, url, **kwargs):
        called["type"] = type_arg
        called["url"] = url
        called["transcribe"] = kwargs.get("transcribe")

    monkeypatch.setattr(tc, "download_file", fake_download_file)
    asyncio.run(tc.handle_callback(update, context))

    assert called["type"] == "audio"
    assert called["url"] == "https://www.youtube.com/watch?v=abc"
    assert called["transcribe"] is True


def test_handle_callback_sub_src_ai_s_shows_summary_options(monkeypatch):
    tc.user_urls[333] = "https://www.youtube.com/watch?v=abc"
    update = _make_update("sub_src_ai_sum", chat_id=333)
    context = _make_context()

    shown = {}

    async def fake_show_summary_options(update_arg, context_arg, url):
        shown["url"] = url

    monkeypatch.setattr(tc, "show_summary_options", fake_show_summary_options)
    asyncio.run(tc.handle_callback(update, context))

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

    monkeypatch.setattr(tc, "get_runtime_value", lambda key, default=None: "" if key == "GROQ_API_KEY" else default)
    asyncio.run(tc.transcribe_audio_file(update, context))

    messages = [call.args[0] for call in update.callback_query.edit_message_text.await_args_list]
    assert any("brak klucza api" in msg.lower() for msg in messages)


def test_handle_callback_spotify_transcribe_calls_download_spotify_resolved(monkeypatch):
    tc.user_urls[123] = "https://open.spotify.com/episode/abc123"
    update = _make_update("transcribe", chat_id=123)
    context = _make_context()
    context.user_data["platform"] = "spotify"
    context.user_data["spotify_resolved"] = {
        "source": "itunes",
        "audio_url": "https://example.com/ep.mp3",
        "title": "Test Episode",
    }

    called = {}

    async def fake_download_spotify(update_arg, context_arg, resolved, fmt, transcribe=False, **kw):
        called["resolved"] = resolved
        called["transcribe"] = transcribe

    monkeypatch.setattr(tc, "download_spotify_resolved", fake_download_spotify)
    asyncio.run(tc.handle_callback(update, context))

    assert called["transcribe"] is True
    assert called["resolved"]["source"] == "itunes"


def test_handle_callback_spotify_transcribe_summary_shows_options(monkeypatch):
    tc.user_urls[123] = "https://open.spotify.com/episode/abc123"
    update = _make_update("transcribe_summary", chat_id=123)
    context = _make_context()
    context.user_data["platform"] = "spotify"
    context.user_data["spotify_resolved"] = {"title": "Test Episode"}

    called = {}

    async def fake_show_spotify_summary(update_arg, context_arg):
        called["invoked"] = True

    monkeypatch.setattr(tc, "_show_spotify_summary_options", fake_show_spotify_summary)
    asyncio.run(tc.handle_callback(update, context))

    assert called.get("invoked") is True


def test_handle_callback_spotify_summary_option_calls_download(monkeypatch):
    tc.user_urls[123] = "https://open.spotify.com/episode/abc123"
    update = _make_update("summary_option_2", chat_id=123)
    context = _make_context()
    context.user_data["platform"] = "spotify"
    context.user_data["spotify_resolved"] = {
        "source": "youtube",
        "youtube_url": "https://youtube.com/watch?v=xyz",
        "title": "Test Episode",
    }

    called = {}

    async def fake_download_spotify(update_arg, context_arg, resolved, fmt, transcribe=False, summary=False, summary_type=None):
        called["transcribe"] = transcribe
        called["summary"] = summary
        called["summary_type"] = summary_type

    monkeypatch.setattr(tc, "download_spotify_resolved", fake_download_spotify)
    asyncio.run(tc.handle_callback(update, context))

    assert called["transcribe"] is True
    assert called["summary"] is True
    assert called["summary_type"] == 2


def test_handle_callback_spotify_expired_session():
    tc.user_urls[123] = "https://open.spotify.com/episode/abc123"
    update = _make_update("transcribe", chat_id=123)
    context = _make_context()
    context.user_data["platform"] = "spotify"

    asyncio.run(tc.handle_callback(update, context))

    update.callback_query.edit_message_text.assert_awaited_with(
        "Sesja Spotify wygasła. Wyślij link ponownie."
    )
