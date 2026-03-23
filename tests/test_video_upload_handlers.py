"""Feature-oriented tests for uploaded video Telegram handlers."""

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


class TestVideoUpload:
    def test_handle_video_upload_requires_pin_when_unauthorized(self, monkeypatch):
        update = _make_update(user_id=888, chat_id=888)
        message = update.message
        message.video = Mock(file_id="vid1", file_size=5000, duration=30, mime_type="video/mp4", file_name="test.mp4")
        message.document = None
        context = _make_context()

        _set_authorized_users(monkeypatch, set())
        monkeypatch.setattr(tc, "handle_pin", AsyncMock(return_value=False))

        _async(tc.handle_video_upload(update, context))

        assert "pending_video" in context.user_data
        assert context.user_data["awaiting_pin"] is True

    def test_handle_video_upload_checks_rate_limit(self, monkeypatch):
        update = _make_update(user_id=888, chat_id=888)
        message = update.message
        message.video = Mock(file_id="vid1", file_size=5000, duration=30, mime_type="video/mp4", file_name="test.mp4")
        message.document = None
        context = _make_context()

        _set_authorized_users(monkeypatch, {888})
        monkeypatch.setattr(tc, "handle_pin", AsyncMock(return_value=False))
        monkeypatch.setattr(tc, "check_rate_limit", lambda *_: False)

        _async(tc.handle_video_upload(update, context))

        assert "Przekroczono limit requestów" in update.message.reply_text.await_args.args[0]

    def test_handle_video_upload_triggers_processing(self, monkeypatch):
        update = _make_update(user_id=888, chat_id=888)
        message = update.message
        message.video = Mock(file_id="vid1", file_size=5000, duration=30, mime_type="video/mp4", file_name="test.mp4")
        message.document = None
        context = _make_context()

        _set_authorized_users(monkeypatch, {888})
        monkeypatch.setattr(tc, "handle_pin", AsyncMock(return_value=False))
        monkeypatch.setattr(tc, "check_rate_limit", lambda *_: True)

        called = {}

        async def fake_process(update_arg, context_arg, video_info):
            called["called"] = True

        monkeypatch.setattr(tc, "process_video_file", fake_process)

        _async(tc.handle_video_upload(update, context))

        assert called["called"] is True

    def test_process_video_file_rejects_large_files(self, monkeypatch):
        update = _make_update(user_id=888, chat_id=888)
        context = _make_context()
        monkeypatch.setattr("bot.mtproto.is_mtproto_available", lambda: False)

        _async(tc.process_video_file(update, context, {
            "file_id": "big_vid",
            "file_size": 50 * 1024 * 1024,
            "duration": 60,
            "mime_type": "video/mp4",
            "title": "bigvideo",
            "ext": ".mp4",
        }))

        assert "Plik jest za duży" in update.message.reply_text.await_args.args[0]

    def test_extract_video_info_from_video_message(self):
        message = Mock()
        message.video = Mock(file_id="v1", file_size=2048, duration=15, mime_type="video/mp4", file_name="clip.mp4")
        message.document = None

        info = tc._extract_video_info(message)

        assert info == {
            "file_id": "v1",
            "file_size": 2048,
            "duration": 15,
            "mime_type": "video/mp4",
            "title": "clip.mp4",
            "ext": ".mp4",
        }

    def test_extract_video_info_from_document(self):
        message = Mock()
        message.video = None
        message.document = Mock(file_id="d1", file_size=4096, mime_type="video/x-matroska", file_name="movie.mkv")

        info = tc._extract_video_info(message)

        assert info == {
            "file_id": "d1",
            "file_size": 4096,
            "duration": None,
            "mime_type": "video/x-matroska",
            "title": "movie.mkv",
            "ext": ".mkv",
        }

    def test_extract_video_info_ignores_non_video_document(self):
        message = Mock()
        message.video = None
        message.document = Mock(file_id="d1", file_size=500, mime_type="application/pdf", file_name="doc.pdf")

        assert tc._extract_video_info(message) is None

    def test_process_video_file_downloads_extracts_and_sets_context(self, tmp_path, monkeypatch):
        update = _make_update(user_id=888, chat_id=888)
        context = _make_context()
        progress_message = Mock()
        progress_message.edit_text = AsyncMock()
        update.message.reply_text = AsyncMock(return_value=progress_message)

        monkeypatch.setattr(tc, "DOWNLOAD_PATH", str(tmp_path / "downloads"))
        os.makedirs(tc.DOWNLOAD_PATH, exist_ok=True)

        tg_file = AsyncMock()

        async def download_to_drive(path):
            Path(path).write_bytes(b"fake-video-data")

        tg_file.download_to_drive = download_to_drive
        context.bot.get_file = AsyncMock(return_value=tg_file)

        original_run = subprocess.run

        def fake_subprocess_run(cmd, **kwargs):
            if cmd[0] == "ffmpeg":
                mp3_path = cmd[-1]
                Path(mp3_path).write_bytes(b"fake-mp3-data")
                result = Mock()
                result.returncode = 0
                result.stderr = b""
                return result
            return original_run(cmd, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

        _async(tc.process_video_file(update, context, {
            "file_id": "vid1",
            "file_size": 5000,
            "duration": 30,
            "mime_type": "video/mp4",
            "title": "test_video",
            "ext": ".mp4",
        }))

        assert "audio_file_path" in context.user_data
        assert context.user_data["audio_file_title"] == "test_video"
        assert context.user_data["audio_file_path"].endswith(".mp3")
        assert progress_message.edit_text.await_count >= 1
