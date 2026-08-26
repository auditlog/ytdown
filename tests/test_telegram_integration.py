"""Focused integration tests for Telegram command/callback module boundaries."""

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock, Mock

import pytest

from bot import telegram_callbacks as callbacks
from bot import telegram_commands as commands
from bot.handlers import time_range_callbacks as _trc
from tests.telegram_callbacks_support import _attach_runtime as _attach_callback_runtime
from tests.telegram_callbacks_support import _make_context as _make_callback_context
from tests.telegram_callbacks_support import _make_message_update
from tests.telegram_callbacks_support import _make_update as _make_callback_update
from tests.telegram_commands_support import (
    _attach_runtime,
    _make_context as _make_command_context,
    _make_update as _make_command_update,
)


@pytest.mark.integration
def test_runtime_auth_flow_continues_pending_url_after_successful_pin(monkeypatch):
    update = _make_command_update(text="12345678", user_id=222, chat_id=222)
    context = _make_command_context()
    runtime = _attach_runtime(context, authorized_users=set())
    runtime.session_store.set_field(222, "awaiting_pin", True)
    runtime.session_store.set_field(
        222,
        "pending_action",
        {"kind": "url", "payload": "https://youtube.com/watch?v=abc"},
    )

    monkeypatch.setattr(commands, "get_runtime_value", lambda key, default=None: "12345678" if key == "PIN_CODE" else default)
    monkeypatch.setattr(commands, "failed_attempts", defaultdict(int))

    resumed = {}

    async def fake_process_youtube_link(update_arg, context_arg, url):
        resumed["url"] = url

    monkeypatch.setattr(commands, "process_youtube_link", fake_process_youtube_link)

    handled = asyncio.run(commands.handle_pin(update, context))

    assert handled is True
    assert 222 in runtime.authorized_users_set
    assert resumed["url"] == "https://youtube.com/watch?v=abc"
    assert runtime.session_store.get_field(222, "awaiting_pin") is None
    assert runtime.session_store.get_field(222, "pending_url") is None


@pytest.mark.integration
def test_callback_time_range_preset_updates_session_and_returns_to_menu(monkeypatch):
    chat_id = 333
    url = "https://www.youtube.com/watch?v=abc"
    update = _make_callback_update("time_range_preset_first_10", chat_id=chat_id)
    context = _make_callback_context()
    runtime = _attach_runtime(context, authorized_users=set())
    runtime.session_store.set_field(chat_id, "current_url", url)

    monkeypatch.setattr(_trc, "get_video_info", lambda *_: {"duration": 900, "title": "Sample"})

    returned = {}

    async def fake_back_to_main_menu(update_arg, context_arg, back_url):
        returned["url"] = back_url

    monkeypatch.setattr(_trc, "back_to_main_menu", fake_back_to_main_menu)

    asyncio.run(callbacks.handle_callback(update, context))

    assert runtime.session_store.get_field(chat_id, "time_range") == {
        "start": "0:00",
        "end": "10:00",
        "start_sec": 0,
        "end_sec": 600,
    }
    assert returned["url"] == url


@pytest.mark.integration
def test_callback_spotify_summary_route_uses_runtime_session_state(monkeypatch):
    chat_id = 444
    update = _make_callback_update("summary_option_3", chat_id=chat_id)
    context = _make_callback_context()
    runtime = _attach_runtime(context, authorized_users=set())
    runtime.session_store.set_field(chat_id, "current_url", "https://open.spotify.com/episode/test")
    runtime.session_store.set_field(chat_id, "platform", "spotify")
    runtime.session_store.set_field(
        chat_id,
        "spotify_resolved",
        {"source": "youtube", "youtube_url": "https://youtube.com/watch?v=xyz", "title": "Episode"},
    )

    called = {}

    async def fake_download_spotify_resolved(update_arg, context_arg, resolved, fmt, **kwargs):
        called["resolved"] = resolved
        called["fmt"] = fmt
        called["summary"] = kwargs.get("summary")
        called["summary_type"] = kwargs.get("summary_type")

    monkeypatch.setattr(callbacks, "download_spotify_resolved", fake_download_spotify_resolved)

    asyncio.run(callbacks.handle_callback(update, context))

    assert called["resolved"]["title"] == "Episode"
    assert called["fmt"] == "mp3"
    assert called["summary"] is True
    assert called["summary_type"] == 3


