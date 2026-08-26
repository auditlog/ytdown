"""Unit tests for bot.services.spotify_video_service."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import requests

from bot import spotify_video as sv
from bot.spotify_video import SPOTIFY_VIDEO_HEIGHTS
from bot.services import spotify_video_service as svs

FIXTURES = Path(__file__).parent / "fixtures"


def _manifest() -> dict:
    return json.loads((FIXTURES / "spotify_manifest.json").read_text(encoding="utf-8"))


def _embed(**overrides) -> sv.EmbedData:
    values = dict(
        access_token="FAKE",
        manifest_id="cdc59c43",
        title="Testowy odcinek",
        show_name="Testowy podcast",
        duration_ms=32000,
        has_video=True,
        requires_drm=False,
    )
    values.update(overrides)
    return sv.EmbedData(**values)


def test_resolve_video_episode_returns_none_for_non_episode_url(monkeypatch):
    """A non-episode URL must be rejected by the parse, before any IO. The
    fetch stub is what proves that: without it, a regression that dropped the
    early return would issue a real request to open.spotify.com on its way to
    failing."""

    def must_not_fetch(*args, **kwargs):
        pytest.fail("resolve_video_episode must not fetch for a non-episode URL")

    monkeypatch.setattr(svs, "load_spotify_cookie", lambda path=None: "sp_dc_value")
    monkeypatch.setattr(svs, "fetch_embed_data", must_not_fetch)

    assert svs.resolve_video_episode("https://example.com/foo") is None


def test_resolve_video_episode_raises_without_cookie(monkeypatch):
    monkeypatch.setattr(svs, "load_spotify_cookie", lambda path=None: None)
    with pytest.raises(sv.SpotifyVideoError) as exc:
        svs.resolve_video_episode("https://open.spotify.com/episode/25NlRLSIHjtfU47zm4FGm4")
    assert str(exc.value) == "no_cookie"


def test_resolve_video_episode_returns_none_for_audio_only(monkeypatch):
    monkeypatch.setattr(svs, "load_spotify_cookie", lambda path=None: "sp_dc_value")
    monkeypatch.setattr(svs, "fetch_embed_data", lambda eid, cookie: _embed(has_video=False))
    assert svs.resolve_video_episode("https://open.spotify.com/episode/25NlRLSIHjtfU47zm4FGm4") is None


def test_resolve_video_episode_refuses_drm_protected(monkeypatch):
    monkeypatch.setattr(svs, "load_spotify_cookie", lambda path=None: "sp_dc_value")
    monkeypatch.setattr(svs, "fetch_embed_data", lambda eid, cookie: _embed(requires_drm=True))

    def must_not_be_reached(mid, token):
        # DRM refusal must happen before any manifest fetch is attempted --
        # both because a real call here would hit the network (forbidden in
        # this suite) and because it proves the hard DRM boundary rather
        # than just the resulting reason code.
        raise AssertionError("fetch_video_manifest must not be reached for DRM-protected episodes")

    monkeypatch.setattr(svs, "fetch_video_manifest", must_not_be_reached)

    with pytest.raises(sv.SpotifyVideoError) as exc:
        svs.resolve_video_episode("https://open.spotify.com/episode/25NlRLSIHjtfU47zm4FGm4")
    assert str(exc.value) == "drm_protected"


def test_resolve_video_episode_builds_episode(monkeypatch):
    monkeypatch.setattr(svs, "load_spotify_cookie", lambda path=None: "sp_dc_value")
    monkeypatch.setattr(svs, "fetch_embed_data", lambda eid, cookie: _embed())
    monkeypatch.setattr(svs, "fetch_video_manifest", lambda mid, token: _manifest())

    episode = svs.resolve_video_episode(
        "https://open.spotify.com/episode/25NlRLSIHjtfU47zm4FGm4"
    )
    assert episode.title == "Testowy odcinek"
    # list_profiles reports every H.264 rendition the manifest carries,
    # including the 240p one the keyboard deliberately does not expose --
    # the exclusion belongs to build_quality_options, not here.
    assert [p.height for p in episode.profiles] == [1080, 720, 480, 240]
    assert episode.subtitle_languages == ["pl-pl"]


def test_resolve_video_episode_normalizes_embed_shape_change(monkeypatch):
    """fetch_embed_data raises a bare SpotifyVideoError (not a requests
    exception) with a full sentence when the embed page's __NEXT_DATA__ blob
    is missing. resolve_video_episode must normalize that to the bare
    "api_changed" reason code, or get_video_error_message silently falls back
    to the generic message instead of the specific "API changed" text."""

    monkeypatch.setattr(svs, "load_spotify_cookie", lambda path=None: "sp_dc_value")

    def raise_shape_changed(eid, cookie):
        raise sv.SpotifyVideoError(
            "Spotify embed page did not contain the expected __NEXT_DATA__ blob"
        )

    monkeypatch.setattr(svs, "fetch_embed_data", raise_shape_changed)

    with pytest.raises(sv.SpotifyVideoError) as exc:
        svs.resolve_video_episode("https://open.spotify.com/episode/25NlRLSIHjtfU47zm4FGm4")
    assert str(exc.value) == "api_changed"


def test_resolve_video_episode_normalizes_manifest_shape_change(monkeypatch):
    """fetch_video_manifest raises a bare SpotifyVideoError (not
    requests.HTTPError) with a full sentence on a 404 from the v6 endpoint.
    resolve_video_episode must normalize that to "api_changed" too."""

    monkeypatch.setattr(svs, "load_spotify_cookie", lambda path=None: "sp_dc_value")
    monkeypatch.setattr(svs, "fetch_embed_data", lambda eid, cookie: _embed())

    def raise_404(mid, token):
        raise sv.SpotifyVideoError(
            "Spotify manifest endpoint v6 returned 404 — the API shape changed"
        )

    monkeypatch.setattr(svs, "fetch_video_manifest", raise_404)

    with pytest.raises(sv.SpotifyVideoError) as exc:
        svs.resolve_video_episode("https://open.spotify.com/episode/25NlRLSIHjtfU47zm4FGm4")
    assert str(exc.value) == "api_changed"


def test_resolve_video_episode_normalizes_missing_manifest_contents(monkeypatch):
    """list_profiles / manifest_duration_ms / subtitle_languages all route
    through _manifest_content, which raises a bare SpotifyVideoError with a
    full sentence ("Spotify manifest carries no contents entry") when the
    manifest is missing its "contents" key. This is the same "API shape
    changed" scenario as a 404 from fetch_video_manifest, just discovered
    one step later -- resolve_video_episode must normalize it too."""

    monkeypatch.setattr(svs, "load_spotify_cookie", lambda path=None: "sp_dc_value")
    monkeypatch.setattr(svs, "fetch_embed_data", lambda eid, cookie: _embed())
    monkeypatch.setattr(svs, "fetch_video_manifest", lambda mid, token: {"base_urls": []})

    with pytest.raises(sv.SpotifyVideoError) as exc:
        svs.resolve_video_episode("https://open.spotify.com/episode/25NlRLSIHjtfU47zm4FGm4")
    assert str(exc.value) == "api_changed"


def test_resolve_video_episode_normalizes_profile_without_id(monkeypatch):
    """A profile entry missing "id" used to raise a bare KeyError out of
    list_profiles, straight past this function's SpotifyVideoError handling
    and out of the handler entirely. Manifest drift must always arrive as
    the "api_changed" reason code, whatever shape Spotify's drift takes."""

    manifest = _manifest()
    manifest["contents"][0]["profiles"] = [
        {"file_type": "mp4", "max_bitrate": 1, "video_codec": "avc1.4d401f",
         "video_height": 480, "video_width": 854},
    ]

    monkeypatch.setattr(svs, "load_spotify_cookie", lambda path=None: "sp_dc_value")
    monkeypatch.setattr(svs, "fetch_embed_data", lambda eid, cookie: _embed())
    monkeypatch.setattr(svs, "fetch_video_manifest", lambda mid, token: manifest)

    with pytest.raises(sv.SpotifyVideoError) as exc:
        svs.resolve_video_episode("https://open.spotify.com/episode/25NlRLSIHjtfU47zm4FGm4")
    assert str(exc.value) == "api_changed"


