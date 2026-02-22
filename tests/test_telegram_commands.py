"""
Unit tests for Telegram command handlers.
"""

import asyncio
import os
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta

from unittest.mock import Mock, AsyncMock

from bot import telegram_commands as tc



def _async(coro):
    return asyncio.run(coro)


def _make_update(text: str = "", user_id: int = 123456, chat_id: int = 123456):
    update = Mock()
    update.effective_user.id = user_id
    update.effective_user.first_name = "User"
    update.effective_chat.id = chat_id

    update.message = Mock()
    update.message.text = text
    update.message.reply_text = AsyncMock(return_value=Mock(edit_text=AsyncMock()))
    update.message.delete = AsyncMock()

    return update


def _make_context():
    context = Mock()
    context.user_data = {}
    context.bot = Mock()
    return context


class TestStart:
    def test_start_sets_awaiting_pin_for_unauthorized(self, monkeypatch):
        update = _make_update(user_id=111)
        context = _make_context()

        monkeypatch.setattr(tc, "authorized_users", set())
        tc.block_until[111] = 0

        _async(tc.start(update, context))

        assert context.user_data["awaiting_pin"] is True
        update.message.reply_text.assert_awaited_once()

    def test_start_returns_logged_in_message_when_authorized(self, monkeypatch):
        update = _make_update(user_id=111)
        context = _make_context()

        monkeypatch.setattr(tc, "authorized_users", {111})
        tc.block_until[111] = 0

        _async(tc.start(update, context))

        assert "awaiting_pin" not in context.user_data
        update.message.reply_text.assert_awaited_once_with(
            "Witaj, User!\n\n"
            "Jesteś już zalogowany. Możesz wysłać link do YouTube, aby pobrać film lub audio."
        )

    def test_start_blocked_until_expiration(self, monkeypatch):
        update = _make_update(user_id=111)
        context = _make_context()

        monkeypatch.setattr(tc, "authorized_users", {111})
        tc.block_until[111] = datetime.now().timestamp() + 30

        _async(tc.start(update, context))

        update.message.reply_text.assert_awaited_once()
        assert "zablokowany" in update.message.reply_text.await_args.args[0]


class TestHandlePin:
    def test_handle_pin_accepts_correct_pin_and_clears_state(self, monkeypatch):
        update = _make_update(text="12345678", user_id=222)
        context = _make_context()
        context.user_data.update({"awaiting_pin": True, "pending_url": "https://youtube.com/watch?v=abc"})

        monkeypatch.setattr(tc, "PIN_CODE", "12345678")
        monkeypatch.setattr(tc, "authorized_users", set())
        monkeypatch.setattr(tc, "failed_attempts", defaultdict(int))
        monkeypatch.setattr(tc, "manage_authorized_user", lambda *args, **kwargs: True)

        called = {}

        async def fake_process_youtube_link(update_arg, context_arg, url):
            called["url"] = url

        monkeypatch.setattr(tc, "process_youtube_link", fake_process_youtube_link)

        handled = _async(tc.handle_pin(update, context))

        assert handled is True
        assert "awaiting_pin" not in context.user_data
        assert "pending_url" not in context.user_data
        assert called["url"] == "https://youtube.com/watch?v=abc"
        update.message.reply_text.assert_awaited_once_with(
            "PIN poprawny! Możesz teraz korzystać z bota.\n\n"
            "Wyślij link do YouTube, aby pobrać film lub audio."
        )

    def test_handle_pin_rejects_wrong_pin_and_increments_attempts(self, monkeypatch):
        update = _make_update(text="00000000", user_id=222)
        context = _make_context()
        context.user_data.update({"awaiting_pin": True})

        monkeypatch.setattr(tc, "PIN_CODE", "12345678")
        monkeypatch.setattr(tc, "authorized_users", set())
        monkeypatch.setattr(tc, "failed_attempts", defaultdict(int))

        handled = _async(tc.handle_pin(update, context))

        assert handled is True
        assert tc.failed_attempts[222] == 1
        update.message.reply_text.assert_awaited_once()
        update.message.delete.assert_awaited_once()

    def test_handle_pin_blocks_after_max_attempts(self, monkeypatch):
        update = _make_update(text="00000000", user_id=222)
        context = _make_context()
        context.user_data.update({"awaiting_pin": True})

        monkeypatch.setattr(tc, "PIN_CODE", "12345678")
        monkeypatch.setattr(tc, "MAX_ATTEMPTS", 1)
        monkeypatch.setattr(tc, "authorized_users", set())
        attempts = defaultdict(int)
        monkeypatch.setattr(tc, "failed_attempts", attempts)

        handled = _async(tc.handle_pin(update, context))

        assert handled is True
        assert attempts[222] == 1
        assert tc.block_until[222] > 0
        msg = update.message.reply_text.await_args.args[0]
        assert "Przekroczono maksymalną liczbę prób" in msg


