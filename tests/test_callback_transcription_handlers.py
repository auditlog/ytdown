"""Transcription-, audio-, and Spotify-oriented tests for Telegram callbacks."""

import asyncio
import logging
import os
from unittest.mock import AsyncMock

from bot import telegram_callbacks as tc
from bot.downloader_validation import sanitize_filename
from bot.handlers import spotify_callbacks as sc
from bot.services.spotify_video_service import get_video_error_message
from bot.spotify_video import SpotifyVideoCancelled, SpotifyVideoError
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


def test_handle_callback_custom_prompt_does_not_require_active_url(monkeypatch):
    tc.user_urls.pop(123, None)
    update = _make_update("tr_prompt_token", chat_id=123)
    context = _make_context()
    called = {}

    async def fake_handle(update_arg, context_arg, data):
        called["data"] = data

    monkeypatch.setattr(tc, "handle_transcript_prompt_callback", fake_handle)

    asyncio.run(tc.handle_callback(update, context))

    assert called["data"] == "tr_prompt_token"
    update.callback_query.edit_message_text.assert_not_awaited()


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


# --- Spotify transcription routing (Task 12) --------------------------------
#
# transcribe / summary_option_* must prefer a native spotify_video session
# (subtitles or, failing that, native audio + Groq) over the legacy
# spotify_resolved fallback, and only fall through to the expired-session
# message when neither survived. Each of the four cases below asserts not
# just which handler ran, but that the others did NOT -- a routing bug that
# calls the wrong handler in addition to the right one would otherwise slip
# through unnoticed.


def test_handle_callback_transcribe_routes_to_video_session_over_resolved(monkeypatch):
    """A spotify_video session must win even when a stale spotify_resolved
    fallback is also still sitting in the session -- proves priority, not
    just presence."""

    tc.user_urls[123] = "https://open.spotify.com/episode/abc123"
    update = _make_update("transcribe", chat_id=123)
    context = _make_context()
    context.user_data["platform"] = "spotify"
    context.user_data["spotify_video"] = _spotify_video_session()
    context.user_data["spotify_resolved"] = {"source": "itunes", "title": "Stale"}

    called = {}

    async def fake_transcribe_video(update_arg, context_arg, session_data, *, summary, summary_type):
        called["session_data"] = session_data
        called["summary"] = summary
        called["summary_type"] = summary_type

    async def must_not_be_reached(*a, **kw):
        raise AssertionError("legacy download_spotify_resolved must not run when spotify_video is present")

    monkeypatch.setattr(tc, "transcribe_spotify_video", fake_transcribe_video)
    monkeypatch.setattr(tc, "download_spotify_resolved", must_not_be_reached)
    asyncio.run(tc.handle_callback(update, context))

    assert called["session_data"]["episode_id"] == "abc123"
    assert called["summary"] is False
    assert called["summary_type"] is None


def test_handle_callback_summary_option_routes_to_video_session(monkeypatch):
    tc.user_urls[123] = "https://open.spotify.com/episode/abc123"
    update = _make_update("summary_option_3", chat_id=123)
    context = _make_context()
    context.user_data["platform"] = "spotify"
    context.user_data["spotify_video"] = _spotify_video_session()

    called = {}

    async def fake_transcribe_video(update_arg, context_arg, session_data, *, summary, summary_type):
        called["summary"] = summary
        called["summary_type"] = summary_type

    async def must_not_be_reached(*a, **kw):
        raise AssertionError("legacy download_spotify_resolved must not run when spotify_video is present")

    monkeypatch.setattr(tc, "transcribe_spotify_video", fake_transcribe_video)
    monkeypatch.setattr(tc, "download_spotify_resolved", must_not_be_reached)
    asyncio.run(tc.handle_callback(update, context))

    assert called["summary"] is True
    assert called["summary_type"] == 3


def test_handle_callback_transcribe_falls_back_to_resolved_without_video_session(monkeypatch):
    """No spotify_video session at all -- must still use the legacy
    spotify_resolved path, and must not touch the new video handler."""

    tc.user_urls[123] = "https://open.spotify.com/episode/abc123"
    update = _make_update("transcribe", chat_id=123)
    context = _make_context()
    context.user_data["platform"] = "spotify"
    context.user_data["spotify_resolved"] = {"source": "itunes", "title": "Test Episode"}

    called = {}

    async def fake_download_spotify(update_arg, context_arg, resolved, fmt, transcribe=False, **kw):
        called["resolved"] = resolved
        called["transcribe"] = transcribe

    async def must_not_be_reached(*a, **kw):
        raise AssertionError("transcribe_spotify_video must not run without a spotify_video session")

    monkeypatch.setattr(tc, "download_spotify_resolved", fake_download_spotify)
    monkeypatch.setattr(tc, "transcribe_spotify_video", must_not_be_reached)
    asyncio.run(tc.handle_callback(update, context))

    assert called["transcribe"] is True
    assert called["resolved"]["source"] == "itunes"


def test_handle_callback_transcribe_expired_without_video_or_resolved(monkeypatch):
    tc.user_urls[123] = "https://open.spotify.com/episode/abc123"
    update = _make_update("transcribe", chat_id=123)
    context = _make_context()
    context.user_data["platform"] = "spotify"

    async def must_not_be_reached(*a, **kw):
        raise AssertionError("neither handler should run once both sessions are gone")

    monkeypatch.setattr(tc, "download_spotify_resolved", must_not_be_reached)
    monkeypatch.setattr(tc, "transcribe_spotify_video", must_not_be_reached)
    asyncio.run(tc.handle_callback(update, context))

    update.callback_query.edit_message_text.assert_awaited_with(
        "Sesja Spotify wygasła. Wyślij link ponownie."
    )


# --- spv_ routing (Task 11) ------------------------------------------------


def _spotify_video_session():
    return {
        "episode_id": "abc123",
        "title": "Test Episode",
        "show_name": "Test Show",
        "duration_ms": 32000,
        "manifest": {"contents": [{"profiles": []}]},
        "subtitle_languages": [],
    }


def test_handle_callback_spv_routes_to_download_spotify_video(monkeypatch):
    tc.user_urls[123] = "https://open.spotify.com/episode/abc123"
    update = _make_update("spv_video_720p", chat_id=123)
    context = _make_context()
    context.user_data["spotify_video"] = _spotify_video_session()

    called = {}

    async def fake_download_spotify_video(update_arg, context_arg, session_data, *, height):
        called["session_data"] = session_data
        called["height"] = height

    monkeypatch.setattr(tc, "download_spotify_video", fake_download_spotify_video)
    asyncio.run(tc.handle_callback(update, context))

    assert called["height"] == 720
    assert called["session_data"]["episode_id"] == "abc123"
    update.callback_query.edit_message_text.assert_not_awaited()


