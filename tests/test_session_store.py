"""Tests for the central session store."""

from bot.session_store import (
    SessionStore,
    download_progress,
    session_store,
    user_playlist_data,
    user_time_ranges,
    user_urls,
)
from bot.session_context import clear_transient_flow_state
from bot.runtime import AppRuntime
from bot.session_store import SecurityStore
from unittest.mock import Mock


def test_session_store_updates_and_clears_fields():
    store = SessionStore()

    store.set_field(123, "current_url", "https://youtube.com/watch?v=abc")
    store.set_field(123, "time_range", {"start": "0:10", "end": "0:20"})

    assert store.get_field(123, "current_url") == "https://youtube.com/watch?v=abc"
    assert store.get_field(123, "time_range") == {"start": "0:10", "end": "0:20"}

    assert store.pop_field(123, "current_url") == "https://youtube.com/watch?v=abc"
    assert store.get_field(123, "current_url") is None

    store.clear_session(123)
    assert store.get_field(123, "time_range") is None


def test_session_store_can_clear_multiple_fields_at_once():
    store = SessionStore()

    store.update_session(
        123,
        current_url="https://youtube.com/watch?v=abc",
        time_range={"start": "0:10", "end": "0:20"},
        platform="youtube",
    )

    store.clear_fields(123, "current_url", "time_range")

    assert store.get_field(123, "current_url") is None
    assert store.get_field(123, "time_range") is None
    assert store.get_field(123, "platform") == "youtube"

    store.clear_fields(123, "platform")
    assert store.get_field(123, "platform") is None


def test_field_maps_proxy_shared_session_store():
    session_store.reset()

    user_urls[1] = "https://youtube.com/watch?v=abc"
    user_time_ranges[1] = {"start": "0:30", "end": "1:00"}
    user_playlist_data[1] = {"title": "Playlist", "entries": []}
    download_progress[1] = {"status": "downloading"}

    assert session_store.get_field(1, "current_url") == "https://youtube.com/watch?v=abc"
    assert session_store.get_field(1, "time_range") == {"start": "0:30", "end": "1:00"}
    assert session_store.get_field(1, "playlist_data") == {"title": "Playlist", "entries": []}
    assert session_store.get_field(1, "download_progress") == {"status": "downloading"}

    assert user_urls.pop(1) == "https://youtube.com/watch?v=abc"
    assert 1 not in user_urls

    user_time_ranges.clear()
    user_playlist_data.clear()
    download_progress.clear()
    assert len(user_time_ranges) == 0
    assert len(user_playlist_data) == 0
    assert len(download_progress) == 0


def test_clear_transient_flow_state_clears_runtime_session_fields():
    context = Mock()
    context.user_data = {
        "platform": "spotify",
        "spotify_resolved": {"title": "x"},
        "ig_carousel": {"photos": []},
        "audio_file_path": "/tmp/test.mp3",
        "audio_file_title": "Recording",
        "subtitle_pending": {"lang": "pl"},
    }
    runtime = AppRuntime(
        config={},
        session_store=SessionStore(),
        security_store=SecurityStore(),
        services=Mock(),
        authorized_users_repository=Mock(),
        download_history_repository=Mock(),
        authorized_users_set=set(),
    )
    context.application = Mock()
    context.application.bot_data = {"app_runtime": runtime}

    runtime.session_store.update_session(
        55,
        current_url="https://youtube.com/watch?v=abc",
        time_range={"start": "0:10", "end": "0:20"},
        playlist_data={"entries": []},
        platform="spotify",
        spotify_resolved={"title": "Episode"},
        instagram_carousel={"photos": []},
        audio_file_path="/tmp/test.mp3",
        audio_file_title="Recording",
        subtitle_pending={"lang": "pl"},
    )

    clear_transient_flow_state(
        context,
        55,
        user_urls=user_urls,
        user_time_ranges=user_time_ranges,
        user_playlist_data=user_playlist_data,
    )

    assert runtime.session_store.get_field(55, "current_url") is None
    assert runtime.session_store.get_field(55, "time_range") is None
    assert runtime.session_store.get_field(55, "playlist_data") is None
    assert runtime.session_store.get_field(55, "platform") is None
    assert runtime.session_store.get_field(55, "spotify_resolved") is None
    assert runtime.session_store.get_field(55, "instagram_carousel") is None
    assert runtime.session_store.get_field(55, "audio_file_path") is None
    assert runtime.session_store.get_field(55, "audio_file_title") is None
    assert runtime.session_store.get_field(55, "subtitle_pending") is None
    assert context.user_data == {}
