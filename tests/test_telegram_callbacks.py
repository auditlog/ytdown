"""Tests for callback parsing functions."""

from bot.handlers.callback_parsing import parse_spotify_video_callback, SPOTIFY_VIDEO_HEIGHTS
from bot.handlers.common_ui import build_spotify_episode_keyboard


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


def _labels(keyboard):
    return [button.text for row in keyboard for button in row]


def test_spotify_keyboard_lists_qualities_with_sizes():
    keyboard = build_spotify_episode_keyboard(
        quality_options=[
            {"height": 1080, "profile_id": 0, "size_mb": 621.0},
            {"height": 720, "profile_id": 1, "size_mb": 336.0},
        ],
        has_native_audio=True,
        has_fallback_audio=False,
    )
    labels = _labels(keyboard)
    assert "Video 1080p (~621 MB)" in labels
    assert "Video 720p (~336 MB)" in labels


def test_spotify_keyboard_hides_fallback_audio_when_unavailable():
    keyboard = build_spotify_episode_keyboard(
        quality_options=[], has_native_audio=True, has_fallback_audio=False
    )
    labels = _labels(keyboard)
    assert "Audio (M4A) — Spotify" in labels
    assert "Audio (MP3)" not in labels
    assert "Audio (M4A)" not in labels


def test_spotify_keyboard_shows_fallback_audio_when_available():
    keyboard = build_spotify_episode_keyboard(
        quality_options=[], has_native_audio=False, has_fallback_audio=True
    )
    labels = _labels(keyboard)
    assert "Audio (MP3)" in labels
    assert "Audio (M4A) — Spotify" not in labels


def test_spotify_keyboard_always_offers_transcription():
    keyboard = build_spotify_episode_keyboard(
        quality_options=[], has_native_audio=False, has_fallback_audio=False
    )
    labels = _labels(keyboard)
    assert "Transkrypcja audio" in labels
    assert "Transkrypcja + Podsumowanie" in labels


def test_spotify_keyboard_uses_spv_callbacks():
    keyboard = build_spotify_episode_keyboard(
        quality_options=[{"height": 720, "profile_id": 1, "size_mb": 336.0}],
        has_native_audio=True,
        has_fallback_audio=False,
    )
    callbacks = [button.callback_data for row in keyboard for button in row]
    assert "spv_video_720p" in callbacks
    assert "spv_audio_m4a" in callbacks


def test_spotify_keyboard_height_callback_round_trip():
    """Verify that keyboard callbacks for all heights are accepted by the parser.

    This test covers Task 8's gap (only 720p was tested) and guards against
    accidental callback/parser drift. Height 320 is notably easy to typo as 360.
    """

    for height in SPOTIFY_VIDEO_HEIGHTS:
        # Build keyboard with this height
        keyboard = build_spotify_episode_keyboard(
            quality_options=[{"height": height, "profile_id": 0, "size_mb": 100.0}],
            has_native_audio=False,
            has_fallback_audio=False,
        )

        # Extract the video button's callback data
        callbacks = [button.callback_data for row in keyboard for button in row if "spv_video" in button.callback_data]
        assert len(callbacks) == 1, f"Expected exactly one video button for height {height}"

        callback_data = callbacks[0]
        expected_callback = f"spv_video_{height}p"
        assert callback_data == expected_callback, f"Wrong callback for height {height}"

        # Parse it back
        parsed = parse_spotify_video_callback(callback_data)
        assert parsed is not None, f"Parser rejected callback {callback_data} for height {height}"
        assert parsed["media_type"] == "video"
        assert parsed["height"] == height, f"Parser returned wrong height for {callback_data}"


def _callbacks(keyboard):
    return [button.callback_data for row in keyboard for button in row]


def test_podcast_keyboards_offer_download_and_trim():
    from bot.handlers.common_ui import build_main_keyboard, build_spotify_track_keyboard

    assert "trim_dl" in _callbacks(build_main_keyboard("castbox"))
    assert "trim_dl" in _callbacks(build_main_keyboard("spotify"))
    assert "trim_dl" in _callbacks(build_spotify_track_keyboard())
    assert "trim_dl" not in _callbacks(build_main_keyboard("youtube"))


def test_spotify_episode_offers_trim_only_with_fallback_audio():
    with_fallback = build_spotify_episode_keyboard(
        quality_options=[], has_native_audio=True, has_fallback_audio=True
    )
    native_only = build_spotify_episode_keyboard(
        quality_options=[], has_native_audio=True, has_fallback_audio=False
    )
    assert "trim_dl" in _callbacks(with_fallback)
    assert "trim_dl" not in _callbacks(native_only)