def test_handle_callback_spv_audio_routes_with_none_height(monkeypatch):
    tc.user_urls[123] = "https://open.spotify.com/episode/abc123"
    update = _make_update("spv_audio_m4a", chat_id=123)
    context = _make_context()
    context.user_data["spotify_video"] = _spotify_video_session()

    called = {}

    async def fake_download_spotify_video(update_arg, context_arg, session_data, *, height):
        called["height"] = height

    monkeypatch.setattr(tc, "download_spotify_video", fake_download_spotify_video)
    asyncio.run(tc.handle_callback(update, context))

    assert called["height"] is None


def test_handle_callback_spv_rejects_unknown_format(monkeypatch):
    tc.user_urls[123] = "https://open.spotify.com/episode/abc123"
    update = _make_update("spv_video_144p", chat_id=123)
    context = _make_context()
    context.user_data["spotify_video"] = _spotify_video_session()

    called = {}

    async def fake_download_spotify_video(*a, **kw):
        called["invoked"] = True

    monkeypatch.setattr(tc, "download_spotify_video", fake_download_spotify_video)
    asyncio.run(tc.handle_callback(update, context))

    update.callback_query.edit_message_text.assert_awaited_once_with(
        "Nieobsługiwany format. Spróbuj wybrać format ponownie."
    )
    assert "invoked" not in called


def test_handle_callback_spv_missing_session_shows_expired_message(monkeypatch):
    tc.user_urls[123] = "https://open.spotify.com/episode/abc123"
    update = _make_update("spv_audio_m4a", chat_id=123)
    context = _make_context()
    # No "spotify_video" key in context.user_data -- session expired/never set.

    called = {}

    async def fake_download_spotify_video(*a, **kw):
        called["invoked"] = True

    monkeypatch.setattr(tc, "download_spotify_video", fake_download_spotify_video)
    asyncio.run(tc.handle_callback(update, context))

    update.callback_query.edit_message_text.assert_awaited_once_with(
        "Sesja Spotify wygasła. Wyślij link ponownie."
    )
    assert "invoked" not in called


# --- download_spotify_video (Task 11) --------------------------------------


def _big_file(path, size_bytes):
    """Create a sparse file of the given size without writing real bytes to disk."""
    path.write_bytes(b"")
    os.truncate(str(path), size_bytes)


def test_download_spotify_video_sends_video_via_bot_api(monkeypatch, tmp_path):
    produced = tmp_path / "episode.mp4"
    produced.write_bytes(b"X" * 2048)

    async def fake_download(**kwargs):
        assert kwargs["height"] == 720
        return str(produced)

    recorded = {}

    def fake_record(context_arg, chat_id, title, url, fmt, size_mb):
        recorded["format"] = fmt
        recorded["title"] = title

    monkeypatch.setattr(sc, "download_episode_media", fake_download)
    monkeypatch.setattr(sc, "record_download_for", fake_record)

    update = _make_update("spv_video_720p", chat_id=123)
    context = _make_context()

    asyncio.run(sc.download_spotify_video(update, context, _spotify_video_session(), height=720))

    context.bot.send_video.assert_awaited_once()
    assert context.bot.send_video.await_args.kwargs["caption"] == "Test Episode"
    context.bot.send_audio.assert_not_awaited()
    assert recorded["format"] == "spotify_native_720p"
    final_text = update.callback_query.edit_message_text.await_args_list[-1].args[0]
    assert final_text == "Gotowe: Test Episode"
    assert not produced.exists()


def test_download_spotify_video_sends_audio_via_bot_api(monkeypatch, tmp_path):
    produced = tmp_path / "episode.m4a"
    produced.write_bytes(b"X" * 1024)

    async def fake_download(**kwargs):
        assert kwargs["height"] is None
        return str(produced)

    monkeypatch.setattr(sc, "download_episode_media", fake_download)
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)

    update = _make_update("spv_audio_m4a", chat_id=123)
    context = _make_context()

    asyncio.run(sc.download_spotify_video(update, context, _spotify_video_session(), height=None))

    context.bot.send_audio.assert_awaited_once()
    kwargs = context.bot.send_audio.await_args.kwargs
    assert kwargs["title"] == "Test Episode"
    assert kwargs["caption"] == "Test Episode"
    context.bot.send_video.assert_not_awaited()


def test_download_spotify_video_reports_drm_refusal(monkeypatch):
    async def fake_download(**kwargs):
        raise SpotifyVideoError("drm_protected")

    monkeypatch.setattr(sc, "download_episode_media", fake_download)
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)

    update = _make_update("spv_video_720p", chat_id=123)
    context = _make_context()

    asyncio.run(sc.download_spotify_video(update, context, _spotify_video_session(), height=720))

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "DRM" in text
    context.bot.send_video.assert_not_awaited()


def test_download_spotify_video_reports_cancellation(monkeypatch):
    async def fake_download(**kwargs):
        raise SpotifyVideoCancelled("stopped")

    monkeypatch.setattr(sc, "download_episode_media", fake_download)
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)

    update = _make_update("spv_video_720p", chat_id=123)
    context = _make_context()

    asyncio.run(sc.download_spotify_video(update, context, _spotify_video_session(), height=720))

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert text == "Pobieranie anulowane."
    context.bot.send_video.assert_not_awaited()


def test_download_spotify_video_reports_and_logs_segment_download_failure(monkeypatch, caplog):
    """A failed segment fetch is the most common runtime failure of this
    pipeline, and it raises a descriptive English sentence rather than one
    of the mapped reason codes. The branch handling it logged nothing and
    fell through to the catch-all "Nie udalo sie przygotowac wideo z tego
    odcinka Spotify.", so the user learned nothing about what failed and the
    operator learned nothing at all -- both halves of what design spec 10
    rules out."""

    segment_error = (
        "Segment download failed after 3 attempts; last error from "
        "https://video-fa.scdn.co/segments/v1/0.mp4?[redacted]: "
        "HTTPSConnectionPool(host='video-fa.scdn.co', port=443): Read timed out."
    )

    async def fake_download(**kwargs):
        raise SpotifyVideoError(segment_error)

    monkeypatch.setattr(sc, "download_episode_media", fake_download)
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)

    update = _make_update("spv_video_720p", chat_id=123)
    context = _make_context()

    with caplog.at_level(logging.ERROR):
        asyncio.run(sc.download_spotify_video(update, context, _spotify_video_session(), height=720))

    text = update.callback_query.edit_message_text.await_args.args[0]
    # The user is told the download itself failed, not handed the catch-all.
    assert text == get_video_error_message("download_failed")
    assert text != get_video_error_message("__unmapped__")
    # The operator gets the real cause.
    assert "Segment download failed" in caplog.text
    assert "video-fa.scdn.co" in caplog.text
    context.bot.send_video.assert_not_awaited()


