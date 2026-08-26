"""Unit tests for bot.spotify_video."""

from pathlib import Path

import pytest

from bot import spotify_video as sv

FIXTURES = Path(__file__).parent / "fixtures"


def _embed_html() -> str:
    return (FIXTURES / "spotify_embed.html").read_text(encoding="utf-8")


def test_load_spotify_cookie_reads_sp_dc(tmp_path):
    jar = tmp_path / "spotify_cookies.txt"
    jar.write_text(
        "# Netscape HTTP Cookie File\n"
        "\n"
        ".spotify.com\tTRUE\t/\tTRUE\t1819277774\tsp_key\taecaf1e4\n"
        ".spotify.com\tTRUE\t/\tTRUE\t1819277774\tsp_dc\tAQDKCX3CHO2D\n",
        encoding="utf-8",
    )
    assert sv.load_spotify_cookie(str(jar)) == "AQDKCX3CHO2D"


def test_load_spotify_cookie_returns_none_for_missing_file(tmp_path):
    assert sv.load_spotify_cookie(str(tmp_path / "absent.txt")) is None


def test_load_spotify_cookie_returns_none_without_sp_dc(tmp_path):
    jar = tmp_path / "spotify_cookies.txt"
    jar.write_text(
        ".spotify.com\tTRUE\t/\tTRUE\t1819277774\tsp_key\taecaf1e4\n",
        encoding="utf-8",
    )
    assert sv.load_spotify_cookie(str(jar)) is None


def test_parse_embed_html_extracts_all_fields():
    data = sv.parse_embed_html(_embed_html())
    assert data.access_token == "FAKE_ACCESS_TOKEN"
    assert data.manifest_id == "cdc59c43c0e85cefb87ad38ee0439f11"
    assert data.title == "Testowy odcinek"
    assert data.show_name == "Testowy podcast"
    assert data.duration_ms == 32000
    assert data.has_video is True
    assert data.requires_drm is False


def test_parse_embed_html_returns_none_without_next_data():
    assert sv.parse_embed_html("<html><body>nothing here</body></html>") is None


def test_parse_embed_html_handles_audio_only_episode():
    html = (
        _embed_html()
        .replace('"hasVideo":true', '"hasVideo":false')
        .replace(
            '"video":[{"manifestId":"cdc59c43c0e85cefb87ad38ee0439f11","requiresDRM":false}]',
            '"video":[]',
        )
    )
    data = sv.parse_embed_html(html)
    assert data.has_video is False
    assert data.manifest_id is None


def test_parse_embed_html_flags_drm_protected_video():
    html = _embed_html().replace('"requiresDRM":false', '"requiresDRM":true')
    assert sv.parse_embed_html(html).requires_drm is True


def test_parse_embed_html_handles_null_settings():
    """Regression test: null settings should not raise AttributeError."""
    html = (
        _embed_html().replace(
            '"settings":{"rtl":false,"session":{"accessToken":"FAKE_ACCESS_TOKEN","isAnonymous":false}}',
            '"settings":null',
        )
    )
    data = sv.parse_embed_html(html)
    assert data is not None
    assert data.access_token == ""  # Empty when settings is null
    assert data.title == "Testowy odcinek"  # Other fields still extracted


def test_parse_embed_html_handles_null_default_audio_file_object():
    """Regression test: null defaultAudioFileObject should not raise AttributeError."""
    html = (
        _embed_html().replace(
            '"defaultAudioFileObject":{"format":"MP4_128_CBCS","video":[{"manifestId":"cdc59c43c0e85cefb87ad38ee0439f11","requiresDRM":false}]}',
            '"defaultAudioFileObject":null',
        )
    )
    data = sv.parse_embed_html(html)
    assert data is not None
    assert data.manifest_id is None  # No video data
    assert data.title == "Testowy odcinek"  # Other fields still extracted


def test_parse_embed_html_handles_null_entity():
    """Regression test: null entity should not raise AttributeError."""
    html = (
        _embed_html().replace(
            '"entity":{"type":"episode","title":"Testowy odcinek","subtitle":"Testowy podcast","duration":32000,"hasVideo":true,"isPlayable":true}',
            '"entity":null',
        )
    )
    data = sv.parse_embed_html(html)
    assert data is not None
    assert data.title == ""  # Empty when entity is null
    assert data.manifest_id == "cdc59c43c0e85cefb87ad38ee0439f11"  # Video data still extracted


def test_parse_embed_html_handles_nested_null_session():
    """Regression test: null session nested inside non-null settings should not raise AttributeError."""
    html = (
        _embed_html().replace(
            '"settings":{"rtl":false,"session":{"accessToken":"FAKE_ACCESS_TOKEN","isAnonymous":false}}',
            '"settings":{"rtl":false,"session":null}',
        )
    )
    data = sv.parse_embed_html(html)
    assert data is not None
    assert data.access_token == ""  # Empty when session is null
    assert data.title == "Testowy odcinek"  # Other fields still extracted


def test_parse_embed_html_handles_null_video_array_element():
    """Regression test: null entry in video array should not raise AttributeError."""
    html = (
        _embed_html().replace(
            '"video":[{"manifestId":"cdc59c43c0e85cefb87ad38ee0439f11","requiresDRM":false}]',
            '"video":[null]',
        )
    )
    data = sv.parse_embed_html(html)
    assert data is not None
    assert data.manifest_id is None  # No valid video data
    assert data.title == "Testowy odcinek"  # Other fields still extracted
