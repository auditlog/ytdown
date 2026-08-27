"""Unit tests for bot.spotify_video."""

import json
import logging
import threading
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


def test_load_spotify_cookie_discovers_browser_export_name(tmp_path, monkeypatch):
    jar = tmp_path / "open.spotify.com_cookies.txt"
    jar.write_text(
        ".spotify.com\tTRUE\t/\tTRUE\t1819277774\tsp_dc\tSESSION_TOKEN\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sv, "__file__", str(tmp_path / "bot" / "spotify_video.py"))
    monkeypatch.setattr(sv, "SPOTIFY_COOKIES_FILE", str(tmp_path / "missing.txt"))
    monkeypatch.setattr(sv, "get_runtime_value", lambda key, default="": default)

    assert sv.load_spotify_cookie() == "SESSION_TOKEN"


def test_load_spotify_cookie_returns_none_for_missing_file(tmp_path):
    assert sv.load_spotify_cookie(str(tmp_path / "absent.txt")) is None


def test_load_spotify_cookie_returns_none_without_sp_dc(tmp_path):
    jar = tmp_path / "spotify_cookies.txt"
    jar.write_text(
        ".spotify.com\tTRUE\t/\tTRUE\t1819277774\tsp_key\taecaf1e4\n",
        encoding="utf-8",
    )
    assert sv.load_spotify_cookie(str(jar)) is None


def test_load_spotify_cookie_returns_none_for_non_utf8_jar(tmp_path):
    """A jar saved in a non-UTF-8 encoding raises UnicodeDecodeError from the
    read, not OSError. Left uncaught it escapes load_spotify_cookie and every
    caller above it, freezing the user's status message; the file is simply
    unusable, which is exactly what returning None already means."""

    jar = tmp_path / "spotify_cookies.txt"
    jar.write_bytes(
        b".spotify.com\tTRUE\t/\tTRUE\t1819277774\tsp_dc\tAQ\xff\xfeDK\n"
    )
    assert sv.load_spotify_cookie(str(jar)) is None


def test_parse_embed_html_raises_for_non_numeric_duration():
    """Spotify handing back a non-numeric duration is API drift, not an
    unreadable page: int() raises ValueError, which used to escape
    parse_embed_html raw. It must surface as SpotifyVideoError so the
    service normalizes it to the "api_changed" message like every other
    shape change."""

    html = _embed_html().replace('"duration":32000', '"duration":"40 min"')
    with pytest.raises(sv.SpotifyVideoError):
        sv.parse_embed_html(html)


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


class _FakeResponse:
    """Minimal stand-in for requests.Response for the manifest fetch tests."""

    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(
                "raise_for_status must not be reached for a status the "
                "function handles explicitly"
            )


def test_fetch_video_manifest_raises_for_404(monkeypatch):
    """The undocumented v6 endpoint disappearing is the single most likely
    future break in this integration (design spec 3.2 / 12), and spec 10
    requires it to read as an explicit API change rather than as a network
    problem. Until now nothing tested that branch at all."""

    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers", {})
        return _FakeResponse(status_code=404)

    monkeypatch.setattr(sv.requests, "get", fake_get)

    with pytest.raises(sv.SpotifyVideoError) as exc:
        sv.fetch_video_manifest("cdc59c43", "FAKE_TOKEN")

    assert "404" in str(exc.value)
    # v7 and v8 both answer 404; only v6 exists (spec 3.2).
    assert "/manifests/v6/json/sources/cdc59c43/" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer FAKE_TOKEN"


def test_fetch_video_manifest_returns_parsed_json(monkeypatch):
    monkeypatch.setattr(
        sv.requests, "get",
        lambda url, **kwargs: _FakeResponse(payload={"base_urls": ["https://cdn/"]}),
    )
    assert sv.fetch_video_manifest("cdc59c43", "FAKE_TOKEN") == {
        "base_urls": ["https://cdn/"]
    }


def test_list_profiles_returns_only_h264_sorted_by_height():
    profiles = sv.list_profiles(_manifest())
    # Every H.264 rendition, including 240p -- which profile the *keyboard*
    # offers is build_quality_options' decision (see
    # tests/test_spotify_video_service.py), not this function's.
    assert [p.height for p in profiles] == [1080, 720, 480, 240]
    assert all(p.codec.startswith("avc1") for p in profiles)


def test_list_profiles_excludes_vp9_and_audio():
    ids = {p.id for p in sv.list_profiles(_manifest())}
    assert 17 not in ids, "VP9 must be excluded"
    assert 15 not in ids and 20 not in ids, "audio profiles must be excluded"


def test_list_profiles_raises_for_profile_without_id():
    """A profile entry missing "id" raised a bare KeyError, which escaped the
    whole handler and left the status message frozen. Manifest drift must
    always leave this module as SpotifyVideoError."""

    manifest = _manifest()
    manifest["contents"][0]["profiles"] = [
        {"file_type": "mp4", "max_bitrate": 1, "video_codec": "avc1.4d401f",
         "video_height": 480, "video_width": 854},
    ]
    with pytest.raises(sv.SpotifyVideoError):
        sv.list_profiles(manifest)


def test_find_audio_profile_id_raises_for_profile_without_id():
    manifest = _manifest()
    manifest["contents"][0]["profiles"] = [
        {"audio_codec": "mp4a.40.2", "file_type": "mp4", "max_bitrate": 1},
    ]
    with pytest.raises(sv.SpotifyVideoError):
        sv.find_audio_profile_id(manifest)


def test_find_audio_profile_id_prefers_aac():
    assert sv.find_audio_profile_id(_manifest()) == 15


def test_manifest_duration_ms():
    assert sv.manifest_duration_ms(_manifest()) == 32000


def test_estimate_size_mb_uses_measured_ratio():
    profile = next(p for p in sv.list_profiles(_manifest()) if p.height == 720)
    # 2605250 bps * 0.45 / 8 = 146545 B/s over 2406 s -> ~336 MiB
    assert sv.estimate_size_mb(profile, 2406000) == pytest.approx(336, abs=3)


def test_build_track_urls_generates_one_candidate_per_cdn():
    init_urls, segment_urls = sv.build_track_urls(_manifest(), profile_id=1)
    assert len(init_urls) == 2
    assert init_urls[0].startswith("https://video-fa.scdn.co/segments/")
    assert init_urls[1].startswith("https://video-cf.spotifycdn.com/segments/")


def test_build_track_urls_substitutes_profile_and_file_type():
    init_urls, _ = sv.build_track_urls(_manifest(), profile_id=1)
    assert "/profiles/1/inits/mp4?" in init_urls[0]
    assert "{{" not in init_urls[0]


def test_build_track_urls_counts_segments_from_duration():
    _, segment_urls = sv.build_track_urls(_manifest(), profile_id=1)
    # 32000 ms / 4 s per segment
    assert len(segment_urls) == 8


def test_build_track_urls_uses_second_based_timestamps():
    _, segment_urls = sv.build_track_urls(_manifest(), profile_id=1)
    assert "/profiles/1/0.mp4?" in segment_urls[0][0]
    assert "/profiles/1/4.mp4?" in segment_urls[1][0]
    assert "/profiles/1/28.mp4?" in segment_urls[7][0]


def test_build_track_urls_preserves_query_parameters():
    _, segment_urls = sv.build_track_urls(_manifest(), profile_id=1)
    assert "token=FAKE_TOKEN" in segment_urls[0][0]
    assert "fauth=FAKE_FAUTH" in segment_urls[0][0]


def test_build_track_urls_rounds_partial_final_segment_up():
    manifest = _manifest()
    manifest["contents"][0]["end_time_millis"] = 30000
    _, segment_urls = sv.build_track_urls(manifest, profile_id=1)
    # 30 s / 4 s = 7.5 -> 8 segments, the last one short
    assert len(segment_urls) == 8


def test_build_track_urls_raises_without_initialization_template():
    """manifest["initialization_template"] was a bare subscript: a manifest
    without it raised KeyError straight through the handler. It is the same
    "Spotify changed the manifest" story as a missing base_urls, and must
    read as one."""

    manifest = _manifest()
    del manifest["initialization_template"]
    with pytest.raises(sv.SpotifyVideoError):
        sv.build_track_urls(manifest, profile_id=1)


def test_build_track_urls_raises_without_segment_template():
    manifest = _manifest()
    del manifest["segment_template"]
    with pytest.raises(sv.SpotifyVideoError):
        sv.build_track_urls(manifest, profile_id=1)


def test_download_track_writes_init_then_segments_in_order(tmp_path, monkeypatch):
    # Segment 0 is forced to finish strictly *after* segment 1 by blocking on
    # an Event that segment 1's fetch sets. Both are submitted in the same
    # batch (batch_size=2), so a write-in-completion-order implementation
    # would write "BBB" before "AAA" here and fail the assertion below — a
    # synchronous, instantaneous stub can never do that on its own, which is
    # why this forces the reversal explicitly instead of hoping for it.
    segment_1_done = threading.Event()

    def fake_fetch(url, timeout=30):
        if url == "https://cdn/init":
            return b"INIT"
        if url == "https://cdn/0":
            assert segment_1_done.wait(timeout=5), "segment 1 never completed"
            return b"AAA"
        if url == "https://cdn/1":
            segment_1_done.set()
            return b"BBB"
        if url == "https://cdn/2":
            return b"CCC"
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(sv, "_fetch_bytes", fake_fetch)

    dest = tmp_path / "video.mp4"
    sv.download_track(
        ["https://cdn/init"],
        [["https://cdn/0"], ["https://cdn/1"], ["https://cdn/2"]],
        str(dest),
        batch_size=2,
    )
    assert dest.read_bytes() == b"INITAAABBBCCC"


def test_download_track_falls_over_to_second_cdn(tmp_path, monkeypatch):
    def fake_fetch(url, timeout=30):
        if "primary" in url:
            raise sv.requests.RequestException("primary down")
        return b"OK"

    monkeypatch.setattr(sv, "_fetch_bytes", fake_fetch)
    dest = tmp_path / "video.mp4"
    sv.download_track(
        ["https://primary/init", "https://backup/init"],
        [["https://primary/0", "https://backup/0"]],
        str(dest),
    )
    assert dest.read_bytes() == b"OKOK"


def test_download_track_reports_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(sv, "_fetch_bytes", lambda url, timeout=30: b"X")
    seen = []
    sv.download_track(
        ["https://cdn/init"],
        [["https://cdn/%d" % i] for i in range(5)],
        str(tmp_path / "v.mp4"),
        progress_cb=lambda done, total: seen.append((done, total)),
        batch_size=2,
    )
    # Exact sequence, not just the final value: (5, 5) alone is what a
    # non-batching, single-callback-at-the-end implementation would also
    # produce, so it proves nothing about batching on its own.
    assert seen == [(2, 5), (4, 5), (5, 5)]


def test_download_track_raises_after_exhausting_retries(tmp_path, monkeypatch):
    calls = []
    sleeps = []

    def fake_fetch(url, timeout=30):
        calls.append(url)
        raise sv.requests.RequestException("boom")

    monkeypatch.setattr(sv, "_fetch_bytes", fake_fetch)
    monkeypatch.setattr(sv.time, "sleep", lambda seconds: sleeps.append(seconds))

    with pytest.raises(sv.SpotifyVideoError):
        sv.download_track(
            ["https://cdn/init"], [["https://cdn/0"]], str(tmp_path / "v.mp4")
        )

    # Fails on the init fetch, which goes through the same _fetch_with_failover
    # as segments — this proves every attempt is tried and backoff happens
    # between attempts (but not after the last one), not just that *some*
    # exception eventually surfaces as SpotifyVideoError.
    assert calls == ["https://cdn/init"] * sv.SEGMENT_ATTEMPTS
    assert sleeps == [1, 2]


def test_download_track_retries_segment_after_init_succeeds(tmp_path, monkeypatch):
    candidates = ["https://primary/0", "https://backup/0"]
    calls = []
    sleeps = []

    def fake_fetch(url, timeout=30):
        if url == "https://cdn/init":
            return b"INIT"
        calls.append(url)
        raise sv.requests.RequestException("boom")

    monkeypatch.setattr(sv, "_fetch_bytes", fake_fetch)
    monkeypatch.setattr(sv.time, "sleep", lambda seconds: sleeps.append(seconds))

    # Unlike the test above, the init fetch succeeds, so this exercises the
    # segment path running inside the ThreadPoolExecutor rather than the
    # direct call before it — proving the concurrent path retries and backs
    # off exactly like the synchronous one.
    with pytest.raises(sv.SpotifyVideoError):
        sv.download_track(
            ["https://cdn/init"], [candidates], str(tmp_path / "v.mp4")
        )

    assert calls == candidates * sv.SEGMENT_ATTEMPTS
    assert sleeps == [1, 2]


def test_download_track_leaves_no_file_after_exhausting_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sv, "_fetch_bytes",
        lambda url, timeout=30: (_ for _ in ()).throw(sv.requests.RequestException("boom")),
    )
    monkeypatch.setattr(sv.time, "sleep", lambda seconds: None)
    dest = tmp_path / "v.mp4"

    with pytest.raises(sv.SpotifyVideoError):
        sv.download_track(["https://cdn/init"], [["https://cdn/0"]], str(dest))

    assert not dest.exists()
    assert not (tmp_path / "v.mp4.part").exists()