class TestHandleYoutubeLink:
    def test_handle_youtube_link_stores_pending_url_when_unauthorized(self, monkeypatch):
        update = _make_update(text="https://youtube.com/watch?v=abc", user_id=333)
        context = _make_context()

        monkeypatch.setattr(tc, "authorized_users", set())
        monkeypatch.setattr(tc, "handle_pin", AsyncMock(return_value=False))

        _async(tc.handle_youtube_link(update, context))

        assert context.user_data["pending_url"] == "https://youtube.com/watch?v=abc"
        assert context.user_data["awaiting_pin"] is True
        update.message.reply_text.assert_awaited_once()

    def test_handle_youtube_link_sets_time_range_for_active_session(self, monkeypatch):
        update = _make_update(text="0:10-0:20", user_id=333, chat_id=333)
        context = _make_context()

        monkeypatch.setattr(tc, "authorized_users", {333})
        monkeypatch.setattr(tc, "handle_pin", AsyncMock(return_value=False))
        monkeypatch.setattr(tc, "check_rate_limit", lambda *_: True)
        monkeypatch.setattr(tc, "validate_youtube_url", lambda *_: True)
        tc.user_urls[333] = "https://youtube.com/watch?v=existing"

        monkeypatch.setattr(
            tc,
            "get_video_info",
            lambda *_: {"duration": 360, "title": "Existing"},
        )
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

        monkeypatch.setattr(tc, "authorized_users", {333})
        monkeypatch.setattr(tc, "handle_pin", AsyncMock(return_value=False))
        monkeypatch.setattr(tc, "check_rate_limit", lambda *_: True)
        monkeypatch.setattr(tc, "validate_youtube_url", lambda *_: False)
        tc.block_until[333] = 0

        _async(tc.handle_youtube_link(update, context))

        assert "Nieprawidłowy URL" in update.message.reply_text.await_args.args[0]

    def test_handle_youtube_link_calls_process_for_valid_url(self, monkeypatch):
        update = _make_update(text="https://youtube.com/watch?v=ok", user_id=333)
        context = _make_context()

        monkeypatch.setattr(tc, "authorized_users", {333})
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

        monkeypatch.setattr(tc, "get_video_info", lambda *_: {
            "title": "Sample",
            "duration": 120,
        })
        monkeypatch.setattr(tc, "estimate_file_size", lambda *_: 10)
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
        message.voice = Mock(
            file_id="v1",
            file_size=1000,
            duration=10,
            mime_type="audio/ogg",
        )
        message.audio = None
        message.document = None
        context = _make_context()

        monkeypatch.setattr(tc, "authorized_users", set())
        monkeypatch.setattr(tc, "handle_pin", AsyncMock(return_value=False))

        _async(tc.handle_audio_upload(update, context))

        assert "pending_audio" in context.user_data
        assert context.user_data["awaiting_pin"] is True
        update.message.reply_text.assert_awaited_once()

    def test_handle_audio_upload_checks_rate_limit_when_authorized(self, monkeypatch):
        update = _make_update(user_id=555, chat_id=555)
        message = update.message
        message.voice = None
        message.audio = Mock(
            file_id="a1",
            file_size=1000,
            duration=10,
            mime_type="audio/mpeg",
            title="Song",
            file_name="song.mp3",
        )
        message.document = None
        context = _make_context()

        monkeypatch.setattr(tc, "authorized_users", {555})
        monkeypatch.setattr(tc, "handle_pin", AsyncMock(return_value=False))
        monkeypatch.setattr(tc, "check_rate_limit", lambda *_: False)

        _async(tc.handle_audio_upload(update, context))

        update.message.reply_text.assert_awaited_once()
        assert "Przekroczono limit requestów" in update.message.reply_text.await_args.args[0]

    def test_handle_audio_upload_triggers_download_for_valid_input(self, monkeypatch):
        update = _make_update(user_id=555, chat_id=555)
        message = update.message
        message.voice = Mock(
            file_id="a1",
            file_size=1000,
            duration=10,
            mime_type="audio/mpeg",
        )
        message.audio = None
        message.document = None
        context = _make_context()

        monkeypatch.setattr(tc, "authorized_users", {555})
        monkeypatch.setattr(tc, "handle_pin", AsyncMock(return_value=False))
        monkeypatch.setattr(tc, "check_rate_limit", lambda *_: True)

        called = {}

        async def fake_process(update_arg, context_arg, audio_info):
            called["called"] = True

        monkeypatch.setattr(tc, "process_audio_file", fake_process)

        _async(tc.handle_audio_upload(update, context))

        assert called["called"] is True
        assert "called" in called


