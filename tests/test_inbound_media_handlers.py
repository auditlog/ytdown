"""Feature-oriented tests for inbound link and audio Telegram handlers."""

import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from bot import telegram_commands as tc
from tests.telegram_commands_support import (
    _async,
    _make_context,
    _make_update,
    _set_authorized_users,
)


class TestHandleYoutubeLink:
    def test_handle_youtube_link_stores_pending_url_when_unauthorized(self, monkeypatch):
        update = _make_update(text="https://youtube.com/watch?v=abc", user_id=333)
        context = _make_context()

        _set_authorized_users(monkeypatch, set())
        monkeypatch.setattr(tc, "handle_pin", AsyncMock(return_value=False))

        _async(tc.handle_youtube_link(update, context))

        assert context.user_data["pending_url"] == "https://youtube.com/watch?v=abc"
        assert context.user_data["awaiting_pin"] is True
        update.message.reply_text.assert_awaited_once()

    def test_handle_youtube_link_sets_time_range_for_active_session(self, monkeypatch):
        update = _make_update(text="0:10-0:20", user_id=333, chat_id=333)
        context = _make_context()

        _set_authorized_users(monkeypatch, {333})
        monkeypatch.setattr(tc, "handle_pin", AsyncMock(return_value=False))
        monkeypatch.setattr(tc, "check_rate_limit", lambda *_: True)
        monkeypatch.setattr(tc, "validate_youtube_url", lambda *_: True)
        tc.user_urls[333] = "https://youtube.com/watch?v=existing"
        monkeypatch.setattr(tc, "get_video_info", lambda *_: {"duration": 360, "title": "Existing"})
        tc.block_until[333] = 0

        _async(tc.handle_youtube_link(update, context))

        assert tc.user_time_ranges.get(333) == {
            "start": "0:10",
            "end": "0:20",
            "start_sec": 10,
            "end_sec": 20,
        }
        assert "✅ Ustawiono zakres" in update.message.reply_text.await_args.args[0]

    def test_handle_youtube_link_rejects_invalid_url(self, monkeypatch):
        update = _make_update(text="https://example.com/not-valid", user_id=333)
        context = _make_context()

        _set_authorized_users(monkeypatch, {333})
        monkeypatch.setattr(tc, "handle_pin", AsyncMock(return_value=False))
        monkeypatch.setattr(tc, "check_rate_limit", lambda *_: True)
        monkeypatch.setattr(tc, "validate_youtube_url", lambda *_: False)
        tc.block_until[333] = 0

        _async(tc.handle_youtube_link(update, context))

        assert "Nieprawidłowy URL" in update.message.reply_text.await_args.args[0]

    def test_handle_youtube_link_calls_process_for_valid_url(self, monkeypatch):
        update = _make_update(text="https://youtube.com/watch?v=ok", user_id=333)
        context = _make_context()

        _set_authorized_users(monkeypatch, {333})
        monkeypatch.setattr(tc, "handle_pin", AsyncMock(return_value=False))
        monkeypatch.setattr(tc, "check_rate_limit", lambda *_: True)
        monkeypatch.setattr(tc, "validate_youtube_url", lambda *_: True)
        tc.block_until[333] = 0

        called = {}

        async def fake_process(update_arg, context_arg, url):
            called["url"] = url

        monkeypatch.setattr(tc, "process_youtube_link", fake_process)

        _async(tc.handle_youtube_link(update, context))

        assert called["url"] == "https://youtube.com/watch?v=ok"


class TestProcessYoutubeLink:
    def test_process_youtube_link_stores_url_and_edits_menu(self, monkeypatch):
        update = _make_update(user_id=444, chat_id=444)
        context = _make_context()
        progress_message = Mock()
        progress_message.edit_text = AsyncMock()
        update.message.reply_text = AsyncMock(return_value=progress_message)

        monkeypatch.setattr(tc, "get_video_info", lambda *_: {"title": "Sample", "duration": 120})
        monkeypatch.setattr(tc, "estimate_file_size", lambda *_: 10)

        _async(tc.process_youtube_link(update, context, "https://youtube.com/watch?v=test"))

        assert tc.user_urls[444] == "https://youtube.com/watch?v=test"
        assert progress_message.edit_text.await_count == 1
        assert "Sample" in progress_message.edit_text.await_args.args[0]

    def test_process_youtube_link_shows_error_when_video_info_missing(self, monkeypatch):
        update = _make_update(user_id=444, chat_id=444)
        context = _make_context()
        progress_message = Mock()
        progress_message.edit_text = AsyncMock()
        update.message.reply_text = AsyncMock(return_value=progress_message)

        monkeypatch.setattr(tc, "get_video_info", lambda *_: None)

        _async(tc.process_youtube_link(update, context, "https://youtube.com/watch?v=test"))

        progress_message.edit_text.assert_awaited_once_with(
            "Wystąpił błąd podczas pobierania informacji o filmie."
        )


