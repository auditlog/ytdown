"""Spotify collection archive orchestration tests."""

from __future__ import annotations

import asyncio
from unittest import mock

from bot.services import archive_service, spotify_archive_service
from bot.session_store import session_store


def _collection(count: int) -> dict:
    return {
        "title": "Duży Mix",
        "tracks": [
            {"title": f"Song {index + 1}", "artist": "Artist"}
            for index in range(count)
        ],
    }


def test_spotify_archive_groups_downloads_by_requested_track_count(
    tmp_path,
    monkeypatch,
):
    session_store.reset()
    monkeypatch.setattr(archive_service, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(spotify_archive_service, "is_7z_available", lambda: True)
    monkeypatch.setattr(
        spotify_archive_service,
        "mtproto_unavailability_reason",
        lambda: None,
    )

    async def fake_resolve(track, **_kwargs):
        return {
            "source": "youtube_music",
            "title": track["title"],
            "artist": track["artist"],
            "youtube_url": "https://youtube.com/watch?v=x",
        }

    async def fake_download(*, resolved, audio_format, output_dir, **_kwargs):
        path = spotify_archive_service.Path(output_dir) / f"{resolved['title']}.{audio_format}"
        path.write_bytes(b"audio")
        return str(path)

    packed_group_sizes = []
    packed_volume_sizes = []

    async def fake_pack(sources, dest_basename, volume_size_mb, **_kwargs):
        packed_group_sizes.append(len(sources))
        packed_volume_sizes.append(volume_size_mb)
        volume = spotify_archive_service.Path(f"{dest_basename}.7z.001")
        volume.write_bytes(b"archive")
        return [volume]

    sent = []

    async def fake_send(_bot, *, volumes, **_kwargs):
        sent.extend(volumes)

    monkeypatch.setattr(spotify_archive_service, "resolve_track_info", fake_resolve)
    monkeypatch.setattr(
        spotify_archive_service,
        "download_resolved_audio",
        fake_download,
    )
    monkeypatch.setattr(spotify_archive_service, "pack_to_volumes", fake_pack)
    monkeypatch.setattr(spotify_archive_service, "send_volumes", fake_send)

    update = mock.MagicMock()
    update.callback_query.edit_message_text = mock.AsyncMock()
    context = mock.MagicMock()
    context.bot = mock.MagicMock()

    result = asyncio.run(
        spotify_archive_service.execute_spotify_collection_archive_flow(
            update,
            context,
            chat_id=7,
            collection=_collection(5),
            selected_indices=[0, 1, 2, 3, 4],
            audio_format="mp3",
            files_per_archive=50,
            executor=mock.MagicMock(),
        )
    )

    # Five files fit into one requested 50-track archive.
    assert packed_group_sizes == [5]
    assert packed_volume_sizes == [1000]
    assert len(sent) == 1
    assert result.downloaded_count == 5
    assert result.archive_count == 1
    assert result.failed_indices == ()
    session_store.reset()


def test_spotify_archive_splits_into_multiple_logical_archives(
    tmp_path,
    monkeypatch,
):
    session_store.reset()
    monkeypatch.setattr(archive_service, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(spotify_archive_service, "is_7z_available", lambda: True)
    monkeypatch.setattr(
        spotify_archive_service,
        "mtproto_unavailability_reason",
        lambda: None,
    )

    async def fake_resolve(track, **_kwargs):
        return {
            "source": "youtube_music",
            "title": track["title"],
            "artist": track["artist"],
            "youtube_url": "https://youtube.com/watch?v=x",
        }

    async def fake_download(*, resolved, output_dir, **_kwargs):
        path = spotify_archive_service.Path(output_dir) / f"{resolved['title']}.mp3"
        path.write_bytes(b"audio")
        return str(path)

    group_sizes = []

    async def fake_pack(sources, dest_basename, _volume_size_mb, **_kwargs):
        group_sizes.append(len(sources))
        volume = spotify_archive_service.Path(f"{dest_basename}.7z.001")
        volume.write_bytes(b"archive")
        return [volume]

    monkeypatch.setattr(spotify_archive_service, "resolve_track_info", fake_resolve)
    monkeypatch.setattr(
        spotify_archive_service,
        "download_resolved_audio",
        fake_download,
    )
    monkeypatch.setattr(spotify_archive_service, "pack_to_volumes", fake_pack)
    monkeypatch.setattr(
        spotify_archive_service,
        "send_volumes",
        mock.AsyncMock(),
    )

    update = mock.MagicMock()
    update.callback_query.edit_message_text = mock.AsyncMock()
    context = mock.MagicMock()
    context.bot = mock.MagicMock()

    result = asyncio.run(
        spotify_archive_service.execute_spotify_collection_archive_flow(
            update,
            context,
            chat_id=8,
            collection=_collection(205),
            selected_indices=list(range(205)),
            audio_format="mp3",
            files_per_archive=100,
            executor=mock.MagicMock(),
        )
    )

    assert group_sizes == [100, 100, 5]
    assert result.archive_count == 3
    assert result.volume_count == 3
    session_store.reset()
