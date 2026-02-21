"""
Unit tests for downloader helpers.
"""

from bot.downloader import (
    sanitize_filename,
    progress_hook,
    get_basic_ydl_opts,
    get_video_info,
    download_youtube_video,
    is_valid_ytdlp_format_id,
    is_valid_audio_format,
    normalize_format_id,
    validate_url,
)


def test_sanitize_filename_replaces_invalid_chars():
    assert sanitize_filename("Test/Video:Name?*|<>\"") == "Test-Video-Name------"


def test_progress_hook_prints_status(capsys):
    progress_hook({
        "status": "downloading",
        "downloaded_bytes": 1024 * 1024,
        "total_bytes": 2 * 1024 * 1024,
    })
    captured = capsys.readouterr()
    assert "Downloading:" in captured.out
    assert "1.0MB / 2.0MB" in captured.out


def test_progress_hook_prints_estimated_status(capsys):
    progress_hook({
        "status": "downloading",
        "downloaded_bytes": 1024 * 1024,
        "total_bytes_estimate": 2 * 1024 * 1024,
    })
    captured = capsys.readouterr()
    assert "estimated 2.0MB" in captured.out


def test_progress_hook_finished_and_error(capsys):
    progress_hook({"status": "finished"})
    progress_hook({"status": "error", "error": "boom"})
    captured = capsys.readouterr().out
    assert "Download finished, processing..." in captured
    assert "Error during download: boom" in captured


def test_get_basic_ydl_opts_contains_expected_fields():
    opts = get_basic_ydl_opts()
    assert opts["quiet"] is True
    assert opts["no_warnings"] is True
    assert len(opts["progress_hooks"]) == 1


def test_get_video_info_returns_info(monkeypatch, sample_video_info):
    class MockYoutubeDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download):
            return sample_video_info

    monkeypatch.setattr("yt_dlp.YoutubeDL", MockYoutubeDL)
    assert get_video_info("https://youtube.com/watch?v=test") == sample_video_info


def test_get_video_info_returns_none_on_error(monkeypatch):
    class MockYoutubeDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download):
            raise RuntimeError("boom")

    monkeypatch.setattr("yt_dlp.YoutubeDL", MockYoutubeDL)
    assert get_video_info("https://youtube.com/watch?v=test") is None


def test_download_youtube_video_success_audio_only(monkeypatch):
    captured = {}
    created = []

    class MockYoutubeDL:
        def __init__(self, opts):
            captured["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download):
            assert download is True
            info = {"title": "sample"}
            created.append(url)
            return info

    monkeypatch.setattr("yt_dlp.YoutubeDL", MockYoutubeDL)
    assert download_youtube_video("https://youtube.com/watch?v=test", audio_only=True, audio_format="mp3") is True
    assert created == ["https://youtube.com/watch?v=test"]
    assert captured["opts"]["format"] == "bestaudio/best"
    assert captured["opts"]["postprocessors"][0]["preferredcodec"] == "mp3"


def test_download_youtube_video_success_with_format_id(monkeypatch):
    captured = {}

    class MockYoutubeDL:
        def __init__(self, opts):
            captured["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download):
            return {"title": "sample"}

    monkeypatch.setattr("yt_dlp.YoutubeDL", MockYoutubeDL)
    assert download_youtube_video("https://youtube.com/watch?v=test", format_id="720p") is True
    assert captured["opts"]["format"] == "720p"


def test_is_valid_ytdlp_format_id():
    assert is_valid_ytdlp_format_id("best") is True
    assert is_valid_ytdlp_format_id("bestvideo") is True
    assert is_valid_ytdlp_format_id("1080p") is True
    assert is_valid_ytdlp_format_id("137+140") is True
    assert is_valid_ytdlp_format_id("1080P") is True
    assert is_valid_ytdlp_format_id("best[height<=720]") is False
    assert is_valid_ytdlp_format_id("mp3") is False


def test_is_valid_audio_format():
    assert is_valid_audio_format("mp3") is True
    assert is_valid_audio_format("wav") is True
    assert is_valid_audio_format("ogg") is True
    assert is_valid_audio_format("bad") is False


def test_normalize_format_id():
    assert normalize_format_id(None) is None
    assert normalize_format_id("auto") == "best"
    assert normalize_format_id("1080p") == "1080p"
    assert normalize_format_id("Best") == "best"


def test_download_youtube_video_rejects_invalid_audio_format(monkeypatch):
    class MockYoutubeDL:
        def __init__(self, opts):
            raise AssertionError("yt-dlp should not be called for invalid audio format")

    monkeypatch.setattr("yt_dlp.YoutubeDL", MockYoutubeDL)
    assert download_youtube_video(
        "https://youtube.com/watch?v=test",
        audio_only=True,
        audio_format="invalid",
    ) is False


def test_download_youtube_video_rejects_invalid_format_id(monkeypatch):
    class MockYoutubeDL:
        def __init__(self, opts):
            raise AssertionError("yt-dlp should not be called for invalid format id")

    monkeypatch.setattr("yt_dlp.YoutubeDL", MockYoutubeDL)
    assert download_youtube_video("https://youtube.com/watch?v=test", format_id="bad-format") is False


def test_download_youtube_video_returns_false_on_exception(monkeypatch):
    class MockYoutubeDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download):
            raise RuntimeError("boom")

    monkeypatch.setattr("yt_dlp.YoutubeDL", MockYoutubeDL)
    assert download_youtube_video("https://youtube.com/watch?v=test") is False


def test_validate_url():
    assert validate_url("https://www.youtube.com/watch?v=test")
    assert validate_url("https://youtu.be/test")
    assert not validate_url("http://youtube.com/watch?v=test")
    assert not validate_url("https://example.com/watch?v=test")
