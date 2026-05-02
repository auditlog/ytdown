"""Unit tests for bot.archive low-level helpers."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest


def test_volume_size_for_mtproto_returns_mtproto_constant():
    from bot import archive
    from bot.security_limits import MTPROTO_VOLUME_SIZE_MB

    assert archive.volume_size_for(use_mtproto=True) == MTPROTO_VOLUME_SIZE_MB


def test_volume_size_for_botapi_returns_botapi_constant():
    from bot import archive
    from bot.security_limits import BOTAPI_VOLUME_SIZE_MB

    assert archive.volume_size_for(use_mtproto=False) == BOTAPI_VOLUME_SIZE_MB


def test_volume_size_for_mtproto_larger_than_botapi():
    from bot.archive import volume_size_for

    assert volume_size_for(use_mtproto=True) > volume_size_for(use_mtproto=False)


def test_transliterate_to_ascii_replaces_polish_letters():
    from bot.archive import transliterate_to_ascii

    assert transliterate_to_ascii("Pączki ąęłżźćń") == "Paczki aelzzcn"


def test_transliterate_to_ascii_preserves_safe_characters():
    from bot.archive import transliterate_to_ascii

    assert transliterate_to_ascii("Hello world - 2026!") == "Hello world - 2026!"


def test_transliterate_to_ascii_handles_uppercase():
    from bot.archive import transliterate_to_ascii

    assert transliterate_to_ascii("ŻÓŁW") == "ZOLW"


def test_compute_archive_basename_format():
    from bot.archive import compute_archive_basename

    ts = datetime(2026, 5, 2, 14, 5, 33)
    assert compute_archive_basename("playlist", ts) == "playlist_20260502-140533"


def test_is_7z_available_when_present():
    from bot import archive

    with mock.patch("bot.archive.shutil.which", return_value="/usr/bin/7z"):
        assert archive.is_7z_available() is True


def test_is_7z_available_when_absent():
    from bot import archive

    with mock.patch("bot.archive.shutil.which", return_value=None):
        assert archive.is_7z_available() is False


def test_pack_to_volumes_raises_on_empty_sources():
    from bot.archive import pack_to_volumes

    with pytest.raises(ValueError, match="empty sources"):
        asyncio.run(pack_to_volumes([], Path("/tmp/x"), volume_size_mb=10))


def test_pack_to_volumes_invokes_7z_with_correct_args(tmp_path):
    from bot import archive

    src1 = tmp_path / "a.bin"
    src1.write_bytes(b"x")
    src2 = tmp_path / "b.bin"
    src2.write_bytes(b"y")
    dest = tmp_path / "out_archive"

    completed = mock.AsyncMock()
    completed.communicate = mock.AsyncMock(return_value=(b"", b""))
    completed.returncode = 0

    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        # Simulate 7z producing two volumes.
        (tmp_path / "out_archive.7z.001").write_bytes(b"a")
        (tmp_path / "out_archive.7z.002").write_bytes(b"b")
        return completed

    with mock.patch("bot.archive.asyncio.create_subprocess_exec", side_effect=fake_exec):
        result = asyncio.run(
            archive.pack_to_volumes([src1, src2], dest, volume_size_mb=42)
        )

    assert captured["args"][:6] == (
        "7z",
        "a",
        "-t7z",
        "-v42m",
        "-mx0",
        "-mmt=on",
    )
    assert str(dest.with_suffix(".7z")) in captured["args"]
    assert str(src1) in captured["args"]
    assert str(src2) in captured["args"]
    assert result == [tmp_path / "out_archive.7z.001", tmp_path / "out_archive.7z.002"]


def test_pack_to_volumes_returns_sorted_volume_paths(tmp_path):
    from bot import archive

    src = tmp_path / "a.bin"
    src.write_bytes(b"x")
    dest = tmp_path / "playlist"

    completed = mock.AsyncMock()
    completed.communicate = mock.AsyncMock(return_value=(b"", b""))
    completed.returncode = 0

    async def fake_exec(*args, **kwargs):
        # Volumes intentionally created out of order on disk.
        for i in (3, 1, 2):
            (tmp_path / f"playlist.7z.00{i}").write_bytes(b"z")
        return completed

    with mock.patch("bot.archive.asyncio.create_subprocess_exec", side_effect=fake_exec):
        result = asyncio.run(archive.pack_to_volumes([src], dest, volume_size_mb=10))

    assert [p.name for p in result] == [
        "playlist.7z.001",
        "playlist.7z.002",
        "playlist.7z.003",
    ]


def test_pack_to_volumes_raises_when_7z_exits_nonzero(tmp_path):
    from bot import archive

    src = tmp_path / "a.bin"
    src.write_bytes(b"x")

    completed = mock.AsyncMock()
    completed.communicate = mock.AsyncMock(return_value=(b"", b"E_NO_DISK_SPACE\n"))
    completed.returncode = 2

    async def fake_exec(*args, **kwargs):
        return completed

    with mock.patch("bot.archive.asyncio.create_subprocess_exec", side_effect=fake_exec):
        with pytest.raises(RuntimeError, match="7z failed"):
            asyncio.run(
                archive.pack_to_volumes([src], tmp_path / "x", volume_size_mb=1)
            )


def test_pack_to_volumes_swallows_progress_callback_exception(tmp_path):
    """Best-effort progress: a raising callback must not abort packing."""
    from bot import archive

    src = tmp_path / "a.bin"
    src.write_bytes(b"x")
    dest = tmp_path / "out"

    async def fake_exec(*args, **kwargs):
        process = mock.MagicMock()
        process.returncode = 0
        process.communicate = mock.AsyncMock(return_value=(b"", b""))
        process.stdout = mock.MagicMock()
        process.stdout.readline = mock.AsyncMock(side_effect=[b""])
        (tmp_path / "out.7z.001").write_bytes(b"v")
        return process

    async def raising_callback(_text):
        raise RuntimeError("boom from callback")

    with mock.patch("bot.archive.asyncio.create_subprocess_exec", side_effect=fake_exec):
        result = asyncio.run(
            archive.pack_to_volumes(
                [src], dest, volume_size_mb=1, progress_cb=raising_callback,
            )
        )

    assert result == [tmp_path / "out.7z.001"]


def test_pack_to_volumes_real_7z_small_volumes(tmp_path):
    """Integration test: spawn the real 7z binary on a small file."""

    import shutil as _shutil
    if _shutil.which("7z") is None:
        pytest.skip("7z binary not available on this host")

    from bot import archive

    src = tmp_path / "data.bin"
    # 3 MB content, with -v1m volumes -> at least 3 volumes.
    src.write_bytes(b"Z" * (3 * 1024 * 1024))
    dest = tmp_path / "intg"

    result = asyncio.run(archive.pack_to_volumes([src], dest, volume_size_mb=1))

    assert len(result) >= 3
    assert all(p.exists() for p in result)
    assert all(p.name.startswith("intg.7z.") for p in result)