class TestAudioUpload:
    def test_handle_audio_upload_requires_pin_when_unauthorized(self, monkeypatch):
        update = _make_update(user_id=555, chat_id=555)
        message = update.message
        message.voice = Mock(file_id="v1", file_size=1000, duration=10, mime_type="audio/ogg")
        message.audio = None
        message.document = None
        context = _make_context()

        _set_authorized_users(monkeypatch, set())
        monkeypatch.setattr(tc, "handle_pin", AsyncMock(return_value=False))

        _async(tc.handle_audio_upload(update, context))

        assert "pending_audio" in context.user_data
        assert context.user_data["awaiting_pin"] is True
        update.message.reply_text.assert_awaited_once()

    def test_handle_audio_upload_checks_rate_limit_when_authorized(self, monkeypatch):
        update = _make_update(user_id=555, chat_id=555)
        message = update.message
        message.voice = None
        message.audio = Mock(file_id="a1", file_size=1000, duration=10, mime_type="audio/mpeg", title="Song", file_name="song.mp3")
        message.document = None
        context = _make_context()

        _set_authorized_users(monkeypatch, {555})
        monkeypatch.setattr(tc, "handle_pin", AsyncMock(return_value=False))
        monkeypatch.setattr(tc, "check_rate_limit", lambda *_: False)

        _async(tc.handle_audio_upload(update, context))

        update.message.reply_text.assert_awaited_once()
        assert "Przekroczono limit requestów" in update.message.reply_text.await_args.args[0]

    def test_handle_audio_upload_triggers_download_for_valid_input(self, monkeypatch):
        update = _make_update(user_id=555, chat_id=555)
        message = update.message
        message.voice = Mock(file_id="a1", file_size=1000, duration=10, mime_type="audio/mpeg")
        message.audio = None
        message.document = None
        context = _make_context()

        _set_authorized_users(monkeypatch, {555})
        monkeypatch.setattr(tc, "handle_pin", AsyncMock(return_value=False))
        monkeypatch.setattr(tc, "check_rate_limit", lambda *_: True)

        called = {}

        async def fake_process(update_arg, context_arg, audio_info):
            called["called"] = True

        monkeypatch.setattr(tc, "process_audio_file", fake_process)

        _async(tc.handle_audio_upload(update, context))

        assert called["called"] is True


class TestAudioFileProcessing:
    def test_process_audio_file_rejects_large_files(self, monkeypatch):
        update = _make_update(user_id=777, chat_id=777)
        context = _make_context()
        monkeypatch.setattr("bot.mtproto.is_mtproto_available", lambda: False)

        result = _async(tc.process_audio_file(update, context, {
            "file_id": "big1",
            "file_size": 50 * 1024 * 1024,
            "duration": 10,
            "mime_type": "audio/mpeg",
            "title": "bigfile",
        }))

        assert result is None
        update.message.reply_text.assert_awaited_once()
        assert "Plik jest za duży" in update.message.reply_text.await_args.args[0]

    def test_process_audio_file_downloads_and_sets_context(self, tmp_path, monkeypatch):
        update = _make_update(user_id=777, chat_id=777)
        context = _make_context()
        progress_message = Mock()
        progress_message.edit_text = AsyncMock()
        update.message.reply_text = AsyncMock(return_value=progress_message)

        monkeypatch.setattr(tc, "DOWNLOAD_PATH", str(tmp_path / "downloads"))
        os.makedirs(tc.DOWNLOAD_PATH, exist_ok=True)

        tg_file = AsyncMock()

        async def download_to_drive(path):
            Path(path).write_bytes(b"abc")

        tg_file.download_to_drive = download_to_drive
        context.bot.get_file = AsyncMock(return_value=tg_file)

        _async(tc.process_audio_file(update, context, {
            "file_id": "x1",
            "file_size": 1024,
            "duration": 12,
            "mime_type": "audio/mpeg",
            "title": "abc",
        }))

        assert "audio_file_path" in context.user_data
        assert context.user_data["audio_file_title"] == "abc"
        assert progress_message.edit_text.await_count >= 1