def _callback_data(markup):
    """Flatten an InlineKeyboardMarkup into its list of callback_data strings."""
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def _progress_edit(update):
    """Return the (text, kwargs) the handler's progress message was edited with."""
    progress_message = update.message.reply_text.return_value
    await_args = progress_message.edit_text.await_args
    return await_args.args[0], await_args.kwargs


@pytest.mark.integration
def test_process_spotify_episode_video_only_stores_session_and_offers_qualities(monkeypatch):
    from bot.handlers import inbound_media as im
    from bot.services import spotify_video_service as svs

    chat_id = 501
    episode = svs.VideoEpisode(
        episode_id="abc",
        title="Testowy odcinek",
        show_name="Testowy podcast",
        duration_ms=32000,
        manifest={"base_urls": []},
        profiles=[],
        subtitle_languages=["pl-pl"],
    )
    monkeypatch.setattr(im, "resolve_video_episode", lambda url: episode)
    monkeypatch.setattr(
        im,
        "build_quality_options",
        lambda ep: [{"height": 720, "profile_id": 1, "size_mb": 336.0}],
    )

    async def fake_resolve_episode(url):
        return None

    monkeypatch.setattr(im, "resolve_episode", fake_resolve_episode)

    update = _make_message_update(chat_id=chat_id)
    context = _make_callback_context()
    runtime = _attach_callback_runtime(context)

    asyncio.run(
        im.extracted_process_spotify_episode(
            update, context, "https://open.spotify.com/episode/abc"
        )
    )

    # The session field is what Task 11/12 read back to drive the actual
    # download -- must carry exactly these keys, no "profiles" (not
    # JSON-friendly, recomputed from the manifest downstream).
    stored = runtime.session_store.get_field(chat_id, "spotify_video")
    assert stored == {
        "episode_id": "abc",
        "title": "Testowy odcinek",
        "show_name": "Testowy podcast",
        "duration_ms": 32000,
        "manifest": {"base_urls": []},
        "subtitle_languages": ["pl-pl"],
    }

    text, kwargs = _progress_edit(update)
    callback_data = _callback_data(kwargs["reply_markup"])

    # Native video and native audio buttons are offered...
    assert "spv_video_720p" in callback_data
    assert "spv_audio_m4a" in callback_data
    # ...but the legacy fallback buttons must not appear: the iTunes/YouTube
    # path never resolved anything here.
    assert "dl_audio_mp3" not in callback_data
    assert "dl_audio_m4a" not in callback_data
    assert "Testowy odcinek" in text
    assert "Podcast: Testowy podcast" in text
    assert "Czas trwania: 0:32" in text
    assert "Źródło: Spotify (wideo)" in text


@pytest.mark.integration
def test_process_spotify_episode_fallback_only_shows_legacy_audio_no_video(monkeypatch):
    from bot.handlers import inbound_media as im

    chat_id = 502
    resolved = {
        "source": "itunes",
        "audio_url": "https://example.com/audio.mp3",
        "title": "Odcinek testowy",
        "show_name": "Testowy podcast",
        "duration": 125,
        "spotify_title": "Odcinek testowy",
        "spotify_show": "Testowy podcast",
    }

    # No exception, no episode: this episode simply has no video track.
    monkeypatch.setattr(im, "resolve_video_episode", lambda url: None)

    async def fake_resolve_episode(url):
        return resolved

    monkeypatch.setattr(im, "resolve_episode", fake_resolve_episode)

    update = _make_message_update(chat_id=chat_id)
    context = _make_callback_context()
    runtime = _attach_callback_runtime(context)

    asyncio.run(
        im.extracted_process_spotify_episode(
            update, context, "https://open.spotify.com/episode/xyz"
        )
    )

    assert runtime.session_store.get_field(chat_id, "spotify_video") is None
    assert runtime.session_store.get_field(chat_id, "spotify_resolved") == resolved

    text, kwargs = _progress_edit(update)
    callback_data = _callback_data(kwargs["reply_markup"])

    assert "dl_audio_mp3" in callback_data
    assert "dl_audio_m4a" in callback_data
    # No native-video or native-audio buttons: video resolution produced
    # nothing to offer.
    assert not any(cb.startswith("spv_") for cb in callback_data)
    assert "Odcinek testowy" in text
    assert "Czas trwania: 2:05" in text
    assert "Źródło audio: iTunes" in text


