"""Concurrent jobs must not collide on files or wipe each other's session state."""

import asyncio
import os
import threading
from unittest.mock import AsyncMock, Mock

import pytest

from bot import telegram_commands as tc
from bot.handlers import playlist_callbacks as pc
from bot.handlers import spotify_callbacks as sc
from bot.handlers import transcription_callbacks as trc
from bot.session_context import (
    clear_session_context_value_if,
    clear_session_value_if,
    clear_uploaded_audio_state,
)
from tests.telegram_callbacks_support import _attach_runtime, _make_context, _make_update
from tests.telegram_commands_support import (
    _make_context as _cmd_context,
    _make_update as _cmd_update,
)


class _FixedDatetime:
    """Freeze the timestamp so two uploads land in the same second."""

    @staticmethod
    def now():
        from datetime import datetime

        return datetime(2026, 1, 1, 12, 0, 0)


def _upload_update(message_id, chat_id=900):
    update = _cmd_update(user_id=1, chat_id=chat_id)
    update.message.message_id = message_id
    progress = Mock()
    progress.edit_text = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=progress)
    return update, progress


def _context_with_files(monkeypatch, tmp_path):
    from bot.handlers import inbound_audio, inbound_video

    context = _cmd_context()
    monkeypatch.setattr(inbound_audio, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(inbound_video, "DOWNLOAD_PATH", str(tmp_path))

    async def get_file(file_id):
        tg_file = AsyncMock()

        async def download_to_drive(path):
            await asyncio.sleep(0)  # let the sibling upload interleave
            with open(path, "ab") as handle:
                handle.write(file_id.encode())

        tg_file.download_to_drive = download_to_drive
        return tg_file

    context.bot.get_file = get_file
    return context


def _fake_ffmpeg(cmd, **kwargs):
    """Mimic ffmpeg without -y: refuse to overwrite an existing output."""
    out = cmd[-1]
    if os.path.exists(out):
        return Mock(returncode=1, stderr=b"already exists")
    with open(out, "wb") as handle:
        handle.write(b"mp3")
    return Mock(returncode=0, stderr=b"")


def test_parallel_voice_uploads_get_distinct_files(monkeypatch, tmp_path):
    from bot.handlers import inbound_audio

    monkeypatch.setattr(inbound_audio, "datetime", _FixedDatetime)
    monkeypatch.setattr(tc.subprocess, "run", _fake_ffmpeg)
    context = _context_with_files(monkeypatch, tmp_path)
    first, first_progress = _upload_update(1)
    second, second_progress = _upload_update(2)
    info = lambda file_id: {  # noqa: E731
        "file_id": file_id, "file_size": 1024, "duration": 3,
        "mime_type": "audio/ogg", "title": "Wiadomość głosowa",
    }

    async def scenario():
        await asyncio.gather(
            tc.process_audio_file(first, context, info("a")),
            tc.process_audio_file(second, context, info("b")),
        )

    asyncio.run(scenario())

    mp3_files = [name for name in os.listdir(tmp_path / "900") if name.endswith(".mp3")]
    assert len(mp3_files) == 2
    for progress in (first_progress, second_progress):
        texts = [call.args[0] for call in progress.edit_text.await_args_list]
        assert "Błąd konwersji pliku audio." not in texts


def test_parallel_video_uploads_get_distinct_files(monkeypatch, tmp_path):
    from bot.handlers import inbound_video

    monkeypatch.setattr(inbound_video, "datetime", _FixedDatetime)
    monkeypatch.setattr(tc.subprocess, "run", _fake_ffmpeg)
    context = _context_with_files(monkeypatch, tmp_path)
    first, first_progress = _upload_update(1)
    second, second_progress = _upload_update(2)
    info = lambda file_id: {  # noqa: E731
        "file_id": file_id, "file_size": 1024, "duration": 3,
        "mime_type": "video/mp4", "title": "Video", "ext": ".mp4",
    }

    async def scenario():
        await asyncio.gather(
            tc.process_video_file(first, context, info("a")),
            tc.process_video_file(second, context, info("b")),
        )

    asyncio.run(scenario())

    mp3_files = [name for name in os.listdir(tmp_path / "900") if name.endswith(".mp3")]
    assert len(mp3_files) == 2
    for progress in (first_progress, second_progress):
        texts = [call.args[0] for call in progress.edit_text.await_args_list]
        assert "Błąd ekstrakcji audio z pliku video." not in texts


# --- conditional clears -------------------------------------------------


def test_clear_value_if_only_clears_the_captured_object():
    context = _make_context()
    runtime = _attach_runtime(context)
    old, new = {"id": "old"}, {"id": "new"}
    runtime.session_store.set_field(5, "playlist_data", new)

    assert clear_session_value_if(context, 5, "playlist_data", {}, old) is False
    assert runtime.session_store.get_field(5, "playlist_data") is new
    assert clear_session_value_if(context, 5, "playlist_data", {}, new) is True
    assert runtime.session_store.get_field(5, "playlist_data") is None


def test_clear_context_value_if_only_clears_the_captured_object():
    context = _make_context()
    runtime = _attach_runtime(context)
    old, new = {"id": "old"}, {"id": "new"}
    runtime.session_store.set_field(5, "spotify_resolved", new)

    assert not clear_session_context_value_if(
        context, 5, "spotify_resolved", old, legacy_key="spotify_resolved"
    )
    assert runtime.session_store.get_field(5, "spotify_resolved") is new
    assert clear_session_context_value_if(
        context, 5, "spotify_resolved", new, legacy_key="spotify_resolved"
    )


def test_uploaded_audio_state_survives_when_a_newer_file_replaced_it():
    context = _make_context()
    runtime = _attach_runtime(context)
    runtime.session_store.set_field(5, "audio_file_path", "/new.mp3")
    runtime.session_store.set_field(5, "audio_file_title", "new")

    clear_uploaded_audio_state(context, 5, "/old.mp3")

    assert runtime.session_store.get_field(5, "audio_file_path") == "/new.mp3"
    assert runtime.session_store.get_field(5, "audio_file_title") == "new"
    clear_uploaded_audio_state(context, 5, "/new.mp3")
    assert runtime.session_store.get_field(5, "audio_file_path") is None


# --- jobs ---------------------------------------------------------------


def test_playlist_job_end_keeps_a_newer_playlist(monkeypatch):
    context = _make_context()
    runtime = _attach_runtime(context)
    old = {"entries": [{"url": "http://x/1", "title": "One"}], "title": "Old"}
    newer = {"entries": [{"url": "http://x/2", "title": "Two"}], "title": "New"}
    runtime.session_store.set_field(7, "playlist_data", old)

    async def fake_item(ctx, chat_id, *args):
        runtime.session_store.set_field(chat_id, "playlist_data", newer)

    monkeypatch.setattr(pc, "_download_single_playlist_item", fake_item)
    monkeypatch.setattr(pc, "parse_playlist_download_choice", lambda _d: Mock(media_type="audio", format_choice="mp3"))
    context.bot.send_message = AsyncMock(return_value=Mock(edit_text=AsyncMock()))
    update = _make_update("pl_dl_audio_mp3", chat_id=7)

    asyncio.run(pc.download_playlist(update, context, "pl_dl_audio_mp3"))

    assert runtime.session_store.get_field(7, "playlist_data") is newer


def test_spotify_job_keeps_newer_resolved_and_records_start_url(monkeypatch, tmp_path):
    context = _make_context()
    runtime = _attach_runtime(context)
    resolved = {"title": "Ep", "source": "youtube", "artist": ""}
    newer = {"title": "Other", "source": "youtube", "artist": ""}
    runtime.session_store.set_field(8, "spotify_resolved", resolved)
    runtime.session_store.set_field(8, "current_url", "https://open.spotify.com/episode/OLD")
    audio = tmp_path / "ep.mp3"
    audio.write_bytes(b"x" * 10)
    monkeypatch.setattr(sc, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(sc, "download_resolved_audio", AsyncMock(return_value=str(audio)))

    async def fake_send(*args, **kwargs):
        # A newer link arrives while the job is sending the file.
        runtime.session_store.set_field(8, "spotify_resolved", newer)
        runtime.session_store.set_field(8, "current_url", "https://open.spotify.com/episode/NEW")

    monkeypatch.setattr(sc, "send_audio_with_trim", fake_send)
    recorded = []
    monkeypatch.setattr(sc, "record_download_for", lambda *args: recorded.append(args))
    update = _make_update("dl_audio_mp3", chat_id=8)

    asyncio.run(sc.download_spotify_resolved(update, context, resolved, "mp3"))

    assert runtime.session_store.get_field(8, "spotify_resolved") is newer
    assert recorded and recorded[0][3] == "https://open.spotify.com/episode/OLD"


def test_audio_transcription_keeps_audio_uploaded_during_the_job(monkeypatch, tmp_path):
    context = _make_context()
    runtime = _attach_runtime(context)
    old_mp3 = tmp_path / "old.mp3"
    old_mp3.write_bytes(b"x")
    transcript = tmp_path / "old_transcript.md"
    transcript.write_text("hello", encoding="utf-8")
    runtime.session_store.set_field(9, "audio_file_path", str(old_mp3))
    runtime.session_store.set_field(9, "audio_file_title", "old")
    monkeypatch.setattr(trc, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(trc, "get_runtime_value", lambda *_: "key")

    async def fake_transcribe(**kwargs):
        runtime.session_store.set_field(9, "audio_file_path", "/uploads/new.mp3")
        runtime.session_store.set_field(9, "audio_file_title", "new")
        return str(transcript)

    monkeypatch.setattr(trc, "run_transcription_with_progress", fake_transcribe)
    monkeypatch.setattr(trc, "send_long_message", AsyncMock())
    monkeypatch.setattr(trc, "offer_custom_transcript_prompt", AsyncMock())
    monkeypatch.setattr(trc, "cleanup_transcription_artifacts", Mock())
    monkeypatch.setattr(trc, "record_download_for", Mock())
    update = _make_update("audio_transcribe", chat_id=9)
    update.effective_user.id = 1

    asyncio.run(trc.transcribe_audio_file(update, context))

    assert runtime.session_store.get_field(9, "audio_file_path") == "/uploads/new.mp3"
    assert runtime.session_store.get_field(9, "audio_file_title") == "new"