def test_resolve_video_episode_normalizes_non_numeric_duration(monkeypatch):
    """parse_embed_html raises SpotifyVideoError (it used to raise a raw
    ValueError) when Spotify hands back a duration that is not a number.
    fetch_embed_data propagates it unchanged, so resolve_video_episode has
    to normalize it like every other embed-shape change."""

    monkeypatch.setattr(svs, "load_spotify_cookie", lambda path=None: "sp_dc_value")

    def raise_bad_duration(eid, cookie):
        raise sv.SpotifyVideoError(
            "Spotify data field entity.duration is not a number: '40 min'"
        )

    monkeypatch.setattr(svs, "fetch_embed_data", raise_bad_duration)

    with pytest.raises(sv.SpotifyVideoError) as exc:
        svs.resolve_video_episode("https://open.spotify.com/episode/25NlRLSIHjtfU47zm4FGm4")
    assert str(exc.value) == "api_changed"


def test_download_episode_media_normalizes_missing_initialization_template(tmp_path):
    """build_track_urls read manifest["initialization_template"] as a bare
    subscript, so a manifest without it raised KeyError past every layer of
    error mapping. Same drift, same "api_changed" reason code."""

    manifest = _manifest()
    del manifest["initialization_template"]

    episode = svs.VideoEpisode(
        episode_id="abc123",
        title="Testowy odcinek",
        show_name="Testowy podcast",
        duration_ms=32000,
        manifest=manifest,
        profiles=sv.list_profiles(manifest),
        subtitle_languages=[],
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(sv.SpotifyVideoError) as exc:
            asyncio.run(
                svs.download_episode_media(
                    episode=episode,
                    height=720,
                    output_dir=str(tmp_path),
                    executor=executor,
                )
            )

    assert str(exc.value) == "api_changed"


def test_resolve_video_episode_reports_expired_session_for_empty_token(monkeypatch):
    """An sp_dc cookie that has expired still renders the embed page, but the
    token it carries comes back empty. That is the failure users hit most
    often, and it must reach them as the "export the cookies again" message
    rather than as a manifest fetch that fails for a mysterious reason."""

    monkeypatch.setattr(svs, "load_spotify_cookie", lambda path=None: "sp_dc_value")
    monkeypatch.setattr(svs, "fetch_embed_data", lambda eid, cookie: _embed(access_token=""))

    def must_not_be_reached(mid, token):
        raise AssertionError("fetch_video_manifest must not be called without a token")

    monkeypatch.setattr(svs, "fetch_video_manifest", must_not_be_reached)

    with pytest.raises(sv.SpotifyVideoError) as exc:
        svs.resolve_video_episode("https://open.spotify.com/episode/25NlRLSIHjtfU47zm4FGm4")
    assert str(exc.value) == "expired_session"


@pytest.mark.parametrize("status", [401, 403])
def test_resolve_video_episode_maps_rejected_token_to_expired_session(monkeypatch, status):
    """Spotify answers a stale token with 401 or 403 (design spec 10). Both
    mean the same thing to the user: re-export the cookies."""

    monkeypatch.setattr(svs, "load_spotify_cookie", lambda path=None: "sp_dc_value")
    monkeypatch.setattr(svs, "fetch_embed_data", lambda eid, cookie: _embed())

    def raise_http_error(mid, token):
        response = requests.Response()
        response.status_code = status
        raise requests.HTTPError(f"{status} Client Error", response=response)

    monkeypatch.setattr(svs, "fetch_video_manifest", raise_http_error)

    with pytest.raises(sv.SpotifyVideoError) as exc:
        svs.resolve_video_episode("https://open.spotify.com/episode/25NlRLSIHjtfU47zm4FGm4")
    assert str(exc.value) == "expired_session"


def test_resolve_video_episode_maps_other_http_errors_to_api_changed(monkeypatch):
    """Only 401/403 mean "your session expired". A 500 is not the user's
    cookie jar, and telling them to re-export it would send them chasing a
    problem they cannot fix."""

    monkeypatch.setattr(svs, "load_spotify_cookie", lambda path=None: "sp_dc_value")
    monkeypatch.setattr(svs, "fetch_embed_data", lambda eid, cookie: _embed())

    def raise_http_error(mid, token):
        response = requests.Response()
        response.status_code = 500
        raise requests.HTTPError("500 Server Error", response=response)

    monkeypatch.setattr(svs, "fetch_video_manifest", raise_http_error)

    with pytest.raises(sv.SpotifyVideoError) as exc:
        svs.resolve_video_episode("https://open.spotify.com/episode/25NlRLSIHjtfU47zm4FGm4")
    assert str(exc.value) == "api_changed"


def test_get_video_error_message_covers_every_reason():
    reasons = (
        "no_cookie", "expired_session", "api_changed", "drm_protected",
        "ffmpeg_missing", "mux_timeout", "download_failed",
    )
    fallback = svs.get_video_error_message("__unmapped__")
    for reason in reasons:
        message = svs.get_video_error_message(reason)
        assert message and message != reason
        assert message != fallback
    # Pins that the codes produce that many distinct messages, not one
    # message reused for all of them -- an empty _ERROR_MESSAGES map would
    # satisfy every assertion above but collapse this set to size 1.
    assert len({svs.get_video_error_message(r) for r in reasons}) == len(reasons)


def test_get_video_error_message_drm_refuses_clearly():
    assert "DRM" in svs.get_video_error_message("drm_protected")


def test_get_video_error_message_mux_timeout_is_distinct_from_ffmpeg_missing():
    """mux() raises the bare code "mux_timeout" (not a descriptive sentence,
    and not the same code as "ffmpeg_missing") when ffmpeg itself times out.
    The causes and the right user advice differ, so the messages must too."""

    message = svs.get_video_error_message("mux_timeout")
    assert message != svs.get_video_error_message("ffmpeg_missing")
    assert "limit czasu" in message.lower()


def test_get_video_error_message_does_not_collide_with_network_timeout():
    """Regression for a real defect: get_video_error_message used to match
    the substring "timeout" against any reason string. download_track wraps
    a stalled segment download into SpotifyVideoError("Segment download
    failed after N attempts: ... read timeout=30 ..."), whose text also
    contains "timeout" -- so a user whose network stalled mid-download was
    told ffmpeg exceeded its time limit, a wrong and confusing message
    about a component that never ran. Now that mux_timeout is an exact-match
    reason code, an unrelated message merely containing "timeout" must fall
    through to the generic fallback, not the mux-specific message."""

    network_stall_message = (
        "Segment download failed after 3 attempts; last error from "
        "https://video-fa.scdn.co/segments/0.mp4: HTTPSConnectionPool"
        "(host='video-fa.scdn.co', port=443): Read timed out. (read timeout=30)"
    )

    result = svs.get_video_error_message(network_stall_message)

    assert result != svs.get_video_error_message("mux_timeout")
    assert result == svs.get_video_error_message("__totally_unmapped__")


def test_get_download_error_message_names_the_download_for_unmapped_failures():
    """A failed segment fetch arrives as a descriptive English sentence, not
    a reason code. At the download stage that always means the download
    failed, which is more than the catch-all ever said."""

    segment_error = (
        "Segment download failed after 3 attempts; last error from "
        "https://video-fa.scdn.co/segments/0.mp4?[redacted]: Read timed out."
    )

    message = svs.get_download_error_message(segment_error)

    assert message == svs.get_video_error_message("download_failed")
    assert message != svs.get_video_error_message("__unmapped__")
    # No English internals, no signed URL, in what the user reads.
    assert "Segment download failed" not in message
    assert "http" not in message


def test_get_download_error_message_keeps_mapped_codes_specific():
    for reason in ("ffmpeg_missing", "mux_timeout", "drm_protected"):
        assert svs.get_download_error_message(reason) == svs.get_video_error_message(reason)


def test_download_episode_media_creates_missing_output_dir(monkeypatch, tmp_path):
    """download_track requires its destination directory to already exist;
    download_episode_media must create it first (see Task 5/6 review note on
    an unwrapped OSError otherwise reaching the user)."""

    def fake_download_track(init_urls, segment_urls, dest_path, **kwargs):
        # A real download_track would raise FileNotFoundError here too if the
        # parent directory is missing, so this stub only proves the point if
        # download_episode_media has already created it.
        Path(dest_path).write_bytes(b"audio-bytes")
        return dest_path

    monkeypatch.setattr(svs, "download_track", fake_download_track)

    episode = svs.VideoEpisode(
        episode_id="abc123",
        title="Testowy odcinek",
        show_name="Testowy podcast",
        duration_ms=32000,
        manifest=_manifest(),
        profiles=[],
        subtitle_languages=[],
    )

    missing_dir = tmp_path / "nested" / "not-created-yet"
    assert not missing_dir.exists()

    with ThreadPoolExecutor(max_workers=1) as executor:
        result = asyncio.run(
            svs.download_episode_media(
                episode=episode,
                height=None,
                output_dir=str(missing_dir),
                executor=executor,
            )
        )

    assert Path(result).read_bytes() == b"audio-bytes"


def test_download_episode_media_video_muxes_and_cleans_temp_files(monkeypatch, tmp_path):
    written_tracks = []

    def fake_download_track(init_urls, segment_urls, dest_path, **kwargs):
        Path(dest_path).write_bytes(b"track-bytes")
        written_tracks.append(dest_path)
        return dest_path

    def fake_mux(video_path, audio_path, out_path):
        # A genuine mux would fail without both inputs present; assert here
        # so a caller that mixed up paths or skipped a download would fail.
        assert Path(video_path).exists()
        assert Path(audio_path).exists()
        Path(out_path).write_bytes(b"muxed-bytes")
        return out_path

    monkeypatch.setattr(svs, "download_track", fake_download_track)
    monkeypatch.setattr(svs, "mux", fake_mux)

    manifest = _manifest()
    episode = svs.VideoEpisode(
        episode_id="abc123",
        title="Testowy odcinek",
        show_name="Testowy podcast",
        duration_ms=32000,
        manifest=manifest,
        profiles=sv.list_profiles(manifest),
        subtitle_languages=sv.subtitle_languages(manifest),
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        result = asyncio.run(
            svs.download_episode_media(
                episode=episode,
                height=720,
                output_dir=str(tmp_path),
                executor=executor,
            )
        )

    assert Path(result).read_bytes() == b"muxed-bytes"
    assert Path(result).name.endswith(".mp4")
    # The video/audio track files are intermediate scratch — a successful mux
    # must remove them, not leave them sitting alongside the final output.
    for temp_path in written_tracks:
        assert not Path(temp_path).exists()


def test_download_episode_media_normalizes_manifest_shape_change(tmp_path):
    """find_audio_profile_id raises a bare SpotifyVideoError with a full
    sentence when the manifest carries no AAC profile -- the same "API
    shape changed" scenario resolve_video_episode normalizes, but reachable
    from download_episode_media too (e.g. a manifest resolved earlier and
    cached, then replayed against a shape Spotify has since changed)."""

    episode = svs.VideoEpisode(
        episode_id="abc123",
        title="Testowy odcinek",
        show_name="Testowy podcast",
        duration_ms=32000,
        manifest={"contents": [{"profiles": []}]},  # no audio_codec anywhere
        profiles=[],
        subtitle_languages=[],
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(sv.SpotifyVideoError) as exc:
            asyncio.run(
                svs.download_episode_media(
                    episode=episode,
                    height=None,
                    output_dir=str(tmp_path),
                    executor=executor,
                )
            )

    assert str(exc.value) == "api_changed"


def test_download_episode_media_removes_truncated_output_on_mux_failure(monkeypatch, tmp_path):
    """ffmpeg runs with -y and writes out_path incrementally; a mid-mux
    failure (e.g. a timeout) must not leave a truncated {title}.mp4 sitting
    in the user's download directory until the 24h cleanup sweep."""

    def fake_download_track(init_urls, segment_urls, dest_path, **kwargs):
        Path(dest_path).write_bytes(b"track-bytes")
        return dest_path

    def fake_mux(video_path, audio_path, out_path):
        # Simulate ffmpeg having written a partial file before the timeout.
        Path(out_path).write_bytes(b"truncated")
        raise sv.SpotifyVideoError("mux_timeout")

    monkeypatch.setattr(svs, "download_track", fake_download_track)
    monkeypatch.setattr(svs, "mux", fake_mux)

    manifest = _manifest()
    episode = svs.VideoEpisode(
        episode_id="abc123",
        title="Testowy odcinek",
        show_name="Testowy podcast",
        duration_ms=32000,
        manifest=manifest,
        profiles=sv.list_profiles(manifest),
        subtitle_languages=sv.subtitle_languages(manifest),
    )

    out_path = Path(tmp_path) / "Testowy odcinek.mp4"

    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(sv.SpotifyVideoError) as exc:
            asyncio.run(
                svs.download_episode_media(
                    episode=episode,
                    height=720,
                    output_dir=str(tmp_path),
                    executor=executor,
                )
            )

    assert not out_path.exists()
    # Pins that a mux failure keeps its own identity rather than being
    # rewritten to "api_changed" by download_episode_media's manifest-shape
    # normalization -- without this, an over-broad try around the mux call
    # would leave this test green while silently erasing the distinction
    # the narrow wrapping (see item 2, fix round 1) was built to preserve.
    assert str(exc.value) == "mux_timeout"


def test_build_quality_options_describes_profiles_with_estimated_sizes():
    manifest = _manifest()
    profiles = sv.list_profiles(manifest)
    episode = svs.VideoEpisode(
        episode_id="abc123",
        title="Testowy odcinek",
        show_name="Testowy podcast",
        duration_ms=sv.manifest_duration_ms(manifest),
        manifest=manifest,
        profiles=profiles,
        subtitle_languages=sv.subtitle_languages(manifest),
    )

    options = svs.build_quality_options(episode)

    assert [opt["height"] for opt in options] == [1080, 720, 480]
    assert [opt["profile_id"] for opt in options] == [0, 1, 2]
    for opt in options:
        assert set(opt.keys()) == {"height", "profile_id", "size_mb"}
        assert opt["size_mb"] > 0
    # A higher-resolution profile carries a higher max_bitrate in the
    # fixture, so its estimated size must not come out smaller.
    sizes = [opt["size_mb"] for opt in options]
    assert sizes == sorted(sizes, reverse=True)


def test_build_quality_options_excludes_heights_the_parser_rejects():
    """The real manifest carries 426x240 and 320x180 H.264 profiles (design
    spec 3.4). Neither is exposed (spec 6.1), and parse_spotify_video_callback
    rejects any height outside SPOTIFY_VIDEO_HEIGHTS -- so offering a button
    for them would hand the user a button that answers "Nieobsluzony format".
    A button that cannot work is never shown."""

    manifest = _manifest()
    profiles = sv.list_profiles(manifest)
    # The fixture must actually carry the excluded profile, or this test
    # passes for the wrong reason.
    assert 240 in [p.height for p in profiles]

    episode = svs.VideoEpisode(
        episode_id="abc123",
        title="Testowy odcinek",
        show_name="Testowy podcast",
        duration_ms=sv.manifest_duration_ms(manifest),
        manifest=manifest,
        profiles=profiles,
        subtitle_languages=sv.subtitle_languages(manifest),
    )

    heights = [opt["height"] for opt in svs.build_quality_options(episode)]

    assert 240 not in heights
    assert set(heights) <= set(SPOTIFY_VIDEO_HEIGHTS)


# --- transcript_from_subtitles (Task 12) ------------------------------------


def test_transcript_from_subtitles_produces_markdown(monkeypatch, tmp_path):
    episode = svs.VideoEpisode(
        episode_id="abc",
        title="Odcinek",
        show_name="Podcast",
        duration_ms=32000,
        manifest=_manifest(),
        profiles=[],
        subtitle_languages=["pl-pl"],
    )

    def fake_fetch(manifest, language_code, dest_path):
        Path(dest_path).write_text(
            "WEBVTT\n\n00:00:00.350 --> 00:00:03.950\nPierwsza linia\n\n"
            "00:00:03.950 --> 00:00:08.710\nDruga linia\n",
            encoding="utf-8",
        )
        return dest_path

    monkeypatch.setattr(svs, "fetch_subtitles", fake_fetch)

    path = svs.transcript_from_subtitles(
        episode=episode, output_dir=str(tmp_path), sanitized_title="odcinek"
    )
    content = Path(path).read_text(encoding="utf-8")
    assert "Pierwsza linia" in content
    assert "Druga linia" in content
    assert "-->" not in content, "timestamps must be stripped"
    # The intermediate .vtt scratch file must not survive delivery -- only
    # the finished markdown transcript should remain in output_dir.
    leftover_vtt = list(tmp_path.glob("*.vtt"))
    assert leftover_vtt == []


def test_transcript_from_subtitles_returns_none_without_subtitles(monkeypatch, tmp_path):
    def must_not_be_reached(manifest, language_code, dest_path):
        # An episode advertising no subtitle languages must never attempt a
        # fetch at all -- proves the guard clause, not just the outcome.
        raise AssertionError("fetch_subtitles must not be reached without subtitle_languages")

    monkeypatch.setattr(svs, "fetch_subtitles", must_not_be_reached)

    episode = svs.VideoEpisode(
        episode_id="abc", title="Odcinek", show_name="Podcast",
        duration_ms=32000, manifest={}, profiles=[], subtitle_languages=[],
    )
    assert svs.transcript_from_subtitles(
        episode=episode, output_dir=str(tmp_path), sanitized_title="odcinek"
    ) is None


def test_transcript_from_subtitles_returns_none_when_fetch_unavailable(monkeypatch, tmp_path):
    """subtitle_languages can be non-empty while Spotify still fails to serve
    the actual track (stale manifest, expired CDN URL, transient failure).
    fetch_subtitles already returns None rather than raising for this --
    transcript_from_subtitles must propagate that as None too, so its
    caller can fall back to the Groq audio pipeline instead of crashing."""

    episode = svs.VideoEpisode(
        episode_id="abc", title="Odcinek", show_name="Podcast",
        duration_ms=32000, manifest=_manifest(), profiles=[],
        subtitle_languages=["pl-pl"],
    )

    monkeypatch.setattr(svs, "fetch_subtitles", lambda manifest, language_code, dest_path: None)

    result = svs.transcript_from_subtitles(
        episode=episode, output_dir=str(tmp_path), sanitized_title="odcinek"
    )
    assert result is None
    # No markdown artifact must be left behind when there is no transcript.
    assert list(tmp_path.glob("*.md")) == []


def test_transcript_from_subtitles_uses_first_subtitle_language(monkeypatch, tmp_path):
    captured = {}

    def fake_fetch(manifest, language_code, dest_path):
        captured["language_code"] = language_code
        Path(dest_path).write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello\n", encoding="utf-8")
        return dest_path

    monkeypatch.setattr(svs, "fetch_subtitles", fake_fetch)

    episode = svs.VideoEpisode(
        episode_id="abc", title="Odcinek", show_name="Podcast",
        duration_ms=32000, manifest=_manifest(), profiles=[],
        subtitle_languages=["pl-pl", "en"],
    )
    svs.transcript_from_subtitles(episode=episode, output_dir=str(tmp_path), sanitized_title="odcinek")

    assert captured["language_code"] == "pl-pl"


def test_download_episode_media_unknown_height_raises(tmp_path):
    manifest = _manifest()
    episode = svs.VideoEpisode(
        episode_id="abc123",
        title="Testowy odcinek",
        show_name="Testowy podcast",
        duration_ms=32000,
        manifest=manifest,
        profiles=sv.list_profiles(manifest),
        subtitle_languages=sv.subtitle_languages(manifest),
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(sv.SpotifyVideoError) as exc:
            asyncio.run(
                svs.download_episode_media(
                    episode=episode,
                    height=4321,
                    output_dir=str(tmp_path),
                    executor=executor,
                )
            )

    assert str(exc.value) == "api_changed"