@pytest.mark.integration
def test_process_spotify_episode_both_available_combines_buttons(monkeypatch):
    from bot.handlers import inbound_media as im
    from bot.services import spotify_video_service as svs

    chat_id = 503
    episode = svs.VideoEpisode(
        episode_id="abc",
        title="Testowy odcinek wideo",
        show_name="Testowy podcast",
        duration_ms=32000,
        manifest={"base_urls": []},
        profiles=[],
        subtitle_languages=[],
    )
    resolved = {
        "source": "youtube",
        "youtube_url": "https://youtube.com/watch?v=xyz",
        "title": "Odcinek testowy",
        "channel": "Testowy kanał",
        "duration": 125,
        "spotify_title": "Odcinek testowy",
        "spotify_show": "Testowy podcast",
    }

    monkeypatch.setattr(im, "resolve_video_episode", lambda url: episode)
    monkeypatch.setattr(
        im,
        "build_quality_options",
        lambda ep: [{"height": 480, "profile_id": 2, "size_mb": 120.0}],
    )

    async def fake_resolve_episode(url):
        return resolved

    monkeypatch.setattr(im, "resolve_episode", fake_resolve_episode)

    update = _make_message_update(chat_id=chat_id)
    context = _make_callback_context()
    runtime = _attach_callback_runtime(context)

    asyncio.run(
        im.extracted_process_spotify_episode(
            update, context, "https://open.spotify.com/episode/abc"
        )
    )

    assert runtime.session_store.get_field(chat_id, "spotify_video") is not None
    assert runtime.session_store.get_field(chat_id, "spotify_resolved") == resolved

    _, kwargs = _progress_edit(update)
    callback_data = _callback_data(kwargs["reply_markup"])

    assert "spv_video_480p" in callback_data
    assert "spv_audio_m4a" in callback_data
    assert "dl_audio_mp3" in callback_data
    assert "dl_audio_m4a" in callback_data


@pytest.mark.integration
def test_process_spotify_episode_neither_resolves_shows_specific_video_error(monkeypatch):
    from bot.handlers import inbound_media as im
    from bot.services import spotify_video_service as svs
    from bot.spotify_video import SpotifyVideoError

    chat_id = 504

    def fake_resolve_video_episode(url):
        raise SpotifyVideoError("no_cookie")

    monkeypatch.setattr(im, "resolve_video_episode", fake_resolve_video_episode)

    async def fake_resolve_episode(url):
        return None

    monkeypatch.setattr(im, "resolve_episode", fake_resolve_episode)

    update = _make_message_update(chat_id=chat_id)
    context = _make_callback_context()
    runtime = _attach_callback_runtime(context)

    asyncio.run(
        im.extracted_process_spotify_episode(
            update, context, "https://open.spotify.com/episode/abc"
        )
    )

    text, kwargs = _progress_edit(update)
    # The specific "no_cookie" reason must win over the generic legacy
    # resolution failure message -- it is the more actionable of the two.
    assert text == svs.get_video_error_message("no_cookie")
    assert "reply_markup" not in kwargs
    assert runtime.session_store.get_field(chat_id, "spotify_video") is None
    assert runtime.session_store.get_field(chat_id, "spotify_resolved") is None


@pytest.mark.integration
def test_process_spotify_episode_neither_resolves_falls_back_to_generic_error(monkeypatch):
    from bot.handlers import inbound_media as im
    from bot.services.spotify_service import get_resolution_error_message

    chat_id = 505

    # No exception this time -- the episode simply has no video, and the
    # legacy path also came up empty.
    monkeypatch.setattr(im, "resolve_video_episode", lambda url: None)

    async def fake_resolve_episode(url):
        return None

    monkeypatch.setattr(im, "resolve_episode", fake_resolve_episode)

    update = _make_message_update(chat_id=chat_id)
    context = _make_callback_context()
    _attach_callback_runtime(context)

    asyncio.run(
        im.extracted_process_spotify_episode(
            update, context, "https://open.spotify.com/episode/abc"
        )
    )

    text, _kwargs = _progress_edit(update)
    assert text == get_resolution_error_message(None)


