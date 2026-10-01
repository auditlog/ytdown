"""Tests for single-audio delivery with the ✂️ trim button."""

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from bot.handlers import audio_delivery
from bot.services.audio_trim_service import AudioTrimError
from bot.services.trim_store import TrimSource
from tests.telegram_callbacks_support import _make_context


def _write(path: Path, payload: bytes = b"\x00" * 1024) -> Path:
    path.write_bytes(payload)
    return path


def _big(path: Path, size_mb: int) -> Path:
    path.write_bytes(b"")
    os.truncate(path, size_mb * 1024 * 1024)
    return path


def test_send_audio_file_uses_bot_api_with_buttons(tmp_path):
    context = _make_context()
    path = _write(tmp_path / "source.mp3")
    asyncio.run(audio_delivery.send_audio_file(
        context, 5, path, title="T", performer="P", caption="C", filename="Episode.mp3",
        buttons=[("✂️ Przytnij", "trim_src_AAAAAAAAAAA")],
    ))
    kwargs = context.bot.send_audio.await_args.kwargs
    assert (kwargs["title"], kwargs["performer"], kwargs["caption"]) == ("T", "P", "C")
    assert kwargs["filename"] == "Episode.mp3"
    assert kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "trim_src_AAAAAAAAAAA"


def test_send_audio_file_defaults_filename_and_no_markup(tmp_path):
    context = _make_context()
    path = _write(tmp_path / "song.mp3")
    asyncio.run(audio_delivery.send_audio_file(context, 5, path, title="T"))
    kwargs = context.bot.send_audio.await_args.kwargs
    assert kwargs["filename"] == "song.mp3"
    assert kwargs["reply_markup"] is None


def test_send_audio_file_uses_mtproto_above_bot_api_limit(tmp_path, monkeypatch):
    context = _make_context()
    path = _big(tmp_path / "big.mp3", 51)
    captured = {}

    async def fake_mtproto(chat_id, file_path, **kwargs):
        captured.update(kwargs, chat_id=chat_id, file_path=file_path)
        return True

    monkeypatch.setattr(audio_delivery, "mtproto_unavailability_reason", lambda: None)
    monkeypatch.setattr(audio_delivery, "send_audio_mtproto", fake_mtproto)
    asyncio.run(audio_delivery.send_audio_file(
        context, 5, path, title="T", caption="C", buttons=[("✂️ Przytnij", "trim_src_X")],
    ))
    assert captured["file_path"] == str(path)
    assert captured["file_name"] == "big.mp3"
    assert captured["buttons"] == [("✂️ Przytnij", "trim_src_X")]
    context.bot.send_audio.assert_not_awaited()


def test_send_audio_file_reports_unavailable_mtproto(tmp_path, monkeypatch):
    path = _big(tmp_path / "big.mp3", 51)
    monkeypatch.setattr(audio_delivery, "mtproto_unavailability_reason", lambda: "Brak pyrogram.")
    with pytest.raises(audio_delivery.AudioDeliveryError) as exc_info:
        asyncio.run(audio_delivery.send_audio_file(_make_context(), 5, path, title="T"))
    assert "Plik za duży dla Bot API (51 MB, limit: 50 MB)" in str(exc_info.value)
    assert "Brak pyrogram." in str(exc_info.value)


def test_send_audio_file_reports_failed_mtproto_upload(tmp_path, monkeypatch):
    path = _big(tmp_path / "big.mp3", 51)
    monkeypatch.setattr(audio_delivery, "mtproto_unavailability_reason", lambda: None)
    monkeypatch.setattr(audio_delivery, "send_audio_mtproto", AsyncMock(return_value=False))
    with pytest.raises(audio_delivery.AudioDeliveryError) as exc_info:
        asyncio.run(audio_delivery.send_audio_file(_make_context(), 5, path, title="T"))
    assert str(exc_info.value) == "Wysyłanie pliku przez MTProto nie powiodło się."


def test_max_sendable_audio_mb(monkeypatch):
    monkeypatch.setattr(audio_delivery, "mtproto_unavailability_reason", lambda: None)
    assert audio_delivery.max_sendable_audio_mb() == audio_delivery.volume_size_for(use_mtproto=True)
    monkeypatch.setattr(audio_delivery, "mtproto_unavailability_reason", lambda: "no")
    assert audio_delivery.max_sendable_audio_mb() == audio_delivery.TELEGRAM_UPLOAD_LIMIT_MB


def _fake_retain(store_dir: Path):
    def retain(chat_id, file_path, **kwargs):
        store_dir.mkdir(parents=True, exist_ok=True)
        dest = store_dir / "source.mp3"
        Path(file_path).rename(dest)
        return TrimSource("AAAAAAAAAAA", chat_id, dest, kwargs["title"], kwargs["performer"],
                          kwargs["duration_sec"], datetime.now(UTC))
    return retain


def test_send_audio_with_trim_retains_and_attaches_button(tmp_path, monkeypatch):
    context = _make_context()
    original = _write(tmp_path / "Episode.mp3")
    monkeypatch.setattr(audio_delivery, "probe_duration", AsyncMock(return_value=61.6))
    monkeypatch.setattr(audio_delivery, "retain_source", _fake_retain(tmp_path / "store"))

    source = asyncio.run(audio_delivery.send_audio_with_trim(
        context, 5, original, title="Ep", performer="Show", caption="Ep",
    ))

    assert source.duration_sec == 62
    kwargs = context.bot.send_audio.await_args.kwargs
    assert kwargs["filename"] == "Episode.mp3"
    button = kwargs["reply_markup"].inline_keyboard[0][0]
    assert (button.text, button.callback_data) == ("✂️ Przytnij", "trim_src_AAAAAAAAAAA")


def test_send_audio_with_trim_falls_back_when_probe_fails(tmp_path, monkeypatch):
    context = _make_context()
    original = _write(tmp_path / "episode.mp3")
    monkeypatch.setattr(audio_delivery, "probe_duration", AsyncMock(side_effect=AudioTrimError("bad")))
    retain = Mock()
    monkeypatch.setattr(audio_delivery, "retain_source", retain)

    assert asyncio.run(audio_delivery.send_audio_with_trim(context, 5, original, title="Ep")) is None
    retain.assert_not_called()
    assert context.bot.send_audio.await_args.kwargs["reply_markup"] is None
    assert original.exists()


def test_send_audio_with_trim_falls_back_when_store_refuses(tmp_path, monkeypatch):
    context = _make_context()
    original = _write(tmp_path / "episode.mp3")
    monkeypatch.setattr(audio_delivery, "probe_duration", AsyncMock(return_value=30.0))
    monkeypatch.setattr(audio_delivery, "retain_source", lambda *a, **k: None)

    assert asyncio.run(audio_delivery.send_audio_with_trim(context, 5, original, title="Ep")) is None
    assert context.bot.send_audio.await_args.kwargs["reply_markup"] is None


def test_send_audio_with_trim_discards_source_when_send_fails(tmp_path, monkeypatch):
    context = _make_context()
    context.bot.send_audio = AsyncMock(side_effect=RuntimeError("telegram down"))
    original = _write(tmp_path / "episode.mp3")
    monkeypatch.setattr(audio_delivery, "probe_duration", AsyncMock(return_value=30.0))
    monkeypatch.setattr(audio_delivery, "retain_source", _fake_retain(tmp_path / "store"))
    discard = Mock()
    monkeypatch.setattr(audio_delivery, "discard_source", discard)

    with pytest.raises(RuntimeError):
        asyncio.run(audio_delivery.send_audio_with_trim(context, 5, original, title="Ep"))
    discard.assert_called_once()
