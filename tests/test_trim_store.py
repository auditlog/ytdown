"""Tests for the on-disk trim source store."""

import json
import os
import time
from collections import namedtuple
from datetime import UTC, datetime, timedelta

import pytest

from bot.services import trim_store

_Usage = namedtuple("usage", "total used free")


@pytest.fixture
def store_root(tmp_path, monkeypatch):
    root = tmp_path / "downloads"
    root.mkdir()
    monkeypatch.setattr(trim_store, "DOWNLOAD_PATH", str(root))
    monkeypatch.setattr(
        trim_store.shutil, "disk_usage", lambda _path: _Usage(100 * 1024**3, 0, 50 * 1024**3)
    )
    return root


def _audio(tmp_path, name="episode.mp3", payload=b"ID3 audio"):
    path = tmp_path / name
    path.write_bytes(payload)
    return path


def _retain(tmp_path, name="episode.mp3", **overrides):
    kwargs = {"title": "Podcast", "performer": None, "duration_sec": 600}
    kwargs.update(overrides)
    return trim_store.retain_source(42, _audio(tmp_path, name), **kwargs)


def test_retain_moves_file_and_writes_meta(store_root, tmp_path):
    original = _audio(tmp_path)
    source = trim_store.retain_source(
        42, original, title="Podcast #120", performer="Host", duration_sec=6130
    )
    assert source is not None
    assert not original.exists()
    assert source.path == store_root / "42" / f"trim_{source.token}" / "source.mp3"
    assert source.workspace == source.path.parent
    assert source.path.read_bytes() == b"ID3 audio"
    meta = json.loads((source.workspace / "meta.json").read_text(encoding="utf-8"))
    assert meta["title"] == "Podcast #120"
    assert meta["performer"] == "Host"
    assert meta["duration_sec"] == 6130
    assert len(source.token) == 11


@pytest.mark.parametrize("link", [False, True])
def test_retain_refreshes_source_mtime(store_root, tmp_path, link):
    # cleanup_old_files ages files by mtime; a download can carry the server's
    # Last-Modified, which must not make a fresh source look two days old.
    original = _audio(tmp_path)
    two_days_ago = time.time() - 2 * 24 * 3600
    os.utime(original, (two_days_ago, two_days_ago))

    source = trim_store.retain_source(
        42, original, title="Podcast", performer=None, duration_sec=600, link=link
    )

    assert abs(source.path.stat().st_mtime - time.time()) < 60


def test_retain_with_link_keeps_original(store_root, tmp_path):
    original = _audio(tmp_path)
    source = trim_store.retain_source(42, original, title="Upload", performer=None, duration_sec=60, link=True)
    assert original.exists()
    assert source.path.read_bytes() == original.read_bytes()


def test_retain_link_falls_back_to_copy(store_root, tmp_path, monkeypatch):
    original = _audio(tmp_path)

    def no_links(*_args):
        raise OSError("cross-device link")

    monkeypatch.setattr(trim_store.os, "link", no_links)
    source = trim_store.retain_source(42, original, title="Upload", performer=None, duration_sec=60, link=True)
    assert source is not None
    assert original.exists()
    assert source.path.exists()


def test_retain_rejects_unsupported_extension(store_root, tmp_path):
    original = _audio(tmp_path, name="voice.ogg")
    assert trim_store.retain_source(42, original, title="Voice", performer=None, duration_sec=5) is None
    assert original.exists()


def test_retain_rejects_when_disk_is_low(store_root, tmp_path, monkeypatch):
    monkeypatch.setattr(trim_store.shutil, "disk_usage", lambda _p: _Usage(100 * 1024**3, 0, 4 * 1024**3))
    original = _audio(tmp_path)
    assert trim_store.retain_source(42, original, title="X", performer=None, duration_sec=5) is None
    assert original.exists()


def test_load_source_round_trips_after_restart(store_root, tmp_path):
    retained = _retain(tmp_path, performer="Host")
    assert trim_store.load_source(42, retained.token) == retained


def test_load_source_rejects_other_chat(store_root, tmp_path):
    retained = _retain(tmp_path)
    assert trim_store.load_source(43, retained.token) is None


@pytest.mark.parametrize("token", ["../../etc", "short", "a" * 12, "", None, "abc/def_ghi"])
def test_load_source_rejects_malformed_tokens(store_root, token):
    assert trim_store.load_source(42, token) is None


def test_load_source_expires_after_retention(store_root, tmp_path, monkeypatch):
    retained = _retain(tmp_path)
    later = retained.created_at + timedelta(hours=24, seconds=1)
    monkeypatch.setattr(trim_store, "_now", lambda: later)
    assert trim_store.load_source(42, retained.token) is None


def test_load_source_returns_none_when_file_was_cleaned(store_root, tmp_path):
    retained = _retain(tmp_path)
    retained.path.unlink()
    assert trim_store.load_source(42, retained.token) is None


def test_expires_at_is_24h_after_creation(store_root, tmp_path):
    retained = _retain(tmp_path)
    assert trim_store.expires_at(retained) - retained.created_at == timedelta(hours=24)


def test_discard_source_removes_workspace(store_root, tmp_path):
    retained = _retain(tmp_path)
    trim_store.discard_source(retained)
    assert not retained.workspace.exists()


def test_purge_expired_sources_keeps_live_and_removes_dead(store_root, tmp_path):
    live = _retain(tmp_path, "a.mp3", title="Live")
    expired = _retain(tmp_path, "b.mp3", title="Old")
    orphan = _retain(tmp_path, "c.mp3", title="Orphan")
    orphan.path.unlink()  # what cleanup_old_files does to files older than 24 h

    meta_path = expired.workspace / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["created_at"] = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    # Age every workspace past the creation grace period.
    old = (datetime.now() - timedelta(hours=1)).timestamp()
    for source in (live, expired, orphan):
        os.utime(source.workspace, (old, old))

    assert trim_store.purge_expired_sources(store_root / "42") == 2
    assert live.workspace.exists()
    assert not expired.workspace.exists()
    assert not orphan.workspace.exists()


def test_purge_skips_young_workspace_without_meta(store_root):
    young = store_root / "42" / "trim_AAAAAAAAAAA"
    young.mkdir(parents=True)
    assert trim_store.purge_expired_sources(store_root / "42") == 0
    assert young.exists()


def test_purge_ignores_other_directories(store_root):
    other = store_root / "42" / "pl_something"
    other.mkdir(parents=True)
    assert trim_store.purge_expired_sources(store_root / "42") == 0
    assert other.exists()
