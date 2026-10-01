"""Spotify music tracks, albums, playlists, and YT Music matching tests."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from bot import spotify
from bot.handlers import inbound_media
from bot.handlers.common_ui import (
    build_spotify_archive_batch_view,
    build_spotify_collection_view,
)
from bot.services import spotify_service
from tests.telegram_callbacks_support import _attach_runtime, _make_context, _make_message_update


def _track(track_id="t1", title="Song", artist="Artist", duration_ms=180_000):
    return {
        "id": track_id,
        "name": title,
        "type": "track",
        "duration_ms": duration_ms,
        "artists": [{"name": artist}],
        "album": {"name": "Album"},
        "external_urls": {"spotify": f"https://open.spotify.com/track/{track_id}"},
    }


def test_parse_spotify_music_urls():
    assert spotify.parse_spotify_track_url("https://open.spotify.com/track/abc?si=x") == "abc"
    assert spotify.parse_spotify_collection_url("https://open.spotify.com/album/alb") == (
        "album",
        "alb",
    )
    assert spotify.parse_spotify_collection_url(
        "https://open.spotify.com/playlist/pl123"
    ) == ("playlist", "pl123")
    assert spotify.parse_spotify_track_url("https://open.spotify.com/episode/ep") is None


def test_spotify_token_uses_cookie_session_without_user_oauth(monkeypatch):
    monkeypatch.setattr(spotify, "get_spotify_user_access_token", lambda: None)
    monkeypatch.setattr(spotify, "load_spotify_cookie", lambda: "cookie")
    monkeypatch.setattr(
        spotify,
        "fetch_embed_access_token",
        lambda resource_type, resource_id, cookie: "user-token",
    )
    monkeypatch.setattr(
        spotify,
        "get_runtime_value",
        lambda key, default="": {
            "SPOTIFY_CLIENT_ID": "client-id",
            "SPOTIFY_CLIENT_SECRET": "client-secret",
        }.get(key, default),
    )

    assert spotify._get_spotify_token("playlist", "pl") == "user-token"


def test_spotify_token_prefers_user_oauth(monkeypatch):
    monkeypatch.setattr(
        spotify,
        "get_spotify_user_access_token",
        lambda: "oauth-user-token",
    )
    load_cookie = mock.Mock()
    monkeypatch.setattr(spotify, "load_spotify_cookie", load_cookie)

    assert spotify._get_spotify_token("playlist", "pl") == "oauth-user-token"
    load_cookie.assert_not_called()


def test_get_spotify_album_normalizes_tracks(monkeypatch):
    monkeypatch.setattr(spotify, "_get_spotify_token", lambda *args: "token")
    monkeypatch.setattr(
        spotify,
        "_spotify_api_get",
        lambda *args, **kwargs: {
            "name": "My Album",
            "artists": [{"name": "Album Artist"}],
            "tracks": {"items": [_track()], "total": 1, "next": None},
        },
    )

    result = spotify.get_spotify_collection("https://open.spotify.com/album/alb")

    assert result["kind"] == "album"
    assert result["owner"] == "Album Artist"
    assert result["tracks"][0]["artist"] == "Artist"
    assert result["tracks"][0]["album"] == "Album"


def test_get_spotify_playlist_accepts_new_item_wrapper(monkeypatch):
    monkeypatch.setattr(spotify, "_get_spotify_token", lambda *args: "token")
    monkeypatch.setattr(
        spotify,
        "_spotify_api_get",
        lambda *args, **kwargs: {
            "name": "My Playlist",
            "owner": {"display_name": "Owner"},
            "items": {"items": [{"item": _track()}], "total": 1, "next": None},
        },
    )

    result = spotify.get_spotify_collection("https://open.spotify.com/playlist/pl")

    assert result["title"] == "My Playlist"
    assert result["tracks"][0]["title"] == "Song"


def test_get_spotify_playlist_paginates_beyond_fifty_items(monkeypatch):
    monkeypatch.setattr(spotify, "_get_spotify_token", lambda *args: "token")
    first_page = {
        "items": [{"item": _track(f"t{index}")} for index in range(50)],
        "total": 60,
        "next": "https://api.spotify.com/v1/playlists/pl/items?offset=50",
    }
    second_page = {
        "items": [{"item": _track(f"t{index}")} for index in range(50, 60)],
        "total": 60,
        "next": None,
    }

    def api_get(path_or_url, *args, **kwargs):
        if str(path_or_url).startswith("https://"):
            return second_page
        return {
            "name": "Long Playlist",
            "owner": {"display_name": "Owner"},
            "items": first_page,
        }

    monkeypatch.setattr(spotify, "_spotify_api_get", api_get)

    result = spotify.get_spotify_collection("https://open.spotify.com/playlist/pl")

    assert spotify.SPOTIFY_COLLECTION_MAX_ITEMS == 500
    assert len(result["tracks"]) == 60
    assert result["truncated"] is False


def test_get_spotify_collection_maps_forbidden(monkeypatch):
    monkeypatch.setattr(spotify, "_get_spotify_token", lambda *args: "token")

    def fail(*args, **kwargs):
        raise spotify.SpotifyMetadataError("forbidden", 403)

    monkeypatch.setattr(spotify, "_spotify_api_get", fail)

    result = spotify.get_spotify_collection("https://open.spotify.com/playlist/pl")

    assert result["error"] == "forbidden"
    assert result["status_code"] == 403


def test_youtube_track_search_prefers_topic_channel(monkeypatch):
    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def extract_info(self, query, download=False):
            assert query.startswith("ytsearch10:")
            return {
                "entries": [
                    {
                        "id": "ordinary",
                        "title": "Artist - Song (Official Audio)",
                        "channel": "Random Uploads",
                        "duration": 180,
                    },
                    {
                        "id": "topic",
                        "title": "Song",
                        "channel": "Artist - Topic",
                        "duration": 180,
                    },
                ]
            }

    monkeypatch.setattr(spotify.yt_dlp, "YoutubeDL", FakeYDL)

    result = spotify.search_youtube_track("Song", "Artist", 180)

    assert result["source"] == "youtube_music"
    assert result["url"].endswith("topic")


def test_search_text_preserves_cyrillic_and_normalizes_polish_letters():
    assert spotify._search_text("Як черешня!") == "як черешня"
    assert spotify._search_text("Z czego składają się Święta") == (
        "z czego skladaja sie swieta"
    )


def test_youtube_track_search_accepts_cyrillic_title(monkeypatch):
    class FakeYDL:
        def __init__(self, _opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, _query, download=False):
            return {
                "entries": [
                    {
                        "id": "cherry",
                        "title": "Як черешня — InskarFolk",
                        "channel": "InskarFolk",
                        "duration": 185,
                    }
                ]
            }

    monkeypatch.setattr(spotify.yt_dlp, "YoutubeDL", FakeYDL)

    result = spotify.search_youtube_track("Як черешня!", "InskarFolk", 184)

    assert result["url"].endswith("cherry")
    assert result["score"] >= 0.55


def test_youtube_track_search_retries_without_official_audio(monkeypatch):
    queries = []

    class FakeYDL:
        def __init__(self, _opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, query, download=False):
            queries.append(query)
            if "official audio" in query:
                return {"entries": []}
            return {
                "entries": [
                    {
                        "id": "drag",
                        "title": "Rejsel - DRAG! feat. Lagoona Aqua Pussy",
                        "channel": "Rejsel and Mudormood",
                        "duration": 135,
                    }
                ]
            }

    monkeypatch.setattr(spotify.yt_dlp, "YoutubeDL", FakeYDL)

    result = spotify.search_youtube_track(
        "DRAG!",
        "Rejsel, Lagoona Aqua Pussy",
        135,
    )

    assert result["url"].endswith("drag")
    assert "official audio" in queries[0]
    assert "official audio" not in queries[1]
    assert result["search_query"] == "Rejsel, Lagoona Aqua Pussy - DRAG!"


def test_youtube_track_search_reports_low_confidence(monkeypatch):
    class FakeYDL:
        def __init__(self, _opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, _query, download=False):
            return {
                "entries": [
                    {
                        "id": "wrong",
                        "title": "Completely different recording",
                        "channel": "Unknown",
                        "duration": 20,
                    }
                ]
            }

    monkeypatch.setattr(spotify.yt_dlp, "YoutubeDL", FakeYDL)

    outcome = spotify.search_youtube_track_detailed("Expected Song", "Artist", 180)

    assert outcome.match is None
    assert outcome.failure_code == "low_confidence"
    assert "wymagane co najmniej 0.55" in outcome.failure_detail


def test_spotify_track_resolution_preserves_search_failure(monkeypatch):
    monkeypatch.setattr(
        spotify,
        "search_youtube_track_detailed",
        lambda *_args, **_kwargs: spotify.YouTubeTrackSearchOutcome(
            match=None,
            failure_code="no_search_results",
            failure_detail="Brak wyników po wszystkich próbach.",
        ),
    )

    outcome = spotify.resolve_spotify_track_info_detailed(
        {"title": "Song", "artist": "Artist", "duration_ms": 180_000}
    )

    assert outcome.resolved is None
    assert outcome.failure_code == "no_search_results"
    assert outcome.failure_detail == "Brak wyników po wszystkich próbach."


def test_build_collection_view_marks_selection_and_paginates():
    collection = {
        "kind": "album",
        "title": "Album",
        "owner": "Artist",
        "tracks": [
            {"title": f"Song {index}", "artist": "Artist"} for index in range(10)
        ],
        "total": 10,
        "selected": [1, 8],
        "page": 1,
        "archive_available": True,
    }

    text, keyboard = build_spotify_collection_view(collection, page_size=8)
    callbacks = [button.callback_data for row in keyboard for button in row]

    assert "Zaznaczono: 2" in text
    assert "spc_t_8" in callbacks
    assert "spc_t_1" not in callbacks
    assert "spc_dl_mp3" in callbacks
    assert "spc_pack_mp3" in callbacks


def test_build_spotify_archive_batch_view_offers_requested_group_sizes():
    collection = {"title": "Mix", "selected": list(range(123))}

    text, keyboard = build_spotify_archive_batch_view(
        collection,
        audio_format="mp3",
    )
    callbacks = [button.callback_data for row in keyboard for button in row]

    assert "Zaznaczono: 123" in text
    assert "spc_pack_mp3_50" in callbacks
    assert "spc_pack_mp3_100" in callbacks
    assert "spc_pack_mp3_all" in callbacks
    assert "spc_pack_back" in callbacks


def test_process_spotify_track_stores_resolved_session(monkeypatch):
    update = _make_message_update("https://open.spotify.com/track/t1", chat_id=77)
    context = _make_context()
    runtime = _attach_runtime(context)
    resolved = {
        "source": "youtube_music",
        "youtube_url": "https://youtube.com/watch?v=x",
        "title": "Song",
        "artist": "Artist",
        "duration": 180,
        "matched_title": "Song",
    }
    monkeypatch.setattr(inbound_media, "resolve_track", mock.AsyncMock(return_value=resolved))

    asyncio.run(
        inbound_media.extracted_process_spotify_track(
            update,
            context,
            "https://open.spotify.com/track/t1",
        )
    )

    assert runtime.session_store.get_field(77, "spotify_resolved") == resolved
    progress_message = update.message.reply_text.return_value
    assert "YT Music" in progress_message.edit_text.await_args.args[0]


def test_download_resolved_audio_accepts_youtube_music(monkeypatch, tmp_path):
    expected = tmp_path / "Artist - Song.mp3"
    expected.write_bytes(b"audio")

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def download(self, urls):
            return None

    monkeypatch.setattr(spotify_service.yt_dlp, "YoutubeDL", FakeYDL)

    with ThreadPoolExecutor(max_workers=1) as executor:
        result = asyncio.run(
            spotify_service.download_resolved_audio(
                resolved={
                    "source": "youtube_music",
                    "title": "Song",
                    "artist": "Artist",
                    "youtube_url": "https://youtube.com/watch?v=x",
                },
                audio_format="mp3",
                output_dir=str(tmp_path),
                executor=executor,
            )
        )

    assert result == str(expected)
