"""
Unit tests for cleanup helpers.
"""

import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from bot.cleanup import cleanup_old_files, get_disk_usage, monitor_disk_space


def _touch_file(path: Path, mtime: float | None = None, size: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def test_cleanup_old_files_removes_stale_files(tmp_path):
    old_root = tmp_path / "stale"
    old_file = old_root / "old.txt"
    new_file = tmp_path / "fresh.txt"
    nested_file = old_root / "nested" / "nested_old.txt"

    now = time.time()
    _touch_file(old_file, now - 48 * 3600)
    _touch_file(new_file, now - 1 * 3600)
    _touch_file(nested_file, now - 48 * 3600)

    deleted = cleanup_old_files(str(tmp_path), max_age_hours=24)

    assert deleted == 2
    assert not old_file.exists()
    assert not nested_file.exists()
    assert new_file.exists()
    assert not nested_file.parent.exists()


def test_cleanup_old_files_nonexistent_directory():
    assert cleanup_old_files("/tmp/path-that-does-not-exist", max_age_hours=24) == 0


def test_get_disk_usage_returns_disk_usage(monkeypatch):
    total = 100 * 1024 ** 3
    used = 40 * 1024 ** 3
    free = 60 * 1024 ** 3

    import bot.cleanup as cleanup_module
    monkeypatch.setattr(cleanup_module.shutil, "disk_usage", lambda _path: (total, used, free))
    got = get_disk_usage()

    used_gb, free_gb, total_gb, usage_percent = got
    assert round(used_gb, 2) == round(40.0, 2)
    assert round(free_gb, 2) == round(60.0, 2)
    assert round(total_gb, 2) == round(100.0, 2)
    assert usage_percent == 40.0


def test_get_disk_usage_df_fallback(monkeypatch):
    import bot.cleanup as cleanup_module

    monkeypatch.setattr(cleanup_module.shutil, "disk_usage", lambda _path: (_ for _ in ()).throw(OSError("boom")))

    class Completed:
        returncode = 0
        stdout = "Filesystem     1K-blocks   Used Available Use% Mounted on\n/dev/sda1 200G 100G 100G 50% /\n"

    monkeypatch.setattr(cleanup_module.subprocess, "run", lambda *args, **kwargs: Completed())

    got = get_disk_usage()
    assert got == (100.0, 100.0, 200.0, 50.0)


def test_get_disk_usage_statvfs_fallback(monkeypatch):
    import bot.cleanup as cleanup_module

    monkeypatch.setattr(cleanup_module.shutil, "disk_usage", lambda _path: (_ for _ in ()).throw(OSError("boom")))
    monkeypatch.setattr(cleanup_module.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("boom")))
    monkeypatch.setattr(cleanup_module.os, "statvfs", lambda _path: SimpleNamespace(
        f_blocks=200,
        f_frsize=1024 ** 2,
        f_avail=100,
    ))

    got = get_disk_usage()
    assert got == (100.0 / 1024, 100.0 / 1024, 200.0 / 1024, 50.0)


def test_monitor_disk_space_no_cleanup_when_disk_available(monkeypatch):
    import bot.cleanup as cleanup_module
    called = []

    monkeypatch.setattr(
        cleanup_module, "get_disk_usage", lambda: (80.0, 20.0, 100.0, 80.0)
    )
    monkeypatch.setattr(cleanup_module, "cleanup_old_files", lambda *args, **kwargs: called.append((args, kwargs)))

    monitor_disk_space()
    assert called == []


def test_monitor_disk_space_runs_cleanup_below_warning_threshold(monkeypatch):
    calls = []

    import bot.cleanup as cleanup_module

    monkeypatch.setattr(
        cleanup_module, "get_disk_usage", lambda: (95.0, 4.0, 100.0, 96.0)
    )

    def fake_cleanup(path, max_age_hours):
        calls.append((path, max_age_hours))

    monkeypatch.setattr(cleanup_module, "cleanup_old_files", fake_cleanup)

    monitor_disk_space()
    assert calls == [(cleanup_module.DOWNLOAD_PATH, 6)]


def test_purge_archive_workspaces_removes_old_pl_dirs(tmp_path, monkeypatch):
    from bot import cleanup

    chat_dir = tmp_path / "111"
    chat_dir.mkdir()
    old_ws = chat_dir / "pl_oldslug_20260102-080000"
    old_ws.mkdir()
    (old_ws / "x.7z.001").write_bytes(b"x")
    # Set mtime to 2 hours ago.
    old_time = time.time() - 2 * 3600
    os.utime(old_ws, (old_time, old_time))

    cleanup._purge_archive_workspaces(chat_dir, retention_min=60)

    assert not old_ws.exists()


def test_purge_archive_workspaces_keeps_recent_dirs(tmp_path):
    from bot import cleanup

    chat_dir = tmp_path / "222"
    chat_dir.mkdir()
    fresh_ws = chat_dir / "pl_fresh_20260502-080000"
    fresh_ws.mkdir()
    # Default mtime is "now", which is well under 60 min.
    cleanup._purge_archive_workspaces(chat_dir, retention_min=60)
    assert fresh_ws.exists()


