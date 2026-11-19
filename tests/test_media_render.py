"""Real FFmpeg verification of cut lengths, portrait render and burned captions."""

import asyncio
import json
import shutil
import subprocess

import pytest

from bot.media import store
from bot.media.analysis import Selection
from bot.media.render import assemble_audio, render_short
from bot.media.store import MediaStore
from bot.media.timeline import Segment


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is not installed")
def test_real_audio_assembly_and_captioned_portrait_video(tmp_path, monkeypatch):
    # Exercise rendering independently of the host's available temporary space.
    monkeypatch.setattr(store, "MIN_FREE_BYTES", 0)
    original = tmp_path / "original.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x240:r=5:d=65",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100:duration=65",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-threads",
            "1",
            "-c:a",
            "aac",
            "-shortest",
            str(original),
        ],
        check=True,
        timeout=60,
        capture_output=True,
    )
    library = MediaStore(tmp_path / "library")
    identity, _ = library.reserve(7)
    cue = Segment("s1", 5, 40, "Próbny napis ze źródła.")
    library.commit(
        7,
        identity,
        title="Test",
        url="",
        kind="video",
        duration=65,
        segments=[cue],
        media_path=original,
    )
    picks = [
        Selection(identity, 5, 10, "Pierwszy", (cue,)),
        Selection(identity, 20, 25, "Drugi", (cue,)),
    ]
    output = tmp_path / "montaz.mp3"
    asyncio.run(assemble_audio(library, 7, picks, output))

    def probe(path):
        return json.loads(
            subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration:stream=width,height",
                    "-of",
                    "json",
                    str(path),
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )

    assert 9.8 <= float(probe(output)["format"]["duration"]) <= 10.3
    clip = tmp_path / "clip.mp4"
    asyncio.run(render_short(library, 7, Selection(identity, 5, 40, "Test", (cue,)), clip))
    metadata = probe(clip)
    assert any(
        stream.get("width") == 720 and stream.get("height") == 1280
        for stream in metadata["streams"]
    )
    assert 34.8 <= float(metadata["format"]["duration"]) <= 35.3
    assert "00:00:00,000 --> 00:00:35,000" in clip.with_suffix(".srt").read_text()
