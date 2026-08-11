"""
Unit tests for bot.downloader_metadata.

get_video_info() swallows the yt-dlp error text, which is why callers could only
ever show a generic message. These tests pin down the variant that keeps it, and
guard the original signature that six call sites still rely on.
"""

import pytest

from bot.downloader_metadata import get_video_info, get_video_info_with_error


AGE_ERROR = "ERROR: [youtube] abc: Sign in to confirm your age."


def _install_fake_ytdl(monkeypatch, *, result=None, error=None):
    """Replace yt_dlp.YoutubeDL with a context manager returning result or raising."""

    class MockYoutubeDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download):
            if error is not None:
                raise Exception(error)
            return result

    monkeypatch.setattr("yt_dlp.YoutubeDL", MockYoutubeDL)


def test_with_error_returns_info_and_no_error_on_success(monkeypatch):
    _install_fake_ytdl(monkeypatch, result={"title": "Sample"})

    info, error = get_video_info_with_error("https://youtube.com/watch?v=test")

    assert info == {"title": "Sample"}
    assert error is None


def test_with_error_returns_yt_dlp_message_on_failure(monkeypatch):
    _install_fake_ytdl(monkeypatch, error=AGE_ERROR)

    info, error = get_video_info_with_error("https://youtube.com/watch?v=test")

    assert info is None
    assert error is not None
    assert "confirm your age" in error


def test_get_video_info_still_returns_info_on_success(monkeypatch):
    _install_fake_ytdl(monkeypatch, result={"title": "Sample"})

    assert get_video_info("https://youtube.com/watch?v=test") == {"title": "Sample"}


def test_get_video_info_still_returns_none_on_failure(monkeypatch):
    _install_fake_ytdl(monkeypatch, error=AGE_ERROR)

    assert get_video_info("https://youtube.com/watch?v=test") is None