def test_purge_archive_workspaces_keeps_dirs_under_threshold(tmp_path):
    from bot import cleanup

    chat_dir = tmp_path / "333"
    chat_dir.mkdir()
    ws = chat_dir / "pl_locked_20260502-080000"
    ws.mkdir()
    (ws / ".lock").touch()
    # Make the workspace look 30 min old (under the 60 min retention).
    age = time.time() - 30 * 60
    os.utime(ws, (age, age))

    cleanup._purge_archive_workspaces(chat_dir, retention_min=60)

    assert ws.exists()


def test_purge_archive_workspaces_keeps_locked_dir_within_safety_net(tmp_path):
    """Lock blocks deletion when age > retention but <= 24h."""
    chat_dir = tmp_path / "555"
    chat_dir.mkdir()
    ws = chat_dir / "pl_locked_old_20260502-080000"
    ws.mkdir()
    (ws / ".lock").touch()
    # 90 min old: past 60 min retention, well below 24h safety net.
    age = time.time() - 90 * 60
    os.utime(ws, (age, age))

    from bot import cleanup
    cleanup._purge_archive_workspaces(chat_dir, retention_min=60)

    assert ws.exists()


def test_purge_archive_workspaces_safety_net_deletes_lock_after_24h(tmp_path):
    """Lock must not save a workspace older than the 24h safety net."""
    chat_dir = tmp_path / "666"
    chat_dir.mkdir()
    ws = chat_dir / "pl_orphan_lock_20260502-080000"
    ws.mkdir()
    (ws / ".lock").touch()
    # 25 h old: past safety net.
    age = time.time() - 25 * 3600
    os.utime(ws, (age, age))

    from bot import cleanup
    cleanup._purge_archive_workspaces(chat_dir, retention_min=60)

    assert not ws.exists()


def test_purge_archive_workspaces_ignores_non_archive_dirs(tmp_path):
    from bot import cleanup

    chat_dir = tmp_path / "444"
    chat_dir.mkdir()
    other = chat_dir / "downloads_subfolder"
    other.mkdir()
    age = time.time() - 7200
    os.utime(other, (age, age))

    cleanup._purge_archive_workspaces(chat_dir, retention_min=60)
    assert other.exists()


def test_purge_pending_archive_jobs_removes_old_jobs(tmp_path):
    from bot import cleanup
    from bot.session_store import (
        ArchiveJobState,
        pending_archive_jobs,
        session_store,
    )

    session_store.reset()
    src = tmp_path / "x.mp4"
    src.write_bytes(b"x")
    old_state = ArchiveJobState(
        file_path=src, title="x", media_type="video", format_choice="best",
        file_size_mb=1.0, use_mtproto=False,
        created_at=datetime.now() - timedelta(hours=2),
    )
    pending_archive_jobs[1] = {"old": old_state}

    cleanup._purge_pending_archive_jobs(retention_min=60)

    assert pending_archive_jobs.get(1, {}).get("old") is None
    assert not src.exists()
    session_store.reset()


def test_purge_archived_deliveries_removes_old_entries(tmp_path):
    from bot import cleanup
    from bot.session_store import (
        ArchivedDeliveryState,
        archived_deliveries,
        session_store,
    )

    session_store.reset()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    old_state = ArchivedDeliveryState(
        workspace=workspace,
        volumes=[workspace / "x.7z.001"],
        caption_prefix="X",
        use_mtproto=True,
        created_at=datetime.now() - timedelta(hours=2),
    )
    archived_deliveries[1] = {"old": old_state}

    cleanup._purge_archived_deliveries(retention_min=60)

    assert archived_deliveries.get(1, {}).get("old") is None
    session_store.reset()


def test_purge_dead_jobs_called_in_periodic_cleanup(monkeypatch):
    """periodic_cleanup invokes job_registry.purge_dead with 6h threshold."""

    from datetime import timedelta
    from bot import cleanup
    from bot.jobs import JobRegistry

    test_registry = JobRegistry()
    monkeypatch.setattr(cleanup, "job_registry", test_registry)
    captured = {}

    def fake_purge(threshold):
        captured["threshold"] = threshold
        return 0

    monkeypatch.setattr(test_registry, "purge_dead", fake_purge)

    # Drive one iteration of the loop body without sleeping for an hour.
    cleanup._purge_dead_jobs(retention_hours=6)

    assert captured["threshold"] == timedelta(hours=6)


def test_purge_partial_archive_workspaces_removes_old(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    from pathlib import Path
    from bot import cleanup
    from bot.session_store import (
        ArchivePartialState,
        partial_archive_workspaces,
        session_store,
    )

    session_store.reset()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    state = ArchivePartialState(
        workspace=workspace, downloaded=[], title="x",
        media_type="audio", format_choice="mp3", use_mtproto=False,
        created_at=datetime.now() - timedelta(hours=2),
    )
    partial_archive_workspaces[1] = {"old": state}

    cleanup._purge_partial_archive_workspaces(retention_min=60)

    assert partial_archive_workspaces.get(1, {}).get("old") is None
    session_store.reset()
