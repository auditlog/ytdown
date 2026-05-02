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
