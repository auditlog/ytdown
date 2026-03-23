"""Tests for stable security policy helpers."""

from bot.security_policy import (
    detect_platform,
    estimate_file_size,
    get_media_label,
    normalize_url,
    validate_url,
)


def test_validate_url_accepts_supported_https_domains():
    assert validate_url("https://www.youtube.com/watch?v=abc") is True
    assert validate_url("https://open.spotify.com/episode/abc") is True
    assert validate_url("https://castbox.fm/episode/test") is True


def test_validate_url_rejects_invalid_or_unsupported_urls():
    assert validate_url("http://www.youtube.com/watch?v=abc") is False
    assert validate_url("https://example.com/video") is False
    assert validate_url("") is False


def test_detect_platform_maps_supported_domains():
    assert detect_platform("https://youtu.be/abc") == "youtube"
    assert detect_platform("https://www.instagram.com/reel/abc") == "instagram"
    assert detect_platform("https://open.spotify.com/episode/abc") == "spotify"


def test_get_media_label_distinguishes_podcast_like_platforms():
    assert get_media_label("spotify") == "odcinku"
    assert get_media_label("castbox") == "odcinku"
    assert get_media_label("youtube") == "filmie"
    assert get_media_label(None) == "filmie"


def test_estimate_file_size_prefers_filesize_over_duration():
    info = {
        "duration": 600,
        "formats": [
            {"format_id": "18", "filesize": 50 * 1024 * 1024},
            {"format_id": "22", "filesize": 75 * 1024 * 1024},
        ],
    }
    assert estimate_file_size(info) == 50.0


def test_normalize_url_returns_original_for_unknown_urls():
    url = "https://www.youtube.com/watch?v=abc"
    assert normalize_url(url) == url
