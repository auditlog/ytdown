"""Feature-oriented tests for auth and admin Telegram command handlers."""

from collections import defaultdict
from datetime import datetime
from unittest.mock import AsyncMock, Mock

from bot import telegram_commands as tc
from tests.telegram_commands_support import (
    _async,
    _attach_runtime,
    _make_context,
    _make_update,
    _set_authorized_users,
    _set_runtime_values,
)


class TestStart:
    def test_start_sets_awaiting_pin_for_unauthorized(self, monkeypatch):
        update = _make_update(user_id=111)
        context = _make_context()

        _set_authorized_users(monkeypatch, set())
        tc.block_until[111] = 0

        _async(tc.start(update, context))

        assert context.user_data["awaiting_pin"] is True
        update.message.reply_text.assert_awaited_once()

    def test_start_returns_logged_in_message_when_authorized(self, monkeypatch):
        update = _make_update(user_id=111)
        context = _make_context()

        _set_authorized_users(monkeypatch, {111})
        tc.block_until[111] = 0

        _async(tc.start(update, context))

        assert "awaiting_pin" not in context.user_data
        update.message.reply_text.assert_awaited_once()
        message = update.message.reply_text.await_args.args[0]
        assert "Wyślij link (" in message
        assert "YouTube" in message
        assert "X" in message
        assert ") aby pobrać" in message

    def test_start_blocked_until_expiration(self, monkeypatch):
        update = _make_update(user_id=111)
        context = _make_context()

        _set_authorized_users(monkeypatch, {111})
        tc.block_until[111] = datetime.now().timestamp() + 30

        _async(tc.start(update, context))

        update.message.reply_text.assert_awaited_once()
        assert "zablokowany" in update.message.reply_text.await_args.args[0]


class TestHandlePin:
    def test_handle_pin_accepts_correct_pin_and_clears_state(self, monkeypatch):
        update = _make_update(text="12345678", user_id=222)
        context = _make_context()
        context.user_data.update({"awaiting_pin": True, "pending_url": "https://youtube.com/watch?v=abc"})

        _set_runtime_values(monkeypatch, PIN_CODE="12345678")
        _set_authorized_users(monkeypatch, set())
        monkeypatch.setattr(tc, "failed_attempts", defaultdict(int))
        monkeypatch.setattr(tc, "add_authorized_user_for", lambda *_args, **_kwargs: True)

        called = {}

        async def fake_process_youtube_link(update_arg, context_arg, url):
            called["url"] = url

        monkeypatch.setattr(tc, "process_youtube_link", fake_process_youtube_link)

        handled = _async(tc.handle_pin(update, context))

        assert handled is True
        assert "awaiting_pin" not in context.user_data
        assert "pending_url" not in context.user_data
        assert called["url"] == "https://youtube.com/watch?v=abc"
        update.message.reply_text.assert_awaited_once()
        message = update.message.reply_text.await_args.args[0]
        assert "Wyślij link (" in message
        assert "YouTube" in message
        assert "X" in message
        assert ") aby pobrać" in message

    def test_handle_pin_uses_runtime_authorized_store(self, monkeypatch):
        update = _make_update(text="12345678", user_id=222, chat_id=222)
        context = _make_context()
        runtime = _attach_runtime(context, authorized_users=set())
        runtime.session_store.set_field(222, "awaiting_pin", True)

        _set_runtime_values(monkeypatch, PIN_CODE="12345678")
        monkeypatch.setattr(tc, "failed_attempts", defaultdict(int))

        handled = _async(tc.handle_pin(update, context))

        assert handled is True
        assert 222 in runtime.authorized_users_set
        assert runtime.authorized_users_repository.save.call_count == 1
        assert runtime.session_store.get_field(222, "awaiting_pin") is None

    def test_handle_pin_rejects_wrong_pin_and_increments_attempts(self, monkeypatch):
        update = _make_update(text="00000000", user_id=222)
        context = _make_context()
        context.user_data.update({"awaiting_pin": True})

        _set_runtime_values(monkeypatch, PIN_CODE="12345678")
        _set_authorized_users(monkeypatch, set())
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

        _set_runtime_values(monkeypatch, PIN_CODE="12345678")
        monkeypatch.setattr(tc, "MAX_ATTEMPTS", 1)
        _set_authorized_users(monkeypatch, set())
        attempts = defaultdict(int)
        monkeypatch.setattr(tc, "failed_attempts", attempts)

        handled = _async(tc.handle_pin(update, context))

        assert handled is True
        assert attempts[222] == 1
        assert tc.block_until[222] > 0
        msg = update.message.reply_text.await_args.args[0]
        assert "Przekroczono maksymalną liczbę prób" in msg