def test_download_track_honours_cancellation(tmp_path, monkeypatch):
    monkeypatch.setattr(sv, "_fetch_bytes", lambda url, timeout=30: b"X")

    class _Cancelled:
        class event:
            @staticmethod
            def is_set():
                return True

    with pytest.raises(sv.SpotifyVideoCancelled):
        sv.download_track(
            ["https://cdn/init"],
            [["https://cdn/0"]],
            str(tmp_path / "v.mp4"),
            cancellation=_Cancelled(),
        )


def test_download_track_cancellation_mid_batch_leaves_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sv, "_fetch_bytes", lambda url, timeout=30: b"X")

    class _CancelAfterFirstBatch:
        """Lets the pre-download check and the first batch's check pass,
        then cancels before the second batch starts."""

        def __init__(self):
            self._checks = 0
            self.event = self

        def is_set(self):
            self._checks += 1
            return self._checks > 2

    dest = tmp_path / "v.mp4"
    with pytest.raises(sv.SpotifyVideoCancelled):
        sv.download_track(
            ["https://cdn/init"],
            [["https://cdn/%d" % i] for i in range(4)],
            str(dest),
            batch_size=2,
            cancellation=_CancelAfterFirstBatch(),
        )

    assert not dest.exists()
    assert not (tmp_path / "v.mp4.part").exists()