def test_download_spotify_video_keeps_mapped_reason_codes_distinct(monkeypatch, caplog):
    """The download_failed fallback must not swallow the reason codes that
    already say something more specific -- ffmpeg_missing is raised from the
    same try block."""

    async def fake_download(**kwargs):
        raise SpotifyVideoError("ffmpeg_missing")

    monkeypatch.setattr(sc, "download_episode_media", fake_download)
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)

    update = _make_update("spv_video_720p", chat_id=123)
    context = _make_context()

    with caplog.at_level(logging.ERROR):
        asyncio.run(sc.download_spotify_video(update, context, _spotify_video_session(), height=720))

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert text == get_video_error_message("ffmpeg_missing")


def test_download_spotify_video_reports_generic_error(monkeypatch):
    async def fake_download(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(sc, "download_episode_media", fake_download)
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)

    update = _make_update("spv_video_720p", chat_id=123)
    context = _make_context()

    asyncio.run(sc.download_spotify_video(update, context, _spotify_video_session(), height=720))

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert text == "Błąd pobierania: boom"
    context.bot.send_video.assert_not_awaited()


def test_download_spotify_video_uses_mtproto_when_over_bot_api_limit(monkeypatch, tmp_path):
    produced = tmp_path / "episode.mp4"
    _big_file(produced, 51 * 1024 * 1024)

    async def fake_download(**kwargs):
        return str(produced)

    mtproto_calls = {}

    async def fake_send_video_mtproto(chat_id, file_path, caption=None, thumb_path=None, *, cancellation=None):
        mtproto_calls["chat_id"] = chat_id
        mtproto_calls["caption"] = caption
        return True

    monkeypatch.setattr(sc, "download_episode_media", fake_download)
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)
    monkeypatch.setattr(sc, "_mtproto_unavailability_reason", lambda: None)
    monkeypatch.setattr(sc, "send_video_mtproto", fake_send_video_mtproto)

    update = _make_update("spv_video_720p", chat_id=123)
    context = _make_context()

    asyncio.run(sc.download_spotify_video(update, context, _spotify_video_session(), height=720))

    assert mtproto_calls["chat_id"] == 123
    assert mtproto_calls["caption"] == "Test Episode"
    context.bot.send_video.assert_not_awaited()
    final_text = update.callback_query.edit_message_text.await_args_list[-1].args[0]
    assert final_text == "Gotowe: Test Episode"


def test_download_spotify_video_audio_mtproto_passes_title(monkeypatch, tmp_path):
    produced = tmp_path / "episode.m4a"
    _big_file(produced, 51 * 1024 * 1024)

    async def fake_download(**kwargs):
        return str(produced)

    captured = {}

    async def fake_send_audio_mtproto(chat_id, file_path, title=None, caption=None, thumb_path=None, *, cancellation=None):
        captured["title"] = title
        captured["caption"] = caption
        return True

    monkeypatch.setattr(sc, "download_episode_media", fake_download)
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)
    monkeypatch.setattr(sc, "_mtproto_unavailability_reason", lambda: None)
    monkeypatch.setattr(sc, "send_audio_mtproto", fake_send_audio_mtproto)

    update = _make_update("spv_audio_m4a", chat_id=123)
    context = _make_context()

    asyncio.run(sc.download_spotify_video(update, context, _spotify_video_session(), height=None))

    assert captured["title"] == "Test Episode"
    assert captured["caption"] == "Test Episode"
    context.bot.send_audio.assert_not_awaited()


def test_download_spotify_video_reports_mtproto_unavailable(monkeypatch, tmp_path):
    produced = tmp_path / "episode.mp4"
    _big_file(produced, 51 * 1024 * 1024)

    async def fake_download(**kwargs):
        return str(produced)

    monkeypatch.setattr(sc, "download_episode_media", fake_download)
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)
    monkeypatch.setattr(sc, "_mtproto_unavailability_reason", lambda: "Brak pyrogram.")

    update = _make_update("spv_video_720p", chat_id=123)
    context = _make_context()

    asyncio.run(sc.download_spotify_video(update, context, _spotify_video_session(), height=720))

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "Plik za duży dla Bot API" in text
    assert "Brak pyrogram." in text
    context.bot.send_video.assert_not_awaited()


def test_download_spotify_video_reports_mtproto_send_failure(monkeypatch, tmp_path):
    produced = tmp_path / "episode.mp4"
    _big_file(produced, 51 * 1024 * 1024)

    async def fake_download(**kwargs):
        return str(produced)

    async def fake_send_video_mtproto(*a, **kw):
        return False

    monkeypatch.setattr(sc, "download_episode_media", fake_download)
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)
    monkeypatch.setattr(sc, "_mtproto_unavailability_reason", lambda: None)
    monkeypatch.setattr(sc, "send_video_mtproto", fake_send_video_mtproto)

    update = _make_update("spv_video_720p", chat_id=123)
    context = _make_context()

    asyncio.run(sc.download_spotify_video(update, context, _spotify_video_session(), height=720))

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert text == "Wysyłanie pliku przez MTProto nie powiodło się."


# --- transcribe_spotify_video (Task 12) -------------------------------------


def test_transcribe_spotify_video_uses_subtitles_without_downloading_audio(monkeypatch, tmp_path):
    """When the episode ships subtitles, no audio may be downloaded and Groq
    must never run -- the whole point of this path is skipping both."""

    transcript_file = tmp_path / "transcript.md"
    transcript_file.write_text("# Test Episode\n\nPierwsza linia.\n", encoding="utf-8")

    def fake_transcript_from_subtitles(*, episode, output_dir, sanitized_title):
        return str(transcript_file)

    async def must_not_download(*a, **kw):
        raise AssertionError("download_episode_media must not run when subtitles are used")

    async def must_not_transcribe(*a, **kw):
        raise AssertionError("run_transcription_with_progress (Groq) must not run when subtitles are used")

    monkeypatch.setattr(sc, "transcript_from_subtitles", fake_transcript_from_subtitles)
    monkeypatch.setattr(sc, "download_episode_media", must_not_download)
    monkeypatch.setattr(sc, "run_transcription_with_progress", must_not_transcribe)
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)

    session = _spotify_video_session()
    session["subtitle_languages"] = ["pl-pl"]

    update = _make_update("transcribe", chat_id=123)
    context = _make_context()

    asyncio.run(sc.transcribe_spotify_video(update, context, session))

    context.bot.send_document.assert_awaited_once()
    sent_kwargs = context.bot.send_document.await_args.kwargs
    assert sent_kwargs["filename"] == "transcript.md"
    final_text = update.callback_query.edit_message_text.await_args_list[-1].args[0]
    assert final_text == "Gotowe: Test Episode"


