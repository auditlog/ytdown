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
    is_valid_audio_quality,
    normalize_format_id,
    validate_url,
    parse_time_seconds,
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


def test_is_valid_audio_quality():
    assert is_valid_audio_quality("mp3", "192") is True
    assert is_valid_audio_quality("mp3", 330) is True
    assert is_valid_audio_quality("mp3", -1) is False
    assert is_valid_audio_quality("mp3", 331) is False
    assert is_valid_audio_quality("opus", 4) is True
    assert is_valid_audio_quality("opus", 10) is False
    assert is_valid_audio_quality("flac", 256) is True
    assert is_valid_audio_quality("flac", "bad") is False


def test_parse_time_seconds():
    assert parse_time_seconds("5") == 5
    assert parse_time_seconds("1:30") == 90
    assert parse_time_seconds("1:02:03") == 3723
    assert parse_time_seconds(45) == 45
    assert parse_time_seconds(45.9) == 45


def test_parse_time_seconds_rejects_invalid():
    assert parse_time_seconds(None) is None
    assert parse_time_seconds(True) is None
    assert parse_time_seconds("") is None
    assert parse_time_seconds("1:xx") is None
    assert parse_time_seconds("1:2:3:4") is None
    assert parse_time_seconds("bad") is None
    assert parse_time_seconds(-1) is None


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


def test_download_youtube_video_rejects_invalid_audio_quality(monkeypatch):
    class MockYoutubeDL:
        def __init__(self, opts):
            raise AssertionError("yt-dlp should not be called for invalid audio quality")

    monkeypatch.setattr("yt_dlp.YoutubeDL", MockYoutubeDL)
    assert download_youtube_video(
        "https://youtube.com/watch?v=test",
        audio_only=True,
        audio_format="mp3",
        audio_quality="500",
    ) is False


def test_download_youtube_video_rejects_invalid_time_range(monkeypatch):
    class MockYoutubeDL:
        def __init__(self, opts):
            raise AssertionError("yt-dlp should not be called for invalid time range")

    monkeypatch.setattr("yt_dlp.YoutubeDL", MockYoutubeDL)
    assert download_youtube_video("https://youtube.com/watch?v=test", time_range_start="1:00", time_range_end="0:59") is False
    assert download_youtube_video("https://youtube.com/watch?v=test", time_range_start="1:00") is False
    assert download_youtube_video("https://youtube.com/watch?v=test", time_range_end="2:00") is False
    assert download_youtube_video("https://youtube.com/watch?v=test", time_range_start="bad", time_range_end="2:00") is False


def test_download_youtube_video_sets_download_sections(monkeypatch):
    captured = {}

    class MockYoutubeDL:
        def __init__(self, opts):
            captured["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download):
            assert download is True
            return {"title": "sample"}

    monkeypatch.setattr("yt_dlp.YoutubeDL", MockYoutubeDL)

    assert download_youtube_video("https://youtube.com/watch?v=test", time_range_start="0:10", time_range_end="0:20") is True
    assert captured["opts"]["download_sections"] == [{"start_time": 10, "end_time": 20}]
    assert captured["opts"]["force_keyframes_at_cuts"] is True


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


class TestDurationValidation:
    """Tests for video_duration parameter in download_youtube_video."""

    def test_start_beyond_duration_rejected(self, monkeypatch):
        class MockYoutubeDL:
            def __init__(self, opts):
                raise AssertionError("yt-dlp should not be called")

        monkeypatch.setattr("yt_dlp.YoutubeDL", MockYoutubeDL)
        # Video is 60s, start=70s — should fail
        result = download_youtube_video(
            "https://youtube.com/watch?v=test",
            time_range_start="1:10",
            time_range_end="1:30",
            video_duration=60,
        )
        assert result is False

    def test_end_beyond_duration_rejected(self, monkeypatch):
        class MockYoutubeDL:
            def __init__(self, opts):
                raise AssertionError("yt-dlp should not be called")

        monkeypatch.setattr("yt_dlp.YoutubeDL", MockYoutubeDL)
        # Video is 120s, end=150s — should fail
        result = download_youtube_video(
            "https://youtube.com/watch?v=test",
            time_range_start="0:10",
            time_range_end="2:30",
            video_duration=120,
        )
        assert result is False

    def test_valid_range_within_duration_accepted(self, monkeypatch):
        captured = {}

        class MockYoutubeDL:
            def __init__(self, opts):
                captured["opts"] = opts

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def extract_info(self, url, download):
                return {"title": "test"}

        monkeypatch.setattr("yt_dlp.YoutubeDL", MockYoutubeDL)
        # Video is 300s, range 10-60 — should succeed
        result = download_youtube_video(
            "https://youtube.com/watch?v=test",
            time_range_start="0:10",
            time_range_end="1:00",
            video_duration=300,
        )
        assert result is True
        assert captured["opts"]["download_sections"] == [{"start_time": 10, "end_time": 60}]

    def test_no_duration_check_when_none(self, monkeypatch):
        captured = {}

        class MockYoutubeDL:
            def __init__(self, opts):
                captured["opts"] = opts

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def extract_info(self, url, download):
                return {"title": "test"}

        monkeypatch.setattr("yt_dlp.YoutubeDL", MockYoutubeDL)
        # video_duration=None — no check, download proceeds
        result = download_youtube_video(
            "https://youtube.com/watch?v=test",
            time_range_start="0:10",
            time_range_end="99:00",
            video_duration=None,
        )
        assert result is True

    def test_start_equals_duration_rejected(self, monkeypatch):
        class MockYoutubeDL:
            def __init__(self, opts):
                raise AssertionError("yt-dlp should not be called")

        monkeypatch.setattr("yt_dlp.YoutubeDL", MockYoutubeDL)
        # Video is 60s, start=60s — at boundary, should fail
        result = download_youtube_video(
            "https://youtube.com/watch?v=test",
            time_range_start="1:00",
            time_range_end="1:30",
            video_duration=60,
        )
        assert result is False
