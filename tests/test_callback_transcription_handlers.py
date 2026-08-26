"""Transcription-, audio-, and Spotify-oriented tests for Telegram callbacks."""

import asyncio
import os

from bot import telegram_callbacks as tc
from bot.handlers import spotify_callbacks as sc
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
