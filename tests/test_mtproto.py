"""
Unit tests for bot.mtproto — MTProto large file download module.
"""

import os
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from bot.mtproto import is_mtproto_available, download_file_mtproto


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

    @pytest.mark.asyncio
    async def test_returns_false_when_pyrogram_missing(self, monkeypatch):
        monkeypatch.setitem(__import__('bot.config', fromlist=['CONFIG']).CONFIG,
                            'TELEGRAM_API_ID', '12345')
        monkeypatch.setitem(__import__('bot.config', fromlist=['CONFIG']).CONFIG,
                            'TELEGRAM_API_HASH', 'abc123hash')
        with patch.dict('sys.modules', {'pyrogram': None}):
            result = await download_file_mtproto("token", 123, 456, "/tmp/test.mp3")
            assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_api_id_missing(self, monkeypatch):
        monkeypatch.setitem(__import__('bot.config', fromlist=['CONFIG']).CONFIG,
                            'TELEGRAM_API_ID', '')
        monkeypatch.setitem(__import__('bot.config', fromlist=['CONFIG']).CONFIG,
                            'TELEGRAM_API_HASH', 'abc123hash')
        mock_pyrogram = MagicMock()
        with patch.dict('sys.modules', {'pyrogram': mock_pyrogram}):
            result = await download_file_mtproto("token", 123, 456, "/tmp/test.mp3")
            assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self, monkeypatch, tmp_path):
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
            result = await download_file_mtproto("token", 123, 456, dest)
            assert result is False
