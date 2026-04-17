"""
Unit tests for bot.mtproto — MTProto large file transfer module.
"""

import asyncio
import os
from unittest.mock import patch, AsyncMock, MagicMock

from bot.mtproto import (
    is_mtproto_available,
    mtproto_unavailability_reason,
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


class TestMtprotoUnavailabilityReason:
    """Verifies that the user-facing hint reflects the actual missing piece."""

    def _set_creds(self, monkeypatch, *, api_id, api_hash):
        cfg = __import__('bot.config', fromlist=['CONFIG']).CONFIG
        monkeypatch.setitem(cfg, 'TELEGRAM_API_ID', api_id)
        monkeypatch.setitem(cfg, 'TELEGRAM_API_HASH', api_hash)

    def test_returns_none_when_fully_configured(self, monkeypatch):
        self._set_creds(monkeypatch, api_id='12345', api_hash='abc')
        mock_pyrogram = MagicMock()
        with patch.dict('sys.modules', {'pyrogram': mock_pyrogram}):
            assert mtproto_unavailability_reason() is None

    def test_mentions_only_pyrogram_when_creds_present(self, monkeypatch):
        self._set_creds(monkeypatch, api_id='12345', api_hash='abc')
        with patch.dict('sys.modules', {'pyrogram': None}):
            reason = mtproto_unavailability_reason()
            assert reason is not None
            assert 'pyrogram' in reason.lower()
            assert 'TELEGRAM_API_ID' not in reason

    def test_mentions_only_creds_when_pyrogram_present(self, monkeypatch):
        self._set_creds(monkeypatch, api_id='', api_hash='abc')
        mock_pyrogram = MagicMock()
        with patch.dict('sys.modules', {'pyrogram': mock_pyrogram}):
            reason = mtproto_unavailability_reason()
            assert reason is not None
            assert 'TELEGRAM_API_ID' in reason
            assert 'pyrogram' not in reason.lower()

    def test_mentions_both_when_nothing_is_set(self, monkeypatch):
        self._set_creds(monkeypatch, api_id='', api_hash='')
        with patch.dict('sys.modules', {'pyrogram': None}):
            reason = mtproto_unavailability_reason()
            assert reason is not None
            assert 'pyrogram' in reason.lower()
            assert 'TELEGRAM_API_ID' in reason


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

    def test_returns_false_when_api_id_not_numeric(self, monkeypatch):
        # Regression: a non-numeric TELEGRAM_API_ID must be rejected cleanly
        # instead of raising ValueError during Client construction.
        monkeypatch.setitem(__import__('bot.config', fromlist=['CONFIG']).CONFIG,
                            'TELEGRAM_API_ID', 'not-a-number')
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

    def test_returns_false_when_api_id_not_numeric(self, monkeypatch, tmp_path):
        # Regression: non-numeric TELEGRAM_API_ID used to raise ValueError inside
        # _build_client, outside the try/except around async with client.
        _set_mtproto_config(monkeypatch, api_id="oops")
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

    def test_returns_false_when_api_id_not_numeric(self, monkeypatch, tmp_path):
        # Regression: non-numeric TELEGRAM_API_ID used to raise ValueError inside
        # _build_client, outside the try/except around async with client.
        _set_mtproto_config(monkeypatch, api_id="bogus")
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
