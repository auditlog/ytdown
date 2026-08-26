"""Tests for callback parsing functions."""

from bot.handlers.callback_parsing import parse_spotify_video_callback


def test_parse_spotify_video_callback_video():
    assert parse_spotify_video_callback("spv_video_720p") == {
        "media_type": "video",
        "height": 720,
    }


def test_parse_spotify_video_callback_audio():
    assert parse_spotify_video_callback("spv_audio_m4a") == {
        "media_type": "audio",
        "height": None,
    }


def test_parse_spotify_video_callback_rejects_unknown_height():
    assert parse_spotify_video_callback("spv_video_144p") is None


def test_parse_spotify_video_callback_rejects_foreign_prefix():
    assert parse_spotify_video_callback("dl_video_720p") is None
    assert parse_spotify_video_callback(None) is None