def test_transcribe_spotify_video_falls_back_to_audio_when_no_subtitles(monkeypatch, tmp_path):
    """No subtitle languages at all -- transcript_from_subtitles must not
    even be attempted, and the native audio + Groq pipeline must run."""

    audio_file = tmp_path / "episode.m4a"
    audio_file.write_bytes(b"fake-audio-bytes")
    transcript_file = tmp_path / "episode_transcript.md"
    transcript_file.write_text("# Test Episode\n\nZ Groq.\n", encoding="utf-8")

    def must_not_use_subtitles(*a, **kw):
        # transcript_from_subtitles is a plain sync function, dispatched
        # through run_in_executor -- an async stub here would return an
        # unawaited coroutine instead of raising, letting a mutated guard
        # slip through with a confusing downstream TypeError instead of
        # this assertion.
        raise AssertionError("transcript_from_subtitles must not run without subtitle_languages")

    async def fake_download_episode_media(*, episode, height, output_dir, executor, **kw):
        assert height is None
        return str(audio_file)

    calls = {}

    async def fake_run_transcription(*, source_path, output_dir, executor, status_callback):
        calls["source_path"] = source_path
        return str(transcript_file)

    monkeypatch.setattr(sc, "transcript_from_subtitles", must_not_use_subtitles)
    monkeypatch.setattr(sc, "download_episode_media", fake_download_episode_media)
    monkeypatch.setattr(sc, "run_transcription_with_progress", fake_run_transcription)
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)
    monkeypatch.setattr(sc, "get_runtime_value", lambda key, default=None: "fake-groq-key" if key == "GROQ_API_KEY" else default)

    session = _spotify_video_session()
    session["subtitle_languages"] = []

    update = _make_update("transcribe", chat_id=123)
    context = _make_context()

    asyncio.run(sc.transcribe_spotify_video(update, context, session))

    assert calls["source_path"] == str(audio_file)
    context.bot.send_document.assert_awaited_once()
    assert not audio_file.exists(), "downloaded audio must be cleaned up after delivery"


def test_transcribe_spotify_video_falls_back_when_subtitle_fetch_fails(monkeypatch, tmp_path):
    """subtitle_languages claims a track exists, but fetching it fails
    (returns None, per its documented contract) -- must fall back to the
    audio + Groq pipeline rather than erroring out."""

    audio_file = tmp_path / "episode.m4a"
    audio_file.write_bytes(b"fake-audio-bytes")
    transcript_file = tmp_path / "episode_transcript.md"
    transcript_file.write_text("# Test Episode\n\nZ Groq.\n", encoding="utf-8")

    download_called = {}

    def fake_transcript_from_subtitles(*, episode, output_dir, sanitized_title):
        return None

    async def fake_download_episode_media(*, episode, height, output_dir, executor, **kw):
        download_called["invoked"] = True
        assert height is None
        return str(audio_file)

    async def fake_run_transcription(*, source_path, output_dir, executor, status_callback):
        return str(transcript_file)

    monkeypatch.setattr(sc, "transcript_from_subtitles", fake_transcript_from_subtitles)
    monkeypatch.setattr(sc, "download_episode_media", fake_download_episode_media)
    monkeypatch.setattr(sc, "run_transcription_with_progress", fake_run_transcription)
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)
    monkeypatch.setattr(sc, "get_runtime_value", lambda key, default=None: "fake-groq-key" if key == "GROQ_API_KEY" else default)

    session = _spotify_video_session()
    session["subtitle_languages"] = ["pl-pl"]

    update = _make_update("transcribe", chat_id=123)
    context = _make_context()

    asyncio.run(sc.transcribe_spotify_video(update, context, session))

    assert download_called.get("invoked") is True
    context.bot.send_document.assert_awaited_once()


def test_transcribe_spotify_video_reports_missing_groq_key_without_downloading(monkeypatch):
    async def must_not_download(*a, **kw):
        raise AssertionError("must not download audio before confirming a Groq key is configured")

    monkeypatch.setattr(sc, "transcript_from_subtitles", lambda **kw: None)
    monkeypatch.setattr(sc, "download_episode_media", must_not_download)
    monkeypatch.setattr(sc, "get_runtime_value", lambda key, default=None: "" if key == "GROQ_API_KEY" else default)

    session = _spotify_video_session()
    session["subtitle_languages"] = []

    update = _make_update("transcribe", chat_id=123)
    context = _make_context()

    asyncio.run(sc.transcribe_spotify_video(update, context, session))

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "brak klucza api" in text.lower()
    context.bot.send_document.assert_not_awaited()


def test_transcribe_spotify_video_clears_video_session_not_resolved(monkeypatch, tmp_path):
    """Success on the video path must clear spotify_video (this session's
    own field) and must leave spotify_resolved -- a stale, unrelated
    fallback that was never touched -- alone."""

    transcript_file = tmp_path / "transcript.md"
    transcript_file.write_text("# Test Episode\n\nTekst.\n", encoding="utf-8")

    monkeypatch.setattr(sc, "transcript_from_subtitles", lambda **kw: str(transcript_file))
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)

    session = _spotify_video_session()
    session["subtitle_languages"] = ["pl-pl"]

    update = _make_update("transcribe", chat_id=123)
    context = _make_context()
    context.user_data["spotify_video"] = session
    context.user_data["spotify_resolved"] = {"source": "itunes", "title": "Untouched"}

    asyncio.run(sc.transcribe_spotify_video(update, context, session))

    assert "spotify_video" not in context.user_data
    assert context.user_data["spotify_resolved"]["source"] == "itunes"