class TestAudioFileProcessing:
    def test_process_audio_file_rejects_large_files(self):
        update = _make_update(user_id=777, chat_id=777)
        context = _make_context()

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

        _async(tc.process_audio_file(
            update,
            context,
            {
                "file_id": "x1",
                "file_size": 1024,
                "duration": 12,
                "mime_type": "audio/mpeg",
                "title": "abc",
            },
        ))

        assert "audio_file_path" in context.user_data
        assert context.user_data["audio_file_title"] == "abc"
        assert progress_message.edit_text.await_count >= 1


class TestStatusAndStatsCommands:
    def test_status_command_requires_authorization(self, monkeypatch):
        update = _make_update(user_id=111)
        context = _make_context()

        monkeypatch.setattr(tc, "authorized_users", set())

        _async(tc.status_command(update, context))

        update.message.reply_text.assert_awaited_once_with("Brak autoryzacji. Użyj /start aby się zalogować.")

    def test_status_command_shows_disk_info(self, monkeypatch, tmp_path):
        update = _make_update(user_id=111)
        context = _make_context()

        (tmp_path / "a.mp4").write_bytes(b"a" * 2048)
        (tmp_path / "b.mp4").write_bytes(b"b" * 2048)

        monkeypatch.setattr(tc, "authorized_users", {111})
        monkeypatch.setattr(tc, "DOWNLOAD_PATH", str(tmp_path))
        monkeypatch.setattr(tc, "get_disk_usage", lambda: (80.0, 20.0, 100.0, 80.0))

        _async(tc.status_command(update, context))

        message = update.message.reply_text.await_args.args[0]
        assert "**Status systemu**" in message
        assert "Przestrzeń dyskowa" in message
        assert "Plików: 2" in message

    def test_history_command_unauthorized(self, monkeypatch):
        update = _make_update(user_id=111)
        context = _make_context()

        monkeypatch.setattr(tc, "authorized_users", set())

        _async(tc.history_command(update, context))

        update.message.reply_text.assert_awaited_once_with("Brak autoryzacji. Użyj /start aby się zalogować.")

    def test_history_command_empty_history(self, monkeypatch):
        update = _make_update(user_id=111)
        context = _make_context()

        monkeypatch.setattr(tc, "authorized_users", {111})
        monkeypatch.setattr(
            tc,
            "get_download_stats",
            lambda user_id=None: {
                "total_downloads": 0,
                "total_size_mb": 0,
                "format_counts": {},
                "success_count": 0,
                "failure_count": 0,
                "recent": [],
            },
        )

        _async(tc.history_command(update, context))
        update.message.reply_text.assert_awaited_once_with("Brak historii pobrań.")

    def test_history_command_with_data(self, monkeypatch):
        update = _make_update(user_id=111)
        context = _make_context()

        monkeypatch.setattr(tc, "authorized_users", {111})
        monkeypatch.setattr(
            tc,
            "get_download_stats",
            lambda user_id=None: {
                "total_downloads": 1,
                "total_size_mb": 123.4,
                "format_counts": {"audio_mp3": 1},
                "success_count": 1,
                "failure_count": 0,
                "recent": [
                    {
                        "timestamp": "2026-01-01T12:00:00",
                        "title": "Test title",
                        "format": "audio_mp3",
                        "file_size_mb": 50,
                        "status": "success",
                    }
                ],
            },
        )

        _async(tc.history_command(update, context))

        text = update.message.reply_text.await_args.args[0]
        assert "📊 **Historia pobrań**" in text
        assert "Łączna liczba pobrań: 1" in text
        assert "audio_mp3: 1" in text

    def test_cleanup_command_unauthorized(self, monkeypatch):
        update = _make_update(user_id=111)
        context = _make_context()

        monkeypatch.setattr(tc, "authorized_users", set())

        _async(tc.cleanup_command(update, context))

        update.message.reply_text.assert_awaited_once_with("Brak autoryzacji. Użyj /start aby się zalogować.")

    def test_cleanup_command_no_files(self, monkeypatch):
        update = _make_update(user_id=111)
        context = _make_context()

        monkeypatch.setattr(tc, "authorized_users", {111})
        monkeypatch.setattr(tc, "cleanup_old_files", lambda *_args, **_kwargs: 0)
        monkeypatch.setattr(tc, "get_disk_usage", lambda: (80.0, 20.0, 100.0, 80.0))

        _async(tc.cleanup_command(update, context))

        assert "Brak plików do usunięcia." in update.message.reply_text.await_args_list[1].args[0]

    def test_users_command_shows_authorized_users(self, monkeypatch):
        update = _make_update(user_id=111)
        context = _make_context()

        monkeypatch.setattr(tc, "authorized_users", {1, 2, 111})

        _async(tc.users_command(update, context))

        text = update.message.reply_text.await_args.args[0]
        assert "Autoryzowani użytkownicy" in text
        assert "- Liczba: 3" in text


