"""Tests for ffmpeg-based fragment cutting."""

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.handlers.time_range import ResolvedRange
from bot.services import audio_trim_service as trim

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)

_ENCODERS = {
    ".mp3": ["-c:a", "libmp3lame", "-q:a", "5"],
    ".m4a": ["-c:a", "aac", "-b:a", "96k"],
    ".flac": ["-c:a", "flac"],
}


def _make_tone(path: Path, seconds: int = 10) -> Path:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f=440:d={seconds}",
         *_ENCODERS[path.suffix], str(path)],
        check=True,
    )
    return path


def _probe_json(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:format_tags=title:stream=codec_type", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(out)


def test_fragment_label_and_filename():
    fragment = ResolvedRange(720, 930, False)
    assert trim.fragment_label(fragment) == "12:00–15:30"
    assert trim.fragment_filename("Podcast: #120", fragment, ".mp3") == "Podcast- #120 [12-00–15-30].mp3"


def test_fragment_filename_caps_long_titles():
    name = trim.fragment_filename("x" * 300, ResolvedRange(0, 5, False), ".mp3")
    assert name.endswith(".mp3")
    assert len(name) <= 204


def test_probe_duration_reports_missing_binary(monkeypatch, tmp_path):
    async def missing(*args, **kwargs):
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr(trim.asyncio, "create_subprocess_exec", missing)
    with pytest.raises(trim.AudioTrimError):
        asyncio.run(trim.probe_duration(tmp_path / "x.mp3"))


def test_run_attaches_and_detaches_process(monkeypatch):
    seen = {}
    cancellation = SimpleNamespace(process=None)

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            seen["attached"] = cancellation.process is self
            return b"", b""

    async def fake_exec(*cmd, **kwargs):
        return FakeProcess()

    monkeypatch.setattr(trim.asyncio, "create_subprocess_exec", fake_exec)
    asyncio.run(trim._run(["ffmpeg"], cancellation=cancellation))
    assert seen["attached"] is True
    assert cancellation.process is None


@needs_ffmpeg
@pytest.mark.parametrize("ext", [".mp3", ".m4a", ".flac"])
def test_cut_fragment_produces_requested_length(tmp_path, ext):
    source = _make_tone(tmp_path / f"source{ext}")
    dest = tmp_path / f"out{ext}"
    asyncio.run(trim.cut_fragment(source, ResolvedRange(2, 5, False), dest, title_tag="Tone [0:02–0:05]"))
    assert asyncio.run(trim.probe_duration(dest)) == pytest.approx(3.0, abs=0.15)


@needs_ffmpeg
def test_cut_fragment_open_end_runs_to_eof(tmp_path):
    source = _make_tone(tmp_path / "source.mp3")
    dest = tmp_path / "out.mp3"
    asyncio.run(trim.cut_fragment(source, ResolvedRange(7, 10, True), dest, title_tag="Tone"))
    assert asyncio.run(trim.probe_duration(dest)) == pytest.approx(3.0, abs=0.15)


@needs_ffmpeg
def test_cut_fragment_writes_title_tag(tmp_path):
    source = _make_tone(tmp_path / "source.mp3")
    dest = tmp_path / "out.mp3"
    asyncio.run(trim.cut_fragment(source, ResolvedRange(1, 2, False), dest, title_tag="Tone [0:01–0:02]"))
    assert _probe_json(dest)["format"]["tags"]["title"] == "Tone [0:01–0:02]"


@needs_ffmpeg
def test_cut_fragment_keeps_mp3_cover(tmp_path):
    tone = _make_tone(tmp_path / "tone.mp3")
    cover = tmp_path / "cover.png"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=16x16",
         "-frames:v", "1", str(cover)],
        check=True,
    )
    source = tmp_path / "source.mp3"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(tone), "-i", str(cover), "-map", "0:a",
         "-map", "1:v", "-c", "copy", "-id3v2_version", "3", "-disposition:v", "attached_pic",
         str(source)],
        check=True,
    )
    dest = tmp_path / "out.mp3"
    asyncio.run(trim.cut_fragment(source, ResolvedRange(1, 4, False), dest, title_tag="Tone"))
    codec_types = [stream["codec_type"] for stream in _probe_json(dest)["streams"]]
    assert codec_types.count("video") == 1


@needs_ffmpeg
def test_cut_fragment_retries_without_cover_when_first_attempt_fails(tmp_path, monkeypatch):
    calls = []
    real_run = trim._run

    async def flaky_run(cmd, **kwargs):
        calls.append(cmd)
        if "0:v:0?" in cmd:
            return 1, b"", b"Could not write header"
        return await real_run(cmd, **kwargs)

    monkeypatch.setattr(trim, "_run", flaky_run)
    source = _make_tone(tmp_path / "source.m4a")
    dest = tmp_path / "out.m4a"
    asyncio.run(trim.cut_fragment(source, ResolvedRange(1, 3, False), dest, title_tag="Tone"))
    assert len(calls) == 2
    assert "0:v:0?" not in calls[1]
    assert dest.exists()


@needs_ffmpeg
def test_cut_fragment_raises_on_failure(tmp_path):
    bogus = tmp_path / "bogus.mp3"
    bogus.write_bytes(b"not audio")
    with pytest.raises(trim.AudioTrimError):
        asyncio.run(trim.cut_fragment(bogus, ResolvedRange(1, 2, False), tmp_path / "out.mp3", title_tag="x"))


@needs_ffmpeg
def test_probe_duration_raises_on_garbage(tmp_path):
    bogus = tmp_path / "bogus.mp3"
    bogus.write_bytes(b"not audio")
    with pytest.raises(trim.AudioTrimError):
        asyncio.run(trim.probe_duration(bogus))