def test_download_track_wraps_non_request_exceptions(tmp_path, monkeypatch):
    def fake_fetch(url, timeout=30):
        if url == "https://cdn/init":
            return b"INIT"
        raise ValueError("weird, non-network failure")

    monkeypatch.setattr(sv, "_fetch_bytes", fake_fetch)
    monkeypatch.setattr(sv.time, "sleep", lambda seconds: None)

    with pytest.raises(sv.SpotifyVideoError):
        sv.download_track(
            ["https://cdn/init"], [["https://cdn/0"]], str(tmp_path / "v.mp4")
        )


def test_download_track_propagates_cancellation_raised_during_fetch(tmp_path, monkeypatch):
    calls = []

    def fake_fetch(url, timeout=30):
        if url == "https://cdn/init":
            return b"INIT"
        calls.append(url)
        raise sv.SpotifyVideoCancelled("cancelled mid-fetch")

    monkeypatch.setattr(sv, "_fetch_bytes", fake_fetch)

    with pytest.raises(sv.SpotifyVideoCancelled):
        sv.download_track(
            ["https://cdn/init"], [["https://cdn/0"]], str(tmp_path / "v.mp4")
        )
    # Not retried like an ordinary fetch failure would be.
    assert len(calls) == 1


def test_download_track_rejects_empty_segment_candidates_immediately(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr(sv.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(sv, "_fetch_bytes", lambda url, timeout=30: b"INIT")

    with pytest.raises(sv.SpotifyVideoError):
        sv.download_track(["https://cdn/init"], [[]], str(tmp_path / "v.mp4"))

    assert sleeps == []


def test_download_track_error_message_includes_failing_url(tmp_path, monkeypatch):
    def fake_fetch(url, timeout=30):
        if url == "https://cdn/init":
            return b"INIT"
        raise sv.requests.RequestException("boom")

    monkeypatch.setattr(sv, "_fetch_bytes", fake_fetch)
    monkeypatch.setattr(sv.time, "sleep", lambda seconds: None)

    with pytest.raises(sv.SpotifyVideoError, match="https://cdn/0"):
        sv.download_track(
            ["https://cdn/init"], [["https://cdn/0"]], str(tmp_path / "v.mp4")
        )


def test_subtitle_languages_reads_manifest():
    assert sv.subtitle_languages(_manifest()) == ["pl-pl"]


def test_subtitle_languages_empty_when_absent():
    manifest = _manifest()
    del manifest["subtitle_language_codes"]
    assert sv.subtitle_languages(manifest) == []


def test_fetch_subtitles_writes_vtt(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sv, "_fetch_bytes",
        lambda url, timeout=30: b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nCzesc\n",
    )
    dest = tmp_path / "subs.vtt"
    result = sv.fetch_subtitles(_manifest(), "pl-pl", str(dest))
    assert result == str(dest)
    assert dest.read_text(encoding="utf-8").startswith("WEBVTT")


def test_fetch_subtitles_returns_none_for_unknown_language(tmp_path, monkeypatch):
    """The point is the guard clause, not the return value. Without this stub
    the test passed even with the guard deleted: control fell through to
    _fetch_with_failover, three real HTTPS requests failed against the
    fixture's host over three real seconds of backoff, and fetch_subtitles
    returned None anyway -- green, networked, and proving nothing."""

    def must_not_fetch(*args, **kwargs):
        pytest.fail("fetch_subtitles must not fetch for a language the manifest does not offer")

    monkeypatch.setattr(sv, "_fetch_bytes", must_not_fetch)

    assert sv.fetch_subtitles(_manifest(), "de-de", str(tmp_path / "s.vtt")) is None


_SIGNED_SEGMENT_URL = (
    "https://video-fa.scdn.co/segments/v1/origins/O/sources/S/profiles/1/0.mp4"
    "?__token__=SUPERSECRETTOKEN&fauth=SUPERSECRETFAUTH"
)


def test_fetch_with_failover_error_message_redacts_signed_urls(monkeypatch):
    """Spotify's CDN URLs are capability URLs: __token__ and fauth authorize
    the fetch, so anyone who can read the failure message (it is logged, and
    it reaches the user's chat through the download flow) can pull the object
    until the signature expires. The host and path are the diagnostic and
    must survive; the query string must not."""

    def fake_fetch(url, timeout=30):
        # A real requests failure quotes the URL it was given, so the secret
        # arrives via the chained error as well as via the URL itself.
        raise RuntimeError(f"403 Client Error: Forbidden for url: {url}")

    monkeypatch.setattr(sv, "_fetch_bytes", fake_fetch)
    monkeypatch.setattr(sv.time, "sleep", lambda seconds: None)

    with pytest.raises(sv.SpotifyVideoError) as exc:
        sv._fetch_with_failover([_SIGNED_SEGMENT_URL])

    message = str(exc.value)
    assert "SUPERSECRETTOKEN" not in message
    assert "SUPERSECRETFAUTH" not in message
    assert "video-fa.scdn.co" in message
    # Redaction must not eat the separator and leave the message unreadable:
    # this text is the operator's only diagnostic for a failed download.
    assert "403 Client Error" in message


def test_redact_url_query_is_idempotent():
    """A message can pass more than one redaction point on its way to a log
    (the segment fetcher composes it, the caller logs it). Redacting twice
    must not stutter the marker or eat the separator after the URL."""

    once = sv._redact_url_query(f"failed for {_SIGNED_SEGMENT_URL}: timeout")
    assert once == sv._redact_url_query(once)
    assert once.endswith(": timeout")
    assert "SUPERSECRETTOKEN" not in once


def test_fetch_subtitles_logs_failure_without_signed_url(monkeypatch, caplog, tmp_path):
    """The subtitle failure path logs at WARNING; the fixture's subtitle
    template carries the same signed query parameters a live one does."""

    def fake_fetch(url, timeout=30):
        raise RuntimeError(f"503 Server Error for url: {url}")

    monkeypatch.setattr(sv, "_fetch_bytes", fake_fetch)
    monkeypatch.setattr(sv.time, "sleep", lambda seconds: None)

    with caplog.at_level(logging.WARNING):
        assert sv.fetch_subtitles(_manifest(), "pl-pl", str(tmp_path / "s.vtt")) is None

    assert "__token__=FAKE" not in caplog.text
    assert "fauth=FAKE_FAUTH" not in caplog.text
    assert "subtitles.spotifycdn.com" in caplog.text
    assert "503 Server Error" in caplog.text


def test_mux_invokes_ffmpeg_with_stream_copy(tmp_path, monkeypatch):
    calls = {}

    class _Result:
        returncode = 0
        stderr = b""

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        (tmp_path / "out.mp4").write_bytes(b"MUXED")
        return _Result()

    monkeypatch.setattr(sv.subprocess, "run", fake_run)
    out = sv.mux(str(tmp_path / "v.mp4"), str(tmp_path / "a.mp4"), str(tmp_path / "out.mp4"))
    assert out == str(tmp_path / "out.mp4")
    # Verify -c copy is present, ensuring stream copy (no re-encoding).
    # Check that "copy" appears after "-c" in the command list.
    c_index = calls["cmd"].index("-c")
    assert calls["cmd"][c_index + 1] == "copy"


def test_mux_raises_ffmpeg_missing(tmp_path, monkeypatch):
    def raise_missing(cmd, **kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(sv.subprocess, "run", raise_missing)
    with pytest.raises(sv.SpotifyVideoError) as exc:
        sv.mux(str(tmp_path / "v.mp4"), str(tmp_path / "a.mp4"), str(tmp_path / "o.mp4"))
    assert str(exc.value) == "ffmpeg_missing"


def test_mux_raises_when_ffmpeg_fails(tmp_path, monkeypatch):
    class _Result:
        returncode = 1
        stderr = b"boom"

    monkeypatch.setattr(sv.subprocess, "run", lambda cmd, **kwargs: _Result())
    with pytest.raises(sv.SpotifyVideoError):
        sv.mux(str(tmp_path / "v.mp4"), str(tmp_path / "a.mp4"), str(tmp_path / "o.mp4"))


def test_mux_raises_timeout_expired(tmp_path, monkeypatch):
    def raise_timeout(cmd, **kwargs):
        raise sv.subprocess.TimeoutExpired("ffmpeg", 600)

    monkeypatch.setattr(sv.subprocess, "run", raise_timeout)
    with pytest.raises(sv.SpotifyVideoError) as exc:
        sv.mux(str(tmp_path / "v.mp4"), str(tmp_path / "a.mp4"), str(tmp_path / "o.mp4"))
    # Bare reason code (not a descriptive sentence): the service layer maps
    # this by exact string match, same as ffmpeg_missing and every other
    # SpotifyVideoError this pipeline raises. A substring/keyword match on an
    # English sentence previously collided with unrelated network-timeout
    # messages -- see test_get_video_error_message_does_not_collide_with_
    # network_timeout in tests/test_spotify_video_service.py.
    assert str(exc.value) == "mux_timeout"
    # The original TimeoutExpired must still be chained for debugging.
    assert isinstance(exc.value.__cause__, sv.subprocess.TimeoutExpired)


def test_download_track_preserves_original_error_when_cleanup_fails(tmp_path, monkeypatch):
    """Regression: os.remove failure must not mask the original SpotifyVideoError."""
    # _fetch_with_failover backs off between its three attempts; without this
    # the test burns 3 real seconds waiting for retries that are stubbed to
    # fail instantly anyway.
    monkeypatch.setattr(sv.time, "sleep", lambda seconds: None)
    remove_calls = []

    def fake_remove(path):
        remove_calls.append(path)
        if path.endswith(".part"):
            raise OSError("Permission denied")
        # Allow other removes to succeed
        if (tmp_path / path).exists():
            import os as real_os
            real_os.remove(path)

    monkeypatch.setattr(sv.os, "remove", fake_remove)

    # Drive download_track into its failure path by making fetch fail
    def fake_fetch(url, timeout=30):
        raise sv.SpotifyVideoError("Network error")

    monkeypatch.setattr(sv, "_fetch_bytes", fake_fetch)

    # The original SpotifyVideoError should propagate, not OSError from remove
    with pytest.raises(sv.SpotifyVideoError, match="Network error"):
        sv.download_track(
            ["https://cdn/init"], [["https://cdn/0"]], str(tmp_path / "v.mp4")
        )
