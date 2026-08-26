"""Unit tests for bot.services.spotify_video_service."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from bot import spotify_video as sv
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
    monkeypatch.setattr(svs, "load_spotify_cookie", lambda path=None: "sp_dc_value")
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
    assert [p.height for p in episode.profiles] == [1080, 720, 480]
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


def test_get_video_error_message_covers_every_reason():
    reasons = ("no_cookie", "expired_session", "api_changed", "drm_protected", "ffmpeg_missing")
    fallback = svs.get_video_error_message("__unmapped__")
    for reason in reasons:
        message = svs.get_video_error_message(reason)
        assert message and message != reason
        assert message != fallback
    # Pins that the five codes produce five distinct messages, not one
    # message reused for all of them -- an empty _ERROR_MESSAGES map would
    # satisfy every assertion above but collapse this set to size 1.
    assert len({svs.get_video_error_message(r) for r in reasons}) == len(reasons)


def test_get_video_error_message_drm_refuses_clearly():
    assert "DRM" in svs.get_video_error_message("drm_protected")


def test_get_video_error_message_handles_ffmpeg_timeout():
    """mux() raises SpotifyVideoError(f"ffmpeg mux timeout: {N}s exceeded"),
    not a bare reason code. An exact-match-only lookup would silently
    degrade this to the generic fallback text; it must get its own message
    instead, distinguishable from both ffmpeg_missing and the fallback."""

    message = svs.get_video_error_message("ffmpeg mux timeout: 600s exceeded")
    assert message != svs.get_video_error_message("some_unmapped_reason")
    assert message != svs.get_video_error_message("ffmpeg_missing")
    assert "limit czasu" in message.lower()


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
        raise sv.SpotifyVideoError("ffmpeg mux timeout: 600s exceeded")

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
        with pytest.raises(sv.SpotifyVideoError):
            asyncio.run(
                svs.download_episode_media(
                    episode=episode,
                    height=720,
                    output_dir=str(tmp_path),
                    executor=executor,
                )
            )

    assert not out_path.exists()


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
