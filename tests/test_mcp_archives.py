"""Large MCP artifacts: real multipart archives, safe failures and restart cleanup."""

import asyncio
import hashlib
import json
import shutil
import subprocess
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from bot.mcp.artifacts import media_artifacts
from bot.mcp.jobs import JobStore
from bot.mcp.policy import Limits, UserError


def test_large_artifact_round_trips_through_real_7z(tmp_path):
    if not shutil.which("7z"):
        pytest.skip("7z unavailable")
    media = tmp_path / "media.mp4"
    payload = b"video payload" * 250000
    media.write_bytes(payload)
    result = media_artifacts(media, asdict(Limits(archive_volume_mb=1, min_free_bytes=1)))
    assert not media.exists()
    assert len(result["archive"]["parts"]) >= 3
    assert result["archive"]["original_size_bytes"] == len(payload)
    assert "rozpakowanie.txt" in result["artifacts"]
    assert all((tmp_path / name).stat().st_size <= 1024**2 for name in result["archive"]["parts"])
    extracted = tmp_path / "extracted"
    subprocess.run(
        ["7z", "x", str(tmp_path / result["archive"]["parts"][0]), f"-o{extracted}", "-y"],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    assert hashlib.sha256((extracted / "media.mp4").read_bytes()).digest() == hashlib.sha256(payload).digest()


def test_small_artifact_is_kept_without_7z(tmp_path, monkeypatch):
    monkeypatch.setattr("bot.mcp.artifacts.is_7z_available", lambda: False)
    media = tmp_path / "media.mp4"
    media.write_bytes(b"video")
    assert media_artifacts(media, asdict(Limits())) == {"artifacts": ["media.mp4"]}
    assert media.exists()


@pytest.mark.parametrize("failure", ["size", "missing_7z", "space"])
def test_archive_failures_do_not_advertise_partial_outputs(tmp_path, monkeypatch, failure):
    media = tmp_path / "media.mp4"
    media.write_bytes(b"x" * (2 * 1024**2))
    limits = Limits(archive_volume_mb=1, max_media_bytes=1 if failure == "size" else 10 * 1024**3)
    monkeypatch.setattr("bot.mcp.artifacts.is_7z_available", lambda: failure != "missing_7z")
    if failure == "space":
        monkeypatch.setattr("bot.archive.shutil.disk_usage", lambda _: SimpleNamespace(free=1))
    with pytest.raises(UserError):
        media_artifacts(media, asdict(limits))
    assert media.exists()
    assert not list(tmp_path.glob("*.7z.*"))


def test_restart_removes_interrupted_archive_and_source(tmp_path):
    job_id = "e" * 32
    directory = tmp_path / job_id
    directory.mkdir()
    (directory / "job.json").write_text(json.dumps({
        "job_id": job_id, "status": "running", "created_at": 1, "artifacts": [],
    }))
    (directory / "media.mp4").write_bytes(b"source")
    (directory / "media.7z.001").write_bytes(b"partial archive")

    async def run():
        store = JobStore(tmp_path)
        await store.open()
        try:
            assert store.get(job_id)["status"] == "failed"
            assert sorted(p.name for p in directory.iterdir()) == ["job.json"]
        finally:
            await store.close()

    asyncio.run(run())