def test_transcribe_spotify_video_reports_api_changed_for_drifted_manifest(monkeypatch):
    """Session state can carry a manifest that no longer parses -- a stale
    manifest surviving from an earlier episode (this is exactly why
    inbound_media.py clears spotify_video on link transitions/failed
    resolution) or genuine Spotify API drift. Resolving it must surface
    the same Polish "api_changed" message every other manifest failure
    produces, not an unhandled exception that leaves the status message
    frozen forever -- download_spotify_video already guards this identical
    construction the same way, in the function immediately above it."""

    # Without this stub the test reaches download_episode_media only when a
    # real GROQ_API_KEY happens to be configured, so it passed locally off a
    # live api_key.md and would have gone red on a fresh checkout -- silently
    # ceasing to exercise api_changed rather than failing for a real reason.
    monkeypatch.setattr(
        sc, "get_runtime_value",
        lambda key, default=None: "fake-groq-key" if key == "GROQ_API_KEY" else default,
    )

    session = _spotify_video_session()
    session["manifest"] = {}  # no "contents" key -- list_profiles/find_audio_profile_id raise here
    session["subtitle_languages"] = []

    update = _make_update("transcribe", chat_id=123)
    context = _make_context()

    asyncio.run(sc.transcribe_spotify_video(update, context, session))

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "zmieniło swoje API" in text


def test_transcribe_spotify_video_sanitizes_empty_title_like_download_episode_media(monkeypatch, tmp_path):
    """title == "" is reachable (Spotify's embed entity can carry an empty
    title, per bot/spotify_video.py). transcribe_spotify_video's own
    sanitized_title must be computed with the exact same formula
    download_episode_media uses for its base_name
    (sanitize_filename(title or "spotify_episode")) -- otherwise they
    diverge ("download" vs "spotify_episode") and
    cleanup_transcription_artifacts's transcript_prefix stops matching the
    real per-part chunk files, leaking them until the 24h sweep."""

    expected_base = sanitize_filename("" or "spotify_episode")
    assert expected_base == "spotify_episode"

    audio_file = tmp_path / f"{expected_base}.m4a"
    audio_file.write_bytes(b"fake-audio-bytes")
    transcript_file = tmp_path / f"{expected_base}_transcript.md"
    transcript_file.write_text("# X\n\nTekst.\n", encoding="utf-8")

    async def fake_download_episode_media(*, episode, height, output_dir, executor, **kw):
        # Mirrors the real formula in bot/services/spotify_video_service.py.
        assert sanitize_filename(episode.title or "spotify_episode") == expected_base
        return str(audio_file)

    async def fake_run_transcription(*, source_path, output_dir, executor, status_callback):
        return str(transcript_file)

    captured_cleanup = {}

    def fake_cleanup(*, source_media_path, output_dir, transcript_prefix):
        captured_cleanup["transcript_prefix"] = transcript_prefix

    monkeypatch.setattr(sc, "transcript_from_subtitles", lambda **kw: None)
    monkeypatch.setattr(sc, "download_episode_media", fake_download_episode_media)
    monkeypatch.setattr(sc, "run_transcription_with_progress", fake_run_transcription)
    monkeypatch.setattr(sc, "cleanup_transcription_artifacts", fake_cleanup)
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)
    monkeypatch.setattr(sc, "get_runtime_value", lambda key, default=None: "fake-groq-key" if key == "GROQ_API_KEY" else default)

    session = _spotify_video_session()
    session["title"] = ""
    session["subtitle_languages"] = []

    update = _make_update("transcribe", chat_id=123)
    context = _make_context()

    asyncio.run(sc.transcribe_spotify_video(update, context, session))

    assert captured_cleanup["transcript_prefix"] == expected_base


def test_transcribe_spotify_video_reports_progress_during_audio_fallback(monkeypatch, tmp_path):
    """The native-audio fallback (no subtitles) must report download
    progress the same way download_spotify_video does -- Task 11 spent
    three fix rounds eliminating exactly this frozen-message defect, and
    the reporter/drain machinery it built sits three definitions above
    this call site, built for precisely this."""

    audio_file = tmp_path / "episode.m4a"
    audio_file.write_bytes(b"fake-audio-bytes")
    transcript_file = tmp_path / "episode_transcript.md"
    transcript_file.write_text("# Test Episode\n\nZ Groq.\n", encoding="utf-8")

    async def fake_download_episode_media(*, episode, height, output_dir, executor, progress_cb=None, **kw):
        loop = asyncio.get_event_loop()

        def worker():
            if progress_cb is not None:
                progress_cb(50, 100)
                progress_cb(100, 100)

        await loop.run_in_executor(None, worker)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return str(audio_file)

    async def fake_run_transcription(*, source_path, output_dir, executor, status_callback):
        return str(transcript_file)

    monkeypatch.setattr(sc, "transcript_from_subtitles", lambda **kw: None)
    monkeypatch.setattr(sc, "download_episode_media", fake_download_episode_media)
    monkeypatch.setattr(sc, "run_transcription_with_progress", fake_run_transcription)
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)
    monkeypatch.setattr(sc, "get_runtime_value", lambda key, default=None: "fake-groq-key" if key == "GROQ_API_KEY" else default)
    monkeypatch.setattr(sc, "_PROGRESS_EDIT_MIN_INTERVAL_SEC", 0)

    session = _spotify_video_session()
    session["subtitle_languages"] = []

    update = _make_update("transcribe", chat_id=123)
    context = _make_context()

    asyncio.run(sc.transcribe_spotify_video(update, context, session))

    edited_texts = [call.args[0] for call in update.callback_query.edit_message_text.await_args_list]
    assert "Pobieranie audio: 50/100" in edited_texts
    assert "Pobieranie audio: 100/100" in edited_texts