@pytest.mark.integration
def test_process_spotify_episode_video_error_with_fallback_shows_notice_and_hides_video(monkeypatch):
    from bot.handlers import inbound_media as im
    from bot.services import spotify_video_service as svs
    from bot.spotify_video import SpotifyVideoError

    chat_id = 506
    resolved = {
        "source": "itunes",
        "audio_url": "https://example.com/audio.mp3",
        "title": "Odcinek testowy",
        "show_name": "Testowy podcast",
        "duration": 125,
        "spotify_title": "Odcinek testowy",
        "spotify_show": "Testowy podcast",
    }

    def fake_resolve_video_episode(url):
        raise SpotifyVideoError("expired_session")

    monkeypatch.setattr(im, "resolve_video_episode", fake_resolve_video_episode)

    async def fake_resolve_episode(url):
        return resolved

    monkeypatch.setattr(im, "resolve_episode", fake_resolve_episode)

    update = _make_message_update(chat_id=chat_id)
    context = _make_callback_context()
    runtime = _attach_callback_runtime(context)

    asyncio.run(
        im.extracted_process_spotify_episode(
            update, context, "https://open.spotify.com/episode/abc"
        )
    )

    assert runtime.session_store.get_field(chat_id, "spotify_video") is None

    text, kwargs = _progress_edit(update)
    callback_data = _callback_data(kwargs["reply_markup"])

    # Fallback audio is offered, but neither native video nor native audio --
    # video resolution failed, so those buttons would not work.
    assert "dl_audio_mp3" in callback_data
    assert not any(cb.startswith("spv_") for cb in callback_data)
    # The specific reason the video path failed is surfaced as a notice
    # alongside the working fallback, not swallowed.
    assert svs.get_video_error_message("expired_session") in text


@pytest.mark.integration
def test_process_spotify_episode_neither_resolves_clears_stale_session_state(monkeypatch):
    """Regression: a second Spotify link that fails on both paths must not
    leave a previous episode's spotify_video/spotify_resolved live in the
    session -- that stale manifest (with signed CDN URLs) would otherwise be
    handed to an unrelated later transcript/download request.
    """
    from bot.handlers import inbound_media as im
    from bot.spotify_video import SpotifyVideoError

    chat_id = 507
    stale_video = {
        "episode_id": "first-episode",
        "title": "Pierwszy odcinek",
        "show_name": "Podcast",
        "duration_ms": 10000,
        "manifest": {"base_urls": ["https://signed.example/first"]},
        "subtitle_languages": [],
    }
    stale_resolved = {
        "source": "itunes",
        "audio_url": "https://example.com/first.mp3",
        "title": "Pierwszy odcinek",
        "show_name": "Podcast",
        "duration": 10,
    }

    def fake_resolve_video_episode(url):
        raise SpotifyVideoError("no_cookie")

    monkeypatch.setattr(im, "resolve_video_episode", fake_resolve_video_episode)

    async def fake_resolve_episode(url):
        return None

    monkeypatch.setattr(im, "resolve_episode", fake_resolve_episode)

    update = _make_message_update(chat_id=chat_id)
    context = _make_callback_context()
    runtime = _attach_callback_runtime(context)
    runtime.session_store.set_field(chat_id, "spotify_video", stale_video)
    runtime.session_store.set_field(chat_id, "spotify_resolved", stale_resolved)

    asyncio.run(
        im.extracted_process_spotify_episode(
            update, context, "https://open.spotify.com/episode/second"
        )
    )

    assert runtime.session_store.get_field(chat_id, "spotify_video") is None
    assert runtime.session_store.get_field(chat_id, "spotify_resolved") is None


@pytest.mark.integration
def test_process_spotify_episode_fallback_only_clears_stale_video_session(monkeypatch):
    """Regression companion to the above: a link that resolves only via the
    legacy fallback path must also drop a previous episode's spotify_video,
    not just leave it untouched because this attempt never looked at video.
    """
    from bot.handlers import inbound_media as im

    chat_id = 508
    stale_video = {
        "episode_id": "first-episode",
        "title": "Pierwszy odcinek",
        "show_name": "Podcast",
        "duration_ms": 10000,
        "manifest": {"base_urls": ["https://signed.example/first"]},
        "subtitle_languages": [],
    }
    resolved = {
        "source": "itunes",
        "audio_url": "https://example.com/second.mp3",
        "title": "Drugi odcinek",
        "show_name": "Podcast",
        "duration": 60,
    }

    monkeypatch.setattr(im, "resolve_video_episode", lambda url: None)

    async def fake_resolve_episode(url):
        return resolved

    monkeypatch.setattr(im, "resolve_episode", fake_resolve_episode)

    update = _make_message_update(chat_id=chat_id)
    context = _make_callback_context()
    runtime = _attach_callback_runtime(context)
    runtime.session_store.set_field(chat_id, "spotify_video", stale_video)

    asyncio.run(
        im.extracted_process_spotify_episode(
            update, context, "https://open.spotify.com/episode/second"
        )
    )

    assert runtime.session_store.get_field(chat_id, "spotify_video") is None
    assert runtime.session_store.get_field(chat_id, "spotify_resolved") == resolved
