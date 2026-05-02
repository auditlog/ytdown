"""Unit tests for bot.services.archive_service workspace + registry helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest


def test_prepare_playlist_workspace_creates_pl_prefixed_dir(tmp_path, monkeypatch):
    from bot.services import archive_service

    monkeypatch.setattr(archive_service, "DOWNLOAD_PATH", str(tmp_path))
    fixed_ts = datetime(2026, 5, 2, 9, 30, 15)
    with mock.patch("bot.services.archive_service.datetime") as dt_mock:
        dt_mock.now.return_value = fixed_ts
        ws = archive_service.prepare_playlist_workspace(7, "Lista A")

    assert ws.exists() and ws.is_dir()
    assert ws.parent == tmp_path / "7"
    assert ws.name.startswith("pl_")
    assert "Lista_A" in ws.name or "Lista A" in ws.name
    assert "20260502-093015" in ws.name


def test_prepare_playlist_workspace_transliterates_polish_chars(tmp_path, monkeypatch):
    from bot.services import archive_service

    monkeypatch.setattr(archive_service, "DOWNLOAD_PATH", str(tmp_path))
    ws = archive_service.prepare_playlist_workspace(9, "Pączki ąęłż")

    assert "Paczki" in ws.name
    assert "ą" not in ws.name and "ł" not in ws.name


def test_prepare_playlist_workspace_uses_big_prefix_when_requested(tmp_path, monkeypatch):
    from bot.services import archive_service

    monkeypatch.setattr(archive_service, "DOWNLOAD_PATH", str(tmp_path))
    ws = archive_service.prepare_playlist_workspace(1, "video", prefix="big")

    assert ws.name.startswith("big_")


def test_register_pending_archive_job_returns_unique_tokens(tmp_path):
    from bot.services import archive_service
    from bot.session_store import (
        ArchiveJobState,
        pending_archive_jobs,
        session_store,
    )

    session_store.reset()
    state = ArchiveJobState(
        file_path=Path(tmp_path / "x.mp4"),
        title="t",
        media_type="video",
        format_choice="best",
        file_size_mb=200.0,
        use_mtproto=False,
        created_at=datetime(2026, 5, 2),
    )

    tokens = {archive_service.register_pending_archive_job(99, state) for _ in range(50)}

    assert len(tokens) == 50
    assert pending_archive_jobs[99].keys() == tokens
    session_store.reset()


def test_register_archived_delivery_stores_state():
    from bot.services import archive_service
    from bot.session_store import (
        ArchivedDeliveryState,
        archived_deliveries,
        session_store,
    )

    session_store.reset()
    delivery = ArchivedDeliveryState(
        workspace=Path("/tmp/pl_ws"),
        volumes=[Path("/tmp/pl_ws/x.7z.001")],
        caption_prefix="ABC",
        use_mtproto=True,
        created_at=datetime(2026, 5, 2),
    )

    token = archive_service.register_archived_delivery(11, delivery)

    assert token in archived_deliveries[11]
    assert archived_deliveries[11][token] is delivery
    session_store.reset()


def test_download_playlist_into_keeps_files_after_download(tmp_path, monkeypatch):
    from bot.services import archive_service

    workspace = tmp_path / "pl_x"
    workspace.mkdir()

    async def fake_run(entry, workspace_path, *, media_type, format_choice, executor):
        produced = workspace_path / f"{entry['title']}.bin"
        produced.write_bytes(b"data")
        return produced, 1.5  # path, size_mb

    monkeypatch.setattr(archive_service, "_download_one_into_workspace", fake_run)

    entries = [{"url": "u1", "title": "first"}, {"url": "u2", "title": "second"}]
    import asyncio

    paths, failed = asyncio.run(
        archive_service.download_playlist_into(
            workspace,
            entries,
            media_type="audio",
            format_choice="mp3",
            executor=mock.MagicMock(),
            status_cb=mock.AsyncMock(),
        )
    )

    assert {p.name for p in paths} == {"first.bin", "second.bin"}
    assert failed == []
    assert (workspace / "first.bin").exists()
    assert (workspace / "second.bin").exists()


def test_download_playlist_into_returns_empty_when_all_fail(tmp_path, monkeypatch):
    from bot.services import archive_service

    workspace = tmp_path / "pl_x"
    workspace.mkdir()

    async def fake_run(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(archive_service, "_download_one_into_workspace", fake_run)

    entries = [{"url": "u1", "title": "a"}, {"url": "u2", "title": "b"}]
    import asyncio

    paths, failed = asyncio.run(
        archive_service.download_playlist_into(
            workspace, entries, media_type="audio", format_choice="mp3",
            executor=mock.MagicMock(), status_cb=mock.AsyncMock(),
        )
    )

    assert paths == []
    assert failed == ["a", "b"]


def test_download_playlist_into_returns_failed_titles_on_partial(tmp_path, monkeypatch):
    from bot.services import archive_service

    workspace = tmp_path / "pl_x"
    workspace.mkdir()
    call_count = {"n": 0}

    async def fake_run(entry, workspace_path, **kwargs):
        call_count["n"] += 1
        if entry["title"] == "bad":
            raise RuntimeError("fail")
        produced = workspace_path / f"{entry['title']}.bin"
        produced.write_bytes(b"x")
        return produced, 0.5

    monkeypatch.setattr(archive_service, "_download_one_into_workspace", fake_run)

    entries = [
        {"url": "u1", "title": "good1"},
        {"url": "u2", "title": "bad"},
        {"url": "u3", "title": "good2"},
    ]
    import asyncio

    paths, failed = asyncio.run(
        archive_service.download_playlist_into(
            workspace, entries, media_type="audio", format_choice="mp3",
            executor=mock.MagicMock(), status_cb=mock.AsyncMock(),
        )
    )

    assert {p.name for p in paths} == {"good1.bin", "good2.bin"}
    assert failed == ["bad"]


def test_download_playlist_into_respects_max_archive_item_size(tmp_path, monkeypatch):
    from bot.services import archive_service

    workspace = tmp_path / "pl_x"
    workspace.mkdir()

    async def fake_run(entry, workspace_path, **kwargs):
        # Pretend the second entry is huge.
        if entry["title"] == "huge":
            return None, 99999.0  # too big
        produced = workspace_path / f"{entry['title']}.bin"
        produced.write_bytes(b"x")
        return produced, 1.0

    monkeypatch.setattr(archive_service, "_download_one_into_workspace", fake_run)

    entries = [
        {"url": "u1", "title": "ok"},
        {"url": "u2", "title": "huge"},
    ]
    import asyncio

    paths, failed = asyncio.run(
        archive_service.download_playlist_into(
            workspace, entries, media_type="audio", format_choice="mp3",
            executor=mock.MagicMock(), status_cb=mock.AsyncMock(),
        )
    )

    assert [p.name for p in paths] == ["ok.bin"]
    assert any("huge" in title for title in failed)