def test_transcribe_spotify_video_waits_for_pending_progress_edit_before_next_message(
    monkeypatch, tmp_path
):
    """Same ordering guarantee Task 11 established for download_spotify_video
    (see its identically-named test) must hold here too: a progress edit
    still in flight when download_episode_media returns must finish before
    the handler's own next status edit begins -- two concurrent
    edit_message_text calls on the same message have no ordering
    guarantee between their underlying HTTP requests."""

    audio_file = tmp_path / "episode.m4a"
    audio_file.write_bytes(b"fake-audio-bytes")
    transcript_file = tmp_path / "episode_transcript.md"
    transcript_file.write_text("# Test Episode\n\nZ Groq.\n", encoding="utf-8")

    log = []

    async def fake_edit_message_text(text, reply_markup=None, parse_mode=None):
        log.append(f"start:{text}")
        if text.startswith("Pobieranie audio:"):
            # Simulate a slow Telegram round-trip for the progress edit
            # specifically, so it is still in flight when the handler's
            # own post-download code becomes ready to send its next edit.
            for _ in range(50):
                await asyncio.sleep(0)
        log.append(f"finish:{text}")

    async def fake_download_episode_media(*, episode, height, output_dir, executor, progress_cb=None, **kw):
        loop = asyncio.get_event_loop()

        def worker():
            if progress_cb is not None:
                progress_cb(100, 100)

        await loop.run_in_executor(None, worker)
        return str(audio_file)

    async def fake_run_transcription(*, source_path, output_dir, executor, status_callback):
        return str(transcript_file)

    monkeypatch.setattr(sc, "transcript_from_subtitles", lambda **kw: None)
    monkeypatch.setattr(sc, "download_episode_media", fake_download_episode_media)
    monkeypatch.setattr(sc, "run_transcription_with_progress", fake_run_transcription)
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)
    monkeypatch.setattr(sc, "get_runtime_value", lambda key, default=None: "fake-groq-key" if key == "GROQ_API_KEY" else default)
    monkeypatch.setattr(sc, "_PROGRESS_EDIT_MIN_INTERVAL_SEC", 0)

    session = _spotify_video_session()
    session["subtitle_languages"] = []

    update = _make_update("transcribe", chat_id=123)
    context = _make_context()
    update.callback_query.edit_message_text = AsyncMock(side_effect=fake_edit_message_text)

    asyncio.run(sc.transcribe_spotify_video(update, context, session))

    assert "finish:Pobieranie audio: 100/100" in log
    finish_progress_idx = log.index("finish:Pobieranie audio: 100/100")
    next_message_start_idx = next(
        i for i, entry in enumerate(log) if entry.startswith("start:Pobieranie zakończone")
    )
    assert finish_progress_idx < next_message_start_idx


# --- progress throttling (Task 11) -----------------------------------------


def test_throttled_progress_reporter_formats_first_update():
    reporter = sc._ThrottledProgressReporter("wideo", min_interval=3.0, time_source=lambda: 100.0)
    assert reporter.record_and_check(412, 602) == "Pobieranie wideo: 412/602"


def test_throttled_progress_reporter_skips_unchanged_text():
    times = iter([100.0, 100.1])
    reporter = sc._ThrottledProgressReporter("wideo", min_interval=3.0, time_source=lambda: next(times))
    assert reporter.record_and_check(10, 100) == "Pobieranie wideo: 10/100"
    assert reporter.record_and_check(10, 100) is None


def test_throttled_progress_reporter_throttles_within_interval():
    times = iter([0.0, 1.0, 2.9])
    reporter = sc._ThrottledProgressReporter("wideo", min_interval=3.0, time_source=lambda: next(times))
    assert reporter.record_and_check(10, 100) == "Pobieranie wideo: 10/100"
    assert reporter.record_and_check(20, 100) is None
    assert reporter.record_and_check(30, 100) is None


def test_throttled_progress_reporter_allows_update_after_interval_elapses():
    times = iter([0.0, 3.5])
    reporter = sc._ThrottledProgressReporter("wideo", min_interval=3.0, time_source=lambda: next(times))
    assert reporter.record_and_check(10, 100) == "Pobieranie wideo: 10/100"
    assert reporter.record_and_check(90, 100) == "Pobieranie wideo: 90/100"


def test_throttled_progress_reporter_ignores_zero_total():
    reporter = sc._ThrottledProgressReporter("audio", min_interval=3.0, time_source=lambda: 0.0)
    assert reporter.record_and_check(0, 0) is None


def test_download_spotify_video_reports_progress_from_worker_thread(monkeypatch, tmp_path):
    """progress_cb runs on a worker thread; verify the run_coroutine_threadsafe
    bridge actually delivers throttled progress text to the Telegram message,
    without relying on any real wall-clock sleep."""

    produced = tmp_path / "episode.mp4"
    produced.write_bytes(b"X" * 1024)

    async def fake_download(*, progress_cb, **kwargs):
        loop = asyncio.get_event_loop()

        def worker():
            progress_cb(50, 100)
            progress_cb(100, 100)

        await loop.run_in_executor(None, worker)
        # Give the loop a couple of ticks to run the coroutines scheduled
        # from the worker thread via run_coroutine_threadsafe. asyncio.sleep(0)
        # is a bare yield, not a real delay.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return str(produced)

    monkeypatch.setattr(sc, "download_episode_media", fake_download)
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)
    monkeypatch.setattr(sc, "_PROGRESS_EDIT_MIN_INTERVAL_SEC", 0)

    update = _make_update("spv_video_720p", chat_id=123)
    context = _make_context()

    asyncio.run(sc.download_spotify_video(update, context, _spotify_video_session(), height=720))

    edited_texts = [call.args[0] for call in update.callback_query.edit_message_text.await_args_list]
    assert "Pobieranie wideo: 50/100" in edited_texts
    assert "Pobieranie wideo: 100/100" in edited_texts


# --- fix round 1 (Task 11 review) ------------------------------------------


def test_download_spotify_video_progress_edit_failure_does_not_abort_download(monkeypatch, tmp_path):
    """A progress edit that raises (e.g. RetryAfter/Forbidden, which
    safe_edit_message does not swallow) must be caught inside the scheduled
    coroutine itself. Un-caught, the exception is stored on the
    fire-and-forget future/task and nobody ever retrieves it -- which does
    not fail this download, but does leak an "exception was never
    retrieved" warning at GC time with no correlation to the download that
    caused it. Checking the captured future's .exception() directly is
    the only way to observe that without depending on GC/logging timing."""

    produced = tmp_path / "episode.mp4"
    produced.write_bytes(b"X" * 1024)

    captured_futures = []
    real_run_coroutine_threadsafe = asyncio.run_coroutine_threadsafe

    def spying_run_coroutine_threadsafe(coro, loop):
        future = real_run_coroutine_threadsafe(coro, loop)
        captured_futures.append(future)
        return future

    async def fake_edit_message_text(text, reply_markup=None, parse_mode=None):
        if text.startswith("Pobieranie wideo: ") and "/" in text:
            raise RuntimeError("flood control")

    async def fake_download(*, progress_cb, **kwargs):
        loop = asyncio.get_event_loop()

        def worker():
            progress_cb(50, 100)

        await loop.run_in_executor(None, worker)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return str(produced)

    monkeypatch.setattr(sc, "download_episode_media", fake_download)
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)
    monkeypatch.setattr(sc, "_PROGRESS_EDIT_MIN_INTERVAL_SEC", 0)
    monkeypatch.setattr(sc.asyncio, "run_coroutine_threadsafe", spying_run_coroutine_threadsafe)

    update = _make_update("spv_video_720p", chat_id=123)
    context = _make_context()
    update.callback_query.edit_message_text = AsyncMock(side_effect=fake_edit_message_text)

    # Must not raise -- the flood-control-style failure must be contained.
    asyncio.run(sc.download_spotify_video(update, context, _spotify_video_session(), height=720))

    context.bot.send_video.assert_awaited_once()
    final_text = update.callback_query.edit_message_text.await_args_list[-1].args[0]
    assert final_text == "Gotowe: Test Episode"
    assert len(captured_futures) == 1
    # Check .done() before .exception(): with no timeout, .exception()
    # blocks until the future resolves, so if the wrap_future-based wait
    # this fix depends on ever regresses (the future never completes),
    # this test would hang forever instead of failing loudly.
    assert captured_futures[0].done()
    # The scheduled progress-edit coroutine must have caught its own
    # exception -- nothing should be left for asyncio's "Task exception
    # was never retrieved" handler to complain about.
    assert captured_futures[0].exception() is None