class TestHandleYoutubeLinkTimeRange:
    def test_handle_youtube_link_rejects_range_after_video_end(self, monkeypatch):
        update = _make_update(text="1:00-10:00", user_id=333, chat_id=333)
        context = _make_context()

        monkeypatch.setattr(tc, "authorized_users", {333})
        monkeypatch.setattr(tc, "handle_pin", AsyncMock(return_value=False))
        monkeypatch.setattr(tc, "check_rate_limit", lambda *_: True)
        monkeypatch.setattr(tc, "validate_youtube_url", lambda *_: True)
        tc.user_urls[333] = "https://www.youtube.com/watch?v=test"
        monkeypatch.setattr(
            tc,
            "get_video_info",
            lambda *_: {"duration": 120, "title": "Short"},
        )
        tc.block_until[333] = 0

        _async(tc.handle_youtube_link(update, context))

        assert "przekracza czas trwania filmu" in update.message.reply_text.await_args.args[0]


class TestAudioMetadataExtraction:
    def test_extract_audio_info_reads_voice_message(self):
        message = Mock()
        message.voice = Mock(
            file_id="v1",
            file_size=1024,
            duration=15,
            mime_type="audio/ogg",
        )
        message.audio = None
        message.document = None

        info = tc._extract_audio_info(message)

        assert info == {
            'file_id': 'v1',
            'file_size': 1024,
            'duration': 15,
            'mime_type': 'audio/ogg',
            'title': 'Wiadomość głosowa',
        }

    def test_extract_audio_info_reads_audio_file(self):
        message = Mock()
        message.voice = None
        message.audio = Mock(
            file_id="a1",
            file_size=2048,
            duration=23,
            mime_type="audio/mpeg",
            title="Sample",
            file_name="sample.mp3",
        )
        message.document = None

        info = tc._extract_audio_info(message)

        assert info == {
            'file_id': 'a1',
            'file_size': 2048,
            'duration': 23,
            'mime_type': 'audio/mpeg',
            'title': 'Sample',
        }

    def test_extract_audio_info_ignores_non_audio_document(self):
        message = Mock()
        message.voice = None
        message.audio = None
        message.document = Mock(
            file_id="d1",
            file_size=500,
            mime_type="image/png",
            file_name="not-audio.png",
        )

        assert tc._extract_audio_info(message) is None


class TestProcessYoutubeLinkAndStatus:
    def test_process_youtube_link_includes_size_warning_when_estimated_too_large(self, monkeypatch):
        update = _make_update(user_id=444, chat_id=444)
        context = _make_context()
        progress_message = Mock()
        progress_message.edit_text = AsyncMock()
        update.message.reply_text = AsyncMock(return_value=progress_message)

        monkeypatch.setattr(tc, "get_video_info", lambda *_: {
            "title": "Sample",
            "duration": 120,
        })
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