class TestHandleYoutubeLinkTimeRange:
    def test_handle_youtube_link_rejects_range_after_video_end(self, monkeypatch):
        update = _make_update(text="1:00-10:00", user_id=333, chat_id=333)
        context = _make_context()

        _set_authorized_users(monkeypatch, {333})
        monkeypatch.setattr(tc, "handle_pin", AsyncMock(return_value=False))
        monkeypatch.setattr(tc, "check_rate_limit", lambda *_: True)
        monkeypatch.setattr(tc, "validate_youtube_url", lambda *_: True)
        tc.user_urls[333] = "https://www.youtube.com/watch?v=test"
        monkeypatch.setattr(tc, "get_video_info", lambda *_: {"duration": 120, "title": "Short"})
        tc.block_until[333] = 0

        _async(tc.handle_youtube_link(update, context))

        assert "przekracza czas trwania filmu" in update.message.reply_text.await_args.args[0]


class TestAudioMetadataExtraction:
    def test_extract_audio_info_reads_voice_message(self):
        message = Mock()
        message.voice = Mock(file_id="v1", file_size=1024, duration=15, mime_type="audio/ogg")
        message.audio = None
        message.document = None

        info = tc._extract_audio_info(message)

        assert info == {
            "file_id": "v1",
            "file_size": 1024,
            "duration": 15,
            "mime_type": "audio/ogg",
            "title": "Wiadomość głosowa",
        }

    def test_extract_audio_info_reads_audio_file(self):
        message = Mock()
        message.voice = None
        message.audio = Mock(file_id="a1", file_size=2048, duration=23, mime_type="audio/mpeg", title="Sample", file_name="sample.mp3")
        message.document = None

        info = tc._extract_audio_info(message)

        assert info == {
            "file_id": "a1",
            "file_size": 2048,
            "duration": 23,
            "mime_type": "audio/mpeg",
            "title": "Sample",
        }

    def test_extract_audio_info_ignores_non_audio_document(self):
        message = Mock()
        message.voice = None
        message.audio = None
        message.document = Mock(file_id="d1", file_size=500, mime_type="image/png", file_name="not-audio.png")

        assert tc._extract_audio_info(message) is None


class TestProcessYoutubeLinkAndStatus:
    def test_process_youtube_link_includes_size_warning_when_estimated_too_large(self, monkeypatch):
        update = _make_update(user_id=444, chat_id=444)
        context = _make_context()
        progress_message = Mock()
        progress_message.edit_text = AsyncMock()
        update.message.reply_text = AsyncMock(return_value=progress_message)

        monkeypatch.setattr(tc, "get_video_info", lambda *_: {"title": "Sample", "duration": 120})
        monkeypatch.setattr(tc, "estimate_file_size", lambda *_: tc.MAX_FILE_SIZE_MB + 10)

        _async(tc.process_youtube_link(update, context, "https://youtube.com/watch?v=large"))

        text = progress_message.edit_text.await_args.args[0]
        buttons = [
            button.text
            for row in progress_message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]

        assert "Szacowany rozmiar najlepszej jakości" in text
        assert "Video 1080p (Full HD)" in buttons