def test_download_spotify_video_progress_bridge_error_does_not_abort_download(monkeypatch, tmp_path):
    """A synchronous failure inside progress_cb's own body (e.g. the
    reporter or the scheduling call itself raising) must be contained
    there -- otherwise it propagates into download_track's caller and, in
    the real pipeline, triggers the .part-file cleanup that discards a
    partially downloaded episode."""

    produced = tmp_path / "episode.mp4"
    produced.write_bytes(b"X" * 1024)

    async def fake_download(*, progress_cb, **kwargs):
        loop = asyncio.get_event_loop()

        def worker():
            progress_cb(50, 100)

        await loop.run_in_executor(None, worker)
        await asyncio.sleep(0)
        return str(produced)

    def broken_record_and_check(self, done, total):
        raise RuntimeError("reporter exploded")

    monkeypatch.setattr(sc, "download_episode_media", fake_download)
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)
    monkeypatch.setattr(sc._ThrottledProgressReporter, "record_and_check", broken_record_and_check)

    update = _make_update("spv_video_720p", chat_id=123)
    context = _make_context()

    # Must not raise -- the download must still complete and be sent.
    asyncio.run(sc.download_spotify_video(update, context, _spotify_video_session(), height=720))

    context.bot.send_video.assert_awaited_once()
    final_text = update.callback_query.edit_message_text.await_args_list[-1].args[0]
    assert final_text == "Gotowe: Test Episode"


def test_download_spotify_video_waits_for_pending_progress_edit_before_next_message(
    monkeypatch, tmp_path
):
    """After download_episode_media returns, a progress edit still in
    flight must be allowed to finish before the handler's own next status
    edit begins -- otherwise the two edit_message_text calls run
    concurrently with no guarantee which one Telegram applies last (two
    independent HTTP requests, ordered only by whichever server-side
    round-trip happens to finish first).

    Every other progress test in this file uses a plain AsyncMock that
    resolves the progress edit instantly, which can look correctly
    ordered purely by asyncio scheduling coincidence -- as this exact
    test did in an earlier, weaker form, passing against the flag-gate
    code that provides no ordering guarantee at all. Making the progress
    edit's underlying call take many genuine event-loop ticks (simulating
    a slow Telegram round-trip) is what actually distinguishes "ordering
    is enforced" from "ordering happened not to break yet": with the
    flag-gate mechanism, the handler's next message starts while the
    slow progress edit is still suspended mid-flight; with a mechanism
    that genuinely waits for the pending edit, it cannot.
    """

    produced = tmp_path / "episode.mp4"
    produced.write_bytes(b"X" * 1024)

    log = []

    async def fake_edit_message_text(text, reply_markup=None, parse_mode=None):
        log.append(f"start:{text}")
        if text.startswith("Pobieranie wideo:"):
            # Simulate a slow Telegram round-trip for the progress edit
            # specifically, so it is still in flight when the handler's
            # own post-download code becomes ready to send its next edit.
            for _ in range(50):
                await asyncio.sleep(0)
        log.append(f"finish:{text}")

    async def fake_download(*, progress_cb, **kwargs):
        loop = asyncio.get_event_loop()

        def worker():
            # Last progress report, then the executor call returns --
            # exactly download_track's real shape: progress_cb fires from
            # a worker thread with nothing else happening between the
            # last call and download_episode_media's return.
            progress_cb(100, 100)

        await loop.run_in_executor(None, worker)
        return str(produced)

    monkeypatch.setattr(sc, "download_episode_media", fake_download)
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)
    monkeypatch.setattr(sc, "_PROGRESS_EDIT_MIN_INTERVAL_SEC", 0)

    update = _make_update("spv_video_720p", chat_id=123)
    context = _make_context()
    update.callback_query.edit_message_text = AsyncMock(side_effect=fake_edit_message_text)

    asyncio.run(sc.download_spotify_video(update, context, _spotify_video_session(), height=720))

    assert "finish:Pobieranie wideo: 100/100" in log
    finish_progress_idx = log.index("finish:Pobieranie wideo: 100/100")
    next_message_start_idx = next(
        i for i, entry in enumerate(log) if entry.startswith("start:Pobieranie zakończone")
    )
    assert finish_progress_idx < next_message_start_idx


def test_download_spotify_video_progress_throttle_uses_real_interval(monkeypatch, tmp_path):
    """Regression guard for the real (non-monkeypatched) throttle interval:
    if the wiring ever passed 0 instead of _PROGRESS_EDIT_MIN_INTERVAL_SEC,
    this test would start seeing two progress edits instead of one, since
    two calls made back-to-back are microseconds apart on the real clock."""

    produced = tmp_path / "episode.mp4"
    produced.write_bytes(b"X" * 1024)

    async def fake_download(*, progress_cb, **kwargs):
        loop = asyncio.get_event_loop()

        def worker():
            progress_cb(10, 100)
            progress_cb(20, 100)

        await loop.run_in_executor(None, worker)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return str(produced)

    monkeypatch.setattr(sc, "download_episode_media", fake_download)
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)
    # _PROGRESS_EDIT_MIN_INTERVAL_SEC deliberately left at its real value.

    update = _make_update("spv_video_720p", chat_id=123)
    context = _make_context()

    asyncio.run(sc.download_spotify_video(update, context, _spotify_video_session(), height=720))

    progress_edits = [
        call.args[0]
        for call in update.callback_query.edit_message_text.await_args_list
        if call.args[0].startswith("Pobieranie wideo: ")
    ]
    assert progress_edits == ["Pobieranie wideo: 10/100"]


