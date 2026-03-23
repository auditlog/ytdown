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


def test_execute_download_plan_runs_ytdlp_and_returns_result(monkeypatch, tmp_path):
    media = tmp_path / "2026-03-21 Sample.mp3"
    media.write_text("media", encoding="utf-8")

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
        ydl_opts={"format": "bestaudio/best"},
        time_range=None,
    )

    called = {}

    class MockYoutubeDL:
        def __init__(self, opts):
            called["opts"] = opts

        def download(self, urls):
            called["urls"] = urls
            return 0

    monkeypatch.setattr(ds.yt_dlp, "YoutubeDL", MockYoutubeDL)

    result = ds.execute_download_plan(plan)

    assert called["opts"] == {"format": "bestaudio/best"}
    assert called["urls"] == ["https://www.youtube.com/watch?v=abc"]
    assert result.file_path == str(media)
    assert result.file_size_mb > 0


def test_execute_download_plan_raises_when_ytdlp_raises_download_error(monkeypatch, tmp_path):
    """yt-dlp raising DownloadError during execute_download_plan should propagate."""

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
        ydl_opts={"format": "bestaudio/best"},
        time_range=None,
    )

    class MockYoutubeDL:
        def __init__(self, opts):
            pass

        def download(self, urls):
            raise ds.yt_dlp.utils.DownloadError("Video unavailable")

    monkeypatch.setattr(ds.yt_dlp, "YoutubeDL", MockYoutubeDL)

    with pytest.raises(ds.yt_dlp.utils.DownloadError):
        ds.execute_download_plan(plan)


def test_find_downloaded_file_returns_none_for_empty_directory(tmp_path):
    """find_downloaded_file should return None when the download directory is empty."""

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

    # tmp_path is empty — no files at all
    found = ds.find_downloaded_file(plan)

    assert found is None


def test_find_downloaded_file_returns_none_when_only_artifacts_present(tmp_path):
    """find_downloaded_file should return None when only transcript/summary artifacts exist."""

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

    # Only artifact files, no media file
    (tmp_path / "2026-03-21 Sample_transcript.md").write_text("t", encoding="utf-8")
    (tmp_path / "2026-03-21 Sample_summary.md").write_text("s", encoding="utf-8")

    found = ds.find_downloaded_file(plan)

    assert found is None


def test_prepare_download_plan_returns_none_when_video_info_unavailable(monkeypatch, tmp_path):
    """prepare_download_plan should return None when get_video_info returns None (unreachable URL)."""

    monkeypatch.setattr(ds, "get_video_info", lambda url: None)

    plan = ds.prepare_download_plan(
        url="https://www.youtube.com/watch?v=INVALID",
        media_type="audio",
        format_choice="mp3",
        chat_download_path=str(tmp_path),
    )

    assert plan is None


def test_execute_download_plan_raises_file_not_found_when_no_file_produced(monkeypatch, tmp_path):
    """execute_download_plan should raise FileNotFoundError when yt-dlp produces no output file."""

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

    class MockYoutubeDL:
        def __init__(self, opts):
            pass

        def download(self, urls):
            # Succeeds but produces no file on disk
            return 0

    monkeypatch.setattr(ds.yt_dlp, "YoutubeDL", MockYoutubeDL)

    with pytest.raises(FileNotFoundError, match="downloaded file not found"):
        ds.execute_download_plan(plan)