class TestUsersCommand:
    def test_users_command_shows_summary_for_many_users(self, monkeypatch):
        user_id = 111
        update = _make_update(user_id=user_id)
        context = _make_context()

        monkeypatch.setattr(tc, "authorized_users", {user_id, *range(1, 11)})

        _async(tc.users_command(update, context))

        text = update.message.reply_text.await_args.args[0]
        assert "- Liczba: 11" in text
        assert "- Lista ID: 11 użytkowników" in text


class TestNotifyAdminPinFailure:
    def test_notify_sends_message_when_admin_chat_id_set(self, monkeypatch):
        bot = Mock()
        bot.send_message = AsyncMock()

        user = Mock()
        user.id = 999
        user.username = "testuser"
        user.first_name = "Test"
        user.language_code = "pl"

        monkeypatch.setattr(tc, "ADMIN_CHAT_ID", "12345")

        _async(tc.notify_admin_pin_failure(bot, user, attempt_count=2, blocked=False))

        bot.send_message.assert_awaited_once()
        text = bot.send_message.await_args.kwargs["text"]
        assert "[Failed PIN attempt]" in text
        assert "999" in text
        assert "@testuser" in text

    def test_notify_skips_when_no_admin_chat_id(self, monkeypatch):
        bot = Mock()
        bot.send_message = AsyncMock()

        user = Mock()
        user.id = 999
        user.username = "testuser"
        user.first_name = "Test"
        user.language_code = "pl"

        monkeypatch.setattr(tc, "ADMIN_CHAT_ID", "")

        _async(tc.notify_admin_pin_failure(bot, user, attempt_count=1, blocked=False))

        bot.send_message.assert_not_awaited()

    def test_notify_handles_network_error_gracefully(self, monkeypatch):
        bot = Mock()
        bot.send_message = AsyncMock(side_effect=Exception("network error"))

        user = Mock()
        user.id = 999
        user.username = "testuser"
        user.first_name = "Test"
        user.language_code = "pl"

        monkeypatch.setattr(tc, "ADMIN_CHAT_ID", "12345")

        # Should not raise
        _async(tc.notify_admin_pin_failure(bot, user, attempt_count=1, blocked=False))

    def test_notify_handles_invalid_chat_id(self, monkeypatch):
        bot = Mock()
        bot.send_message = AsyncMock()

        user = Mock()
        user.id = 999
        user.username = "testuser"
        user.first_name = "Test"
        user.language_code = "pl"

        monkeypatch.setattr(tc, "ADMIN_CHAT_ID", "not_a_number")

        _async(tc.notify_admin_pin_failure(bot, user, attempt_count=1, blocked=False))

        bot.send_message.assert_not_awaited()

    def test_notify_sends_blocked_message(self, monkeypatch):
        bot = Mock()
        bot.send_message = AsyncMock()

        user = Mock()
        user.id = 999
        user.username = None
        user.first_name = "Blocked"
        user.language_code = None

        monkeypatch.setattr(tc, "ADMIN_CHAT_ID", "12345")

        _async(tc.notify_admin_pin_failure(bot, user, attempt_count=3, blocked=True))

        text = bot.send_message.await_args.kwargs["text"]
        assert "[BLOCKED]" in text
        assert "n/a" in text  # username is None


class TestHistoryWithNewFields:
    def test_history_command_shows_success_failure_counts(self, monkeypatch):
        update = _make_update(user_id=111)
        context = _make_context()

        monkeypatch.setattr(tc, "authorized_users", {111})
        monkeypatch.setattr(
            tc,
            "get_download_stats",
            lambda user_id=None: {
                "total_downloads": 5,
                "total_size_mb": 200.0,
                "format_counts": {"audio_mp3": 3, "video_best": 2},
                "success_count": 4,
                "failure_count": 1,
                "recent": [
                    {
                        "timestamp": "2026-02-20T12:00:00",
                        "title": "Test OK",
                        "format": "audio_mp3",
                        "file_size_mb": 5.0,
                        "status": "success",
                    },
                    {
                        "timestamp": "2026-02-20T13:00:00",
                        "title": "Test Fail",
                        "format": "video_best",
                        "file_size_mb": 0,
                        "status": "failure",
                        "time_range": "0:30-5:00",
                    },
                ],
            },
        )

        _async(tc.history_command(update, context))

        text = update.message.reply_text.await_args.args[0]
        assert "Udane: 4" in text
        assert "Nieudane: 1" in text
        assert "✅" in text
        assert "❌" in text
        assert "✂️0:30-5:00" in text