def test_download_spotify_video_height_zero_uses_video_label_and_send(monkeypatch, tmp_path):
    """height=0 is unreachable through the real parser (SPOTIFY_VIDEO_HEIGHTS
    excludes it), but the label predicate must stay consistent with the
    send-path predicate (both must use "is None", not truthiness) so a
    future widening of accepted heights can't silently mislabel a video
    download as audio."""

    produced = tmp_path / "episode.mp4"
    produced.write_bytes(b"X" * 1024)

    async def fake_download(**kwargs):
        assert kwargs["height"] == 0
        return str(produced)

    monkeypatch.setattr(sc, "download_episode_media", fake_download)
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)

    update = _make_update("spv_video_720p", chat_id=123)
    context = _make_context()

    asyncio.run(sc.download_spotify_video(update, context, _spotify_video_session(), height=0))

    context.bot.send_video.assert_awaited_once()
    context.bot.send_audio.assert_not_awaited()
    first_status = update.callback_query.edit_message_text.await_args_list[0].args[0]
    assert first_status == "Pobieranie wideo ze Spotify..."


# --- fix round 3 (Task 11 review) -------------------------------------------


def test_download_spotify_video_waits_for_all_pending_progress_edits_not_just_the_last(
    monkeypatch, tmp_path
):
    """A progress edit that takes longer than the throttle window can still
    be in flight when the *next* progress_cb call schedules another one.
    Tracking only the most recently scheduled future would await the
    second (faster) edit but never the first (slower) one, so the first
    could still land after the final message. Every scheduled edit must
    be waited on, not just the last."""

    produced = tmp_path / "episode.mp4"
    produced.write_bytes(b"X" * 1024)

    log = []

    async def fake_edit_message_text(text, reply_markup=None, parse_mode=None):
        log.append(f"start:{text}")
        if text == "Pobieranie wideo: 10/100":
            # The first edit (A) is slower than the second (B) below --
            # without tracking every pending future, only B gets awaited.
            for _ in range(80):
                await asyncio.sleep(0)
        elif text == "Pobieranie wideo: 20/100":
            for _ in range(20):
                await asyncio.sleep(0)
        log.append(f"finish:{text}")

    async def fake_download(*, progress_cb, **kwargs):
        loop = asyncio.get_event_loop()

        def worker():
            # Two progress reports close together, before the executor
            # call returns.
            progress_cb(10, 100)
            progress_cb(20, 100)

        await loop.run_in_executor(None, worker)
        return str(produced)

    monkeypatch.setattr(sc, "download_episode_media", fake_download)
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)
    monkeypatch.setattr(sc, "_PROGRESS_EDIT_MIN_INTERVAL_SEC", 0)

    update = _make_update("spv_video_720p", chat_id=123)
    context = _make_context()
    update.callback_query.edit_message_text = AsyncMock(side_effect=fake_edit_message_text)

    asyncio.run(sc.download_spotify_video(update, context, _spotify_video_session(), height=720))

    assert "finish:Pobieranie wideo: 10/100" in log
    finish_a_idx = log.index("finish:Pobieranie wideo: 10/100")
    next_message_start_idx = next(
        i for i, entry in enumerate(log) if entry.startswith("start:Pobieranie zakończone")
    )
    assert finish_a_idx < next_message_start_idx


def test_download_spotify_video_waits_for_pending_progress_edit_before_error_message(
    monkeypatch, tmp_path
):
    """The wait for pending progress edits lives in a `finally` specifically
    so it also covers the error path, not just the success path -- that
    placement has no test coverage otherwise, and the error path is
    exactly the case that motivated this fix in the first place (a failed
    download must not have its error message clobbered by a stale
    progress line either)."""

    log = []

    async def fake_edit_message_text(text, reply_markup=None, parse_mode=None):
        log.append(f"start:{text}")
        if text.startswith("Pobieranie wideo:"):
            for _ in range(50):
                await asyncio.sleep(0)
        log.append(f"finish:{text}")

    async def fake_download(*, progress_cb, **kwargs):
        loop = asyncio.get_event_loop()

        def worker():
            progress_cb(100, 100)

        await loop.run_in_executor(None, worker)
        raise SpotifyVideoError("api_changed")

    monkeypatch.setattr(sc, "download_episode_media", fake_download)
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)
    monkeypatch.setattr(sc, "_PROGRESS_EDIT_MIN_INTERVAL_SEC", 0)

    update = _make_update("spv_video_720p", chat_id=123)
    context = _make_context()
    update.callback_query.edit_message_text = AsyncMock(side_effect=fake_edit_message_text)

    asyncio.run(sc.download_spotify_video(update, context, _spotify_video_session(), height=720))

    assert "finish:Pobieranie wideo: 100/100" in log
    finish_progress_idx = log.index("finish:Pobieranie wideo: 100/100")
    error_start_idx = next(
        i for i, entry in enumerate(log) if entry.startswith("start:Spotify zmieniło swoje API")
    )
    assert finish_progress_idx < error_start_idx


def test_download_spotify_video_waits_for_pending_progress_edit_before_cancel_message(
    monkeypatch, tmp_path
):
    """Same guarantee as the error-path test above, for the cancellation
    path."""

    log = []

    async def fake_edit_message_text(text, reply_markup=None, parse_mode=None):
        log.append(f"start:{text}")
        if text.startswith("Pobieranie wideo:"):
            for _ in range(50):
                await asyncio.sleep(0)
        log.append(f"finish:{text}")

    async def fake_download(*, progress_cb, **kwargs):
        loop = asyncio.get_event_loop()

        def worker():
            progress_cb(100, 100)

        await loop.run_in_executor(None, worker)
        raise SpotifyVideoCancelled("stopped")

    monkeypatch.setattr(sc, "download_episode_media", fake_download)
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)
    monkeypatch.setattr(sc, "_PROGRESS_EDIT_MIN_INTERVAL_SEC", 0)

    update = _make_update("spv_video_720p", chat_id=123)
    context = _make_context()
    update.callback_query.edit_message_text = AsyncMock(side_effect=fake_edit_message_text)

    asyncio.run(sc.download_spotify_video(update, context, _spotify_video_session(), height=720))

    assert "finish:Pobieranie wideo: 100/100" in log
    finish_progress_idx = log.index("finish:Pobieranie wideo: 100/100")
    cancel_start_idx = next(
        i for i, entry in enumerate(log) if entry.startswith("start:Pobieranie anulowane.")
    )
    assert finish_progress_idx < cancel_start_idx
