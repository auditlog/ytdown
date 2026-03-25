"""
Unit tests for bot.mtproto — MTProto large file transfer module.
"""

import asyncio
import os
from unittest.mock import patch, AsyncMock, MagicMock

from bot.mtproto import (
    is_mtproto_available,
    download_file_mtproto,
    send_audio_mtproto,
    send_video_mtproto,
)


class TestIsMtprotoAvailable:
    """Tests for is_mtproto_available() configuration check."""

    def test_missing_api_id_returns_false(self, monkeypatch):
        monkeypatch.setitem(__import__('bot.config', fromlist=['CONFIG']).CONFIG,
                            'TELEGRAM_API_ID', '')
        assert is_mtproto_available() is False

    def test_missing_api_hash_returns_false(self, monkeypatch):
        monkeypatch.setitem(__import__('bot.config', fromlist=['CONFIG']).CONFIG,
                            'TELEGRAM_API_ID', '12345')
        monkeypatch.setitem(__import__('bot.config', fromlist=['CONFIG']).CONFIG,
                            'TELEGRAM_API_HASH', '')
        assert is_mtproto_available() is False

    def test_both_keys_set_but_no_pyrogram(self, monkeypatch):
        monkeypatch.setitem(__import__('bot.config', fromlist=['CONFIG']).CONFIG,
                            'TELEGRAM_API_ID', '12345')
        monkeypatch.setitem(__import__('bot.config', fromlist=['CONFIG']).CONFIG,
                            'TELEGRAM_API_HASH', 'abc123hash')
        # pyrogram is not installed in test env
        with patch.dict('sys.modules', {'pyrogram': None}):
            assert is_mtproto_available() is False

    def test_all_configured_and_pyrogram_available(self, monkeypatch):
        monkeypatch.setitem(__import__('bot.config', fromlist=['CONFIG']).CONFIG,
                            'TELEGRAM_API_ID', '12345')
        monkeypatch.setitem(__import__('bot.config', fromlist=['CONFIG']).CONFIG,
                            'TELEGRAM_API_HASH', 'abc123hash')
        mock_pyrogram = MagicMock()
        with patch.dict('sys.modules', {'pyrogram': mock_pyrogram}):
            assert is_mtproto_available() is True


class TestDownloadFileMtproto:
    """Tests for download_file_mtproto() error handling."""

    def test_returns_false_when_pyrogram_missing(self, monkeypatch):
        monkeypatch.setitem(__import__('bot.config', fromlist=['CONFIG']).CONFIG,
                            'TELEGRAM_API_ID', '12345')
        monkeypatch.setitem(__import__('bot.config', fromlist=['CONFIG']).CONFIG,
                            'TELEGRAM_API_HASH', 'abc123hash')
        with patch.dict('sys.modules', {'pyrogram': None}):
            result = asyncio.run(download_file_mtproto("token", 123, 456, "/tmp/test.mp3"))
            assert result is False

    def test_returns_false_when_api_id_missing(self, monkeypatch):
        monkeypatch.setitem(__import__('bot.config', fromlist=['CONFIG']).CONFIG,
                            'TELEGRAM_API_ID', '')
        monkeypatch.setitem(__import__('bot.config', fromlist=['CONFIG']).CONFIG,
                            'TELEGRAM_API_HASH', 'abc123hash')
        mock_pyrogram = MagicMock()
        with patch.dict('sys.modules', {'pyrogram': mock_pyrogram}):
            result = asyncio.run(download_file_mtproto("token", 123, 456, "/tmp/test.mp3"))
            assert result is False

    def test_returns_false_on_exception(self, monkeypatch, tmp_path):
        monkeypatch.setitem(__import__('bot.config', fromlist=['CONFIG']).CONFIG,
                            'TELEGRAM_API_ID', '12345')
        monkeypatch.setitem(__import__('bot.config', fromlist=['CONFIG']).CONFIG,
                            'TELEGRAM_API_HASH', 'abc123hash')

        mock_client_cls = MagicMock()
        mock_client_instance = MagicMock()
        mock_client_instance.__aenter__ = AsyncMock(side_effect=RuntimeError("Connection failed"))
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client_instance

        mock_pyrogram = MagicMock()
        mock_pyrogram.Client = mock_client_cls

        with patch.dict('sys.modules', {'pyrogram': mock_pyrogram}):
            dest = str(tmp_path / "test.mp3")
            result = asyncio.run(download_file_mtproto("token", 123, 456, dest))
            assert result is False


