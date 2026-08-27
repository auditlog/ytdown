"""Telegram multi-select callbacks for Spotify collections."""

import asyncio
from types import SimpleNamespace
from unittest import mock

from bot.handlers import spotify_collection_callbacks as callbacks
from tests.telegram_callbacks_support import _attach_runtime, _make_context, _make_update


def _collection():
    return {
        "kind": "playlist",
        "title": "Mix",
        "owner": "Owner",
        "tracks": [
            {"title": "One", "artist": "A", "duration_ms": 100_000},
            {"title": "Two", "artist": "B", "duration_ms": 120_000},
        ],
        "total": 2,
        "selected": [],
        "page": 0,
        "archive_available": True,
    }


def test_toggle_track_updates_runtime_selection():
    context = _make_context()
    runtime = _attach_runtime(context)
    runtime.session_store.set_field(5, "spotify_collection", _collection())
    update = _make_update("spc_t_1", chat_id=5)

    asyncio.run(callbacks.handle_spotify_collection_callback(update, context, "spc_t_1"))

    state = runtime.session_store.get_field(5, "spotify_collection")
    assert state["selected"] == [1]
    update.callback_query.edit_message_text.assert_awaited()


def test_download_selected_resolves_and_sends_each_track(monkeypatch):
    context = _make_context()
    runtime = _attach_runtime(context)
    collection = _collection()
    collection["selected"] = [0, 1]
    runtime.session_store.set_field(5, "spotify_collection", collection)
    update = _make_update("spc_dl_mp3", chat_id=5)

    async def fake_resolve(track, **kwargs):
        return {
            "source": "youtube_music",
            "title": track["title"],
            "artist": track["artist"],
            "youtube_url": "https://youtube.com/watch?v=x",
        }

    download = mock.AsyncMock(return_value=True)
    monkeypatch.setattr(callbacks, "resolve_track_info", fake_resolve)
    monkeypatch.setattr(callbacks, "download_spotify_resolved", download)

    asyncio.run(
        callbacks.handle_spotify_collection_callback(update, context, "spc_dl_mp3")
    )

    assert download.await_count == 2
    state = runtime.session_store.get_field(5, "spotify_collection")
    assert state["selected"] == []


def test_download_failures_remain_selected(monkeypatch):
    context = _make_context()
    runtime = _attach_runtime(context)
    collection = _collection()
    collection["selected"] = [0, 1]
    runtime.session_store.set_field(5, "spotify_collection", collection)
    update = _make_update("spc_dl_m4a", chat_id=5)

    async def fake_resolve(track, **kwargs):
        if track["title"] == "Two":
            return None
        return {
            "source": "youtube_music",
            "title": track["title"],
            "artist": track["artist"],
            "youtube_url": "https://youtube.com/watch?v=x",
        }

    monkeypatch.setattr(callbacks, "resolve_track_info", fake_resolve)
    monkeypatch.setattr(
        callbacks,
        "download_spotify_resolved",
        mock.AsyncMock(return_value=True),
    )

    asyncio.run(
        callbacks.handle_spotify_collection_callback(update, context, "spc_dl_m4a")
    )

    state = runtime.session_store.get_field(5, "spotify_collection")
    assert state["selected"] == [1]


def test_archive_button_opens_group_size_choice():
    context = _make_context()
    runtime = _attach_runtime(context)
    collection = _collection()
    collection["selected"] = [0, 1]
    runtime.session_store.set_field(5, "spotify_collection", collection)
    update = _make_update("spc_pack_mp3", chat_id=5)

    asyncio.run(
        callbacks.handle_spotify_collection_callback(update, context, "spc_pack_mp3")
    )

    kwargs = update.callback_query.edit_message_text.await_args.kwargs
    callback_data = [
        button.callback_data
        for row in kwargs["reply_markup"].inline_keyboard
        for button in row
    ]
    assert "spc_pack_mp3_50" in callback_data
    assert "spc_pack_mp3_100" in callback_data
    assert "spc_pack_mp3_all" in callback_data


def test_archive_group_choice_dispatches_and_keeps_failures_selected(monkeypatch):
    context = _make_context()
    runtime = _attach_runtime(context)
    collection = _collection()
    collection["selected"] = [0, 1]
    runtime.session_store.set_field(5, "spotify_collection", collection)
    update = _make_update("spc_pack_mp3_50", chat_id=5)

    execute = mock.AsyncMock(
        return_value=SimpleNamespace(failed_indices=(1,))
    )
    monkeypatch.setattr(
        callbacks,
        "execute_spotify_collection_archive_flow",
        execute,
    )

    asyncio.run(
        callbacks.handle_spotify_collection_callback(
            update,
            context,
            "spc_pack_mp3_50",
        )
    )

    assert execute.await_args.kwargs["audio_format"] == "mp3"
    assert execute.await_args.kwargs["files_per_archive"] == 50
    assert execute.await_args.kwargs["selected_indices"] == [0, 1]
    state = runtime.session_store.get_field(5, "spotify_collection")
    assert state["selected"] == [1]


def test_archive_all_choice_uses_single_logical_archive(monkeypatch):
    context = _make_context()
    runtime = _attach_runtime(context)
    collection = _collection()
    collection["selected"] = [0]
    runtime.session_store.set_field(5, "spotify_collection", collection)
    update = _make_update("spc_pack_m4a_all", chat_id=5)
    execute = mock.AsyncMock(return_value=SimpleNamespace(failed_indices=()))
    monkeypatch.setattr(
        callbacks,
        "execute_spotify_collection_archive_flow",
        execute,
    )

    asyncio.run(
        callbacks.handle_spotify_collection_callback(
            update,
            context,
            "spc_pack_m4a_all",
        )
    )

    assert execute.await_args.kwargs["audio_format"] == "m4a"
    assert execute.await_args.kwargs["files_per_archive"] is None
