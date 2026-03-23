"""Common helper and parser tests for Telegram callbacks."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from telegram.error import BadRequest

from bot import telegram_callbacks as tc


def test_format_bytes_formats_values():
    assert tc.format_bytes(None) == "?"
    assert tc.format_bytes(512) == "512.0 B"
    assert tc.format_bytes(2 * 1024) == "2.0 KB"
    assert tc.format_bytes(3 * 1024 * 1024) == "3.0 MB"
    assert tc.format_bytes(5 * 1024 * 1024 * 1024) == "5.0 GB"
    assert tc.format_bytes(6 * 1024 * 1024 * 1024 * 1024) == "6.0 TB"


def test_format_eta_formats():
    assert tc.format_eta(None) == "?"
    assert tc.format_eta(-1) == "?"
    assert tc.format_eta(45) == "45s"
    assert tc.format_eta(125) == "2m 5s"
    assert tc.format_eta(3661) == "1h 1m"


def test_create_progress_hook_stores_downloading_status():
    hook = tc.create_progress_hook(101)
    hook({
        "status": "downloading",
        "_percent_str": "45%",
        "downloaded_bytes": 1024,
        "total_bytes": 2048,
        "speed": 256,
        "eta": 12,
        "filename": "file.mp3",
    })
    state = tc._download_progress[101]
    assert state["status"] == "downloading"
    assert state["percent"] == "45%"
    assert state["downloaded"] == 1024
    assert state["total"] == 2048


def test_create_progress_hook_stores_finished_and_error():
    hook = tc.create_progress_hook(202)
    hook({"status": "finished", "downloaded_bytes": 100, "total_bytes": 100, "filename": "a"})
    assert tc._download_progress[202]["status"] == "finished"
    assert tc._download_progress[202]["percent"] == "100%"

    hook({"status": "error", "error": "boom"})
    assert tc._download_progress[202]["status"] == "error"


def test_safe_edit_message_ignores_not_modified():
    query = Mock()
    query.edit_message_text = AsyncMock(side_effect=BadRequest("Message is not modified"))
    asyncio.run(tc.safe_edit_message(query, "text"))
    query.edit_message_text.assert_awaited_once()


def test_safe_edit_message_raises_other_bad_request():
    query = Mock()
    query.edit_message_text = AsyncMock(side_effect=BadRequest("Other error"))
    with pytest.raises(BadRequest):
        asyncio.run(tc.safe_edit_message(query, "text"))


def test_send_long_message_splits_large_text():
    bot = Mock()
    bot.send_message = AsyncMock()

    long_text = "A" * 10000
    header = "Header\n\n"
    asyncio.run(tc.send_long_message(bot, 123, long_text, header=header, parse_mode="Markdown"))

    assert bot.send_message.await_count >= 3
    first_call_text = bot.send_message.await_args_list[0].kwargs["text"]
    assert first_call_text.startswith(header)


def test_send_long_message_falls_back_on_parse_error():
    bot = Mock()
    call_count = {"n": 0}

    async def fake_send_message(**kwargs):
        call_count["n"] += 1
        if kwargs.get("parse_mode"):
            raise BadRequest("Can't parse entities")

    bot.send_message = AsyncMock(side_effect=fake_send_message)

    asyncio.run(tc.send_long_message(bot, 123, "text with *broken markdown", parse_mode="Markdown"))

    assert bot.send_message.await_count == 2
    second_call = bot.send_message.await_args_list[1]
    assert "parse_mode" not in second_call.kwargs


def test_parse_summary_option():
    assert tc.parse_summary_option("summary_option_1") == 1
    assert tc.parse_summary_option("audio_summary_option_4") == 4
    assert tc.parse_summary_option("summary_option_0") is None
    assert tc.parse_summary_option("audio_summary_option_bad") is None
    assert tc.parse_summary_option("other_option_2") is None


def test_parse_download_callback_parses_known_payloads():
    assert tc.parse_download_callback("dl_video_720p") == {
        "media_type": "video",
        "mode": "format_id",
        "format": "720p",
    }
    assert tc.parse_download_callback("dl_audio_mp3") == {
        "media_type": "audio",
        "mode": "codec",
        "format": "mp3",
    }
    assert tc.parse_download_callback("dl_audio_format_140") == {
        "media_type": "audio",
        "mode": "format_id",
        "format": "140",
    }


def test_parse_download_callback_returns_none_for_unknown_payload():
    assert tc.parse_download_callback("formats") is None
    assert tc.parse_download_callback("dl_unknown_720p") is None
    assert tc.parse_download_callback("dl_audio_format") is None