class TestStatusAndStatsCommands:
    def test_status_command_requires_authorization(self, monkeypatch):
        update = _make_update(user_id=111)
        context = _make_context()

        _set_authorized_users(monkeypatch, set())

        _async(tc.status_command(update, context))

        update.message.reply_text.assert_awaited_once_with("Brak autoryzacji. Użyj /start aby się zalogować.")

    def test_status_command_shows_disk_info(self, monkeypatch, tmp_path):
        update = _make_update(user_id=111)
        context = _make_context()

        (tmp_path / "a.mp4").write_bytes(b"a" * 2048)
        (tmp_path / "b.mp4").write_bytes(b"b" * 2048)

        _set_authorized_users(monkeypatch, {111})
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

        _set_authorized_users(monkeypatch, set())

        _async(tc.history_command(update, context))

        update.message.reply_text.assert_awaited_once_with("Brak autoryzacji. Użyj /start aby się zalogować.")

    def test_history_command_empty_history(self, monkeypatch):
        update = _make_update(user_id=111)
        context = _make_context()

        _set_authorized_users(monkeypatch, {111})
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

        _set_authorized_users(monkeypatch, {111})
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

        _set_authorized_users(monkeypatch, set())

        _async(tc.cleanup_command(update, context))

        update.message.reply_text.assert_awaited_once_with("Brak autoryzacji. Użyj /start aby się zalogować.")

    def test_cleanup_command_no_files(self, monkeypatch):
        update = _make_update(user_id=111)
        context = _make_context()

        _set_authorized_users(monkeypatch, {111})
        monkeypatch.setattr(tc, "cleanup_old_files", lambda *_args, **_kwargs: 0)
        monkeypatch.setattr(tc, "get_disk_usage", lambda: (80.0, 20.0, 100.0, 80.0))

        _async(tc.cleanup_command(update, context))

        assert "Brak plików do usunięcia." in update.message.reply_text.await_args_list[1].args[0]

    def test_users_command_shows_authorized_users(self, monkeypatch):
        update = _make_update(user_id=111)
        context = _make_context()

        _set_authorized_users(monkeypatch, {1, 2, 111})
        _set_runtime_values(monkeypatch, ADMIN_CHAT_ID="111")

        _async(tc.users_command(update, context))

        text = update.message.reply_text.await_args.args[0]
        assert "Autoryzowani użytkownicy" in text
        assert "- Liczba: 3" in text


class TestUsersCommand:
    def test_users_command_shows_summary_for_many_users(self, monkeypatch):
        user_id = 111
        update = _make_update(user_id=user_id)
        context = _make_context()

        _set_authorized_users(monkeypatch, {user_id, *range(1, 11)})
        _set_runtime_values(monkeypatch, ADMIN_CHAT_ID="111")

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

        _set_runtime_values(monkeypatch, ADMIN_CHAT_ID="12345")

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

        _set_runtime_values(monkeypatch, ADMIN_CHAT_ID="")

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

        _set_runtime_values(monkeypatch, ADMIN_CHAT_ID="12345")

        _async(tc.notify_admin_pin_failure(bot, user, attempt_count=1, blocked=False))

    def test_notify_handles_invalid_chat_id(self, monkeypatch):
        bot = Mock()
        bot.send_message = AsyncMock()

        user = Mock()
        user.id = 999
        user.username = "testuser"
        user.first_name = "Test"
        user.language_code = "pl"

        _set_runtime_values(monkeypatch, ADMIN_CHAT_ID="not_a_number")

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

        _set_runtime_values(monkeypatch, ADMIN_CHAT_ID="12345")

        _async(tc.notify_admin_pin_failure(bot, user, attempt_count=3, blocked=True))

        text = bot.send_message.await_args.kwargs["text"]
        assert "[BLOCKED]" in text
        assert "n/a" in text


class TestHistoryWithNewFields:
    def test_history_command_shows_success_failure_counts(self, monkeypatch):
        update = _make_update(user_id=111)
        context = _make_context()

        _set_authorized_users(monkeypatch, {111})
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