def _set_mtproto_config(monkeypatch, *, api_id="12345", api_hash="abc123hash", bot_token="tok:en"):
    """Helper to set MTProto-related config keys."""
    cfg = __import__('bot.config', fromlist=['CONFIG']).CONFIG
    monkeypatch.setitem(cfg, 'TELEGRAM_API_ID', api_id)
    monkeypatch.setitem(cfg, 'TELEGRAM_API_HASH', api_hash)
    monkeypatch.setitem(cfg, 'TELEGRAM_BOT_TOKEN', bot_token)


class TestSendAudioMtproto:
    """Tests for send_audio_mtproto() upload function."""

    def test_returns_false_when_pyrogram_missing(self, monkeypatch, tmp_path):
        _set_mtproto_config(monkeypatch)
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"\x00" * 100)
        with patch.dict('sys.modules', {'pyrogram': None}):
            result = asyncio.run(send_audio_mtproto(123, str(audio_file), title="Test"))
            assert result is False

    def test_returns_false_when_api_id_missing(self, monkeypatch, tmp_path):
        _set_mtproto_config(monkeypatch, api_id="")
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"\x00" * 100)
        mock_pyrogram = MagicMock()
        with patch.dict('sys.modules', {'pyrogram': mock_pyrogram}):
            result = asyncio.run(send_audio_mtproto(123, str(audio_file), title="Test"))
            assert result is False

    def test_returns_true_on_success(self, monkeypatch, tmp_path):
        _set_mtproto_config(monkeypatch)
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"\x00" * 100)

        mock_client_instance = MagicMock()
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_instance.send_audio = AsyncMock()

        mock_pyrogram = MagicMock()
        mock_pyrogram.Client.return_value = mock_client_instance

        with patch.dict('sys.modules', {'pyrogram': mock_pyrogram}):
            result = asyncio.run(send_audio_mtproto(123, str(audio_file), title="Test", caption="Cap"))
            assert result is True
            mock_client_instance.send_audio.assert_awaited_once()
            call_kwargs = mock_client_instance.send_audio.await_args.kwargs
            assert call_kwargs['chat_id'] == 123
            assert call_kwargs['title'] == "Test"
            assert call_kwargs['caption'] == "Cap"

    def test_returns_false_on_send_exception(self, monkeypatch, tmp_path):
        _set_mtproto_config(monkeypatch)
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"\x00" * 100)

        mock_client_instance = MagicMock()
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_instance.send_audio = AsyncMock(side_effect=RuntimeError("Upload failed"))

        mock_pyrogram = MagicMock()
        mock_pyrogram.Client.return_value = mock_client_instance

        with patch.dict('sys.modules', {'pyrogram': mock_pyrogram}):
            result = asyncio.run(send_audio_mtproto(123, str(audio_file)))
            assert result is False


class TestSendVideoMtproto:
    """Tests for send_video_mtproto() upload function."""

    def test_returns_false_when_pyrogram_missing(self, monkeypatch, tmp_path):
        _set_mtproto_config(monkeypatch)
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"\x00" * 100)
        with patch.dict('sys.modules', {'pyrogram': None}):
            result = asyncio.run(send_video_mtproto(123, str(video_file)))
            assert result is False

    def test_returns_false_when_api_hash_missing(self, monkeypatch, tmp_path):
        _set_mtproto_config(monkeypatch, api_hash="")
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"\x00" * 100)
        mock_pyrogram = MagicMock()
        with patch.dict('sys.modules', {'pyrogram': mock_pyrogram}):
            result = asyncio.run(send_video_mtproto(123, str(video_file)))
            assert result is False

    def test_returns_true_on_success(self, monkeypatch, tmp_path):
        _set_mtproto_config(monkeypatch)
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"\x00" * 100)

        mock_client_instance = MagicMock()
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_instance.send_video = AsyncMock()

        mock_pyrogram = MagicMock()
        mock_pyrogram.Client.return_value = mock_client_instance

        with patch.dict('sys.modules', {'pyrogram': mock_pyrogram}):
            result = asyncio.run(send_video_mtproto(123, str(video_file), caption="My Video"))
            assert result is True
            mock_client_instance.send_video.assert_awaited_once()
            call_kwargs = mock_client_instance.send_video.await_args.kwargs
            assert call_kwargs['chat_id'] == 123
            assert call_kwargs['caption'] == "My Video"

    def test_returns_false_on_send_exception(self, monkeypatch, tmp_path):
        _set_mtproto_config(monkeypatch)
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"\x00" * 100)

        mock_client_instance = MagicMock()
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_instance.send_video = AsyncMock(side_effect=RuntimeError("Upload failed"))

        mock_pyrogram = MagicMock()
        mock_pyrogram.Client.return_value = mock_client_instance

        with patch.dict('sys.modules', {'pyrogram': mock_pyrogram}):
            result = asyncio.run(send_video_mtproto(123, str(video_file)))
            assert result is False
