"""
Unit tests for cleanup helpers.
"""

import os
import time
from types import SimpleNamespace
from pathlib import Path

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
