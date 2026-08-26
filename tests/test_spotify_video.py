"""Unit tests for bot.spotify_video."""

import json
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


def test_parse_embed_html_returns_none_when_data_key_missing():
    """Structural test: missing 'data' key should return None, not partial data.

    This signals a fundamental change in Spotify's page shape, which should be
    handled as 'API changed' not 'episode has no video'.
    """
    html = (
        _embed_html().replace(
            '"data":{"entity":',
            '"removed_data":{"entity":',
        )
    )
    assert sv.parse_embed_html(html) is None


def test_parse_embed_html_returns_none_when_data_is_null():
    """Structural test: null 'data' value should return None, not partial data.

    This signals a fundamental change in Spotify's page shape, which should be
    handled as 'API changed' not 'episode has no video'.
    """
    # Replace the whole "data" value (a self-contained, brace-balanced JSON
    # object) with `null` rather than doing a partial string swap. A partial
    # swap can drop a brace and produce invalid JSON, which would make the
    # assertion pass for the wrong reason (via the JSONDecodeError path,
    # already covered by test_parse_embed_html_returns_none_without_next_data)
    # instead of actually exercising the null-"data" branch.
    original_data_value = (
        '"data":{"entity":{"type":"episode","title":"Testowy odcinek",'
        '"subtitle":"Testowy podcast","duration":32000,"hasVideo":true,'
        '"isPlayable":true},"defaultAudioFileObject":{"format":"MP4_128_CBCS",'
        '"video":[{"manifestId":"cdc59c43c0e85cefb87ad38ee0439f11",'
        '"requiresDRM":false}]}}'
    )
    html = _embed_html().replace(original_data_value, '"data":null')

    # Verify the fixture is still well-formed JSON and that "data" genuinely
    # became null, before handing it to parse_embed_html.
    script_open = '<script id="__NEXT_DATA__" type="application/json">'
    script_close = "</script>"
    start = html.index(script_open) + len(script_open)
    end = html.index(script_close, start)
    payload = json.loads(html[start:end])
    assert payload["props"]["pageProps"]["state"]["data"] is None

    assert sv.parse_embed_html(html) is None


def test_parse_embed_html_returns_none_when_state_is_null():
    """Structural test: null 'state' value should return None, not raise.

    Regression test for a crash where a well-formed payload with
    props.pageProps.state itself null (e.g. {"state": null}) raised an
    unhandled AttributeError from calling .get() directly on None.
    """
    html = (
        '<script id="__NEXT_DATA__" type="application/json">'
        '{"props":{"pageProps":{"state":null}}}'
        "</script>"
    )
    assert sv.parse_embed_html(html) is None


def _manifest() -> dict:
    return json.loads((FIXTURES / "spotify_manifest.json").read_text(encoding="utf-8"))


def test_list_profiles_returns_only_h264_sorted_by_height():
    profiles = sv.list_profiles(_manifest())
    assert [p.height for p in profiles] == [1080, 720, 480]
    assert all(p.codec.startswith("avc1") for p in profiles)


def test_list_profiles_excludes_vp9_and_audio():
    ids = {p.id for p in sv.list_profiles(_manifest())}
    assert 17 not in ids, "VP9 must be excluded"
    assert 15 not in ids and 20 not in ids, "audio profiles must be excluded"


def test_find_audio_profile_id_prefers_aac():
    assert sv.find_audio_profile_id(_manifest()) == 15


def test_manifest_duration_ms():
    assert sv.manifest_duration_ms(_manifest()) == 32000


def test_estimate_size_mb_uses_measured_ratio():
    profile = next(p for p in sv.list_profiles(_manifest()) if p.height == 720)
    # 2605250 bps * 0.45 / 8 = 146545 B/s over 2406 s -> ~336 MiB
    assert sv.estimate_size_mb(profile, 2406000) == pytest.approx(336, abs=3)