class TestProcessAudioFile:
    def test_process_audio_file_reports_unrecognized_message(self):
        update = _make_update(user_id=777, chat_id=777)
        context = _make_context()
        update.message.voice = None
        update.message.audio = None
        update.message.document = None

        _async(tc.process_audio_file(update, context))

        update.message.reply_text.assert_awaited_once_with("Nie rozpoznano pliku audio.")

    def test_process_audio_file_handles_conversion_failure(self, monkeypatch):
        update = _make_update(user_id=777, chat_id=777)
        context = _make_context()

        progress_message = Mock()
        progress_message.edit_text = AsyncMock()
        update.message.reply_text = AsyncMock(return_value=progress_message)

        tg_file = AsyncMock()
        tg_file.download_to_drive = AsyncMock()
        context.bot.get_file = AsyncMock(return_value=tg_file)

        monkeypatch.setattr(
            tc.subprocess,
            "run",
            lambda *args, **kwargs: Mock(returncode=1, stderr=b"conversion failed"),
        )

        _async(tc.process_audio_file(update, context, {
            "file_id": "x1",
            "file_size": 1024,
            "duration": 10,
            "mime_type": "audio/wav",
            "title": "sample",
        }))

        progress_message.edit_text.assert_any_await("Konwersja do MP3...")
        progress_message.edit_text.assert_any_await("Błąd konwersji pliku audio.")


class TestMultiPlatformUI:
    def test_process_youtube_link_hides_flac_and_time_range_for_tiktok(self, monkeypatch):
        update = _make_update(user_id=444, chat_id=444)
        context = _make_context()
        progress_message = Mock()
        progress_message.edit_text = AsyncMock()
        update.message.reply_text = AsyncMock(return_value=progress_message)

        monkeypatch.setattr(tc, "get_video_info", lambda *_: {"title": "TikTok Video", "duration": 30})
        monkeypatch.setattr(tc, "estimate_file_size", lambda *_: 10)
        monkeypatch.setattr(tc, "detect_platform", lambda *_: "tiktok")

        _async(tc.process_youtube_link(update, context, "https://www.tiktok.com/@user/video/1"))

        assert context.user_data["platform"] == "tiktok"
        buttons = [
            button.text
            for row in progress_message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        assert "Audio (FLAC)" not in buttons
        assert "✂️ Zakres czasowy" not in buttons
        assert "Audio (MP3)" in buttons

    def test_process_youtube_link_shows_all_buttons_for_youtube(self, monkeypatch):
        update = _make_update(user_id=444, chat_id=444)
        context = _make_context()
        progress_message = Mock()
        progress_message.edit_text = AsyncMock()
        update.message.reply_text = AsyncMock(return_value=progress_message)

        monkeypatch.setattr(tc, "get_video_info", lambda *_: {"title": "YouTube Video", "duration": 600})
        monkeypatch.setattr(tc, "estimate_file_size", lambda *_: 10)
        monkeypatch.setattr(tc, "detect_platform", lambda *_: "youtube")

        _async(tc.process_youtube_link(update, context, "https://www.youtube.com/watch?v=abc"))

        buttons = [
            button.text
            for row in progress_message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        assert "Audio (FLAC)" in buttons
        assert "✂️ Zakres czasowy" in buttons

    def test_process_youtube_link_shows_audio_only_for_castbox(self, monkeypatch):
        update = _make_update(user_id=444, chat_id=444)
        context = _make_context()
        progress_message = Mock()
        progress_message.edit_text = AsyncMock()
        update.message.reply_text = AsyncMock(return_value=progress_message)

        monkeypatch.setattr(tc, "get_video_info", lambda *_: {"title": "Podcast Episode", "duration": 0})
        monkeypatch.setattr(tc, "estimate_file_size", lambda *_: 10)
        monkeypatch.setattr(tc, "detect_platform", lambda *_: "castbox")
        monkeypatch.setattr(tc, "normalize_url", lambda url: url)

        _async(tc.process_youtube_link(update, context, "https://castbox.fm/episode/Test-id123"))

        buttons = [
            button.text
            for row in progress_message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        assert "Audio (MP3)" in buttons
        assert "Transkrypcja audio" in buttons
        assert "Najlepsza jakość video" not in buttons
        assert "Audio (FLAC)" not in buttons
        assert "✂️ Zakres czasowy" not in buttons
        assert "Lista formatów" not in buttons

    def test_castbox_channel_url_rejected(self, monkeypatch):
        update = _make_update(user_id=444, chat_id=444)
        context = _make_context()

        monkeypatch.setattr(tc, "detect_platform", lambda *_: "castbox")
        monkeypatch.setattr(tc, "normalize_url", lambda url: url)

        _async(tc.process_youtube_link(update, context, "https://castbox.fm/channel/Podcast-id123"))

        call_text = update.message.reply_text.await_args.args[0]
        assert "kanału nie jest obsługiwany" in call_text
