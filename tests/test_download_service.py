"""Unit tests for bot.services.download_service."""

from pathlib import Path

import pytest

from bot.services import download_service as ds


def test_prepare_download_plan_builds_audio_transcription_plan(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ds,
        "get_video_info",
        lambda url: {"title": "Test Video", "duration": 125},
    )
    monkeypatch.setattr(ds.os.path, "exists", lambda path: False)

    plan = ds.prepare_download_plan(
        url="https://www.youtube.com/watch?v=abc",
        media_type="audio",
        format_choice="mp3",
        chat_download_path=str(tmp_path),
        time_range={"start": "0:10", "end": "0:20", "start_sec": 10, "end_sec": 20},
        transcribe=True,
    )

    assert plan is not None
    assert plan.title == "Test Video"
    assert plan.duration == 125
    assert plan.duration_str == "2:05"
    assert plan.ydl_opts["format"] == "bestaudio/best"
    assert plan.ydl_opts["postprocessors"][0]["preferredcodec"] == "mp3"
    assert plan.ydl_opts["force_keyframes_at_cuts"] is True


def test_prepare_download_plan_rejects_invalid_audio_quality(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ds,
        "get_video_info",
        lambda url: {"title": "Test Video", "duration": 125},
    )
    monkeypatch.setattr(ds.os.path, "exists", lambda path: False)

    with pytest.raises(ValueError, match="invalid_audio_quality"):
        ds.prepare_download_plan(
            url="https://www.youtube.com/watch?v=abc",
            media_type="audio",
            format_choice="mp3",
            chat_download_path=str(tmp_path),
            audio_quality="999",
        )


def test_estimate_download_size_adjusts_for_time_range(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ds,
        "get_video_info",
        lambda url: {"title": "Test Video", "duration": 100},
    )
    monkeypatch.setattr(ds.os.path, "exists", lambda path: False)

    plan = ds.prepare_download_plan(
        url="https://www.youtube.com/watch?v=abc",
        media_type="video",
        format_choice="best",
        chat_download_path=str(tmp_path),
        time_range={"start": "0:10", "end": "0:40", "start_sec": 10, "end_sec": 40},
    )

    class MockYoutubeDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download):
            return {"filesize": 100 * 1024 * 1024}

    monkeypatch.setattr(ds.yt_dlp, "YoutubeDL", MockYoutubeDL)

    size_mb = ds.estimate_download_size(plan)
    assert size_mb == 30.0


def test_find_downloaded_file_skips_artifacts(monkeypatch, tmp_path):
    plan = ds.DownloadPlan(
        url="https://www.youtube.com/watch?v=abc",
        media_type="audio",
        format_choice="mp3",
        transcribe=False,
        use_format_id=False,
        audio_quality="192",
        info={"title": "Sample", "duration": 10},
        title="Sample",
        duration=10,
        duration_str="0:10",
        sanitized_title="Sample",
        output_path=str(tmp_path / "2026-03-21 Sample"),
        chat_download_path=str(tmp_path),
        ydl_opts={},
        time_range=None,
    )

    artifact = tmp_path / "2026-03-21 Sample_transcript.md"
    media = tmp_path / "2026-03-21 Sample.mp3"
    artifact.write_text("artifact", encoding="utf-8")
    media.write_text("media", encoding="utf-8")

    found = ds.find_downloaded_file(plan)
    assert found == str(media)
