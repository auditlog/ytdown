"""Unit tests for bot.services.spotify_service."""

from pathlib import Path

from bot.services import spotify_service as ss


def test_get_resolution_error_message_for_missing_result():
    message = ss.get_resolution_error_message(None)
    assert message is not None
    assert "Nie udało się znaleźć" in message


def test_get_resolution_error_message_for_missing_credentials():
    message = ss.get_resolution_error_message({"source": "no_credentials"})
    assert message is not None
    assert "SPOTIFY_CLIENT_ID" in message


def test_build_episode_caption_data_formats_duration():
    data = ss.build_episode_caption_data(
        {
            "source": "itunes",
            "title": "Episode",
            "show_name": "Podcast",
            "duration": 125,
        }
    )
    assert data["title"] == "Episode"
    assert data["show_name"] == "Podcast"
    assert data["duration_str"] == "2:05"
    assert data["source_label"] == "iTunes"


def test_download_resolved_audio_uses_direct_audio(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ss,
        "download_direct_audio",
        lambda audio_url, output_path: f"{output_path}.mp3",
    )

    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as executor:
        result = asyncio.run(
            ss.download_resolved_audio(
                resolved={"source": "itunes", "title": "Episode", "audio_url": "https://example.com/a.mp3"},
                audio_format="mp3",
                output_dir=str(tmp_path),
                executor=executor,
            )
        )

    assert result.endswith(".mp3")


def test_download_resolved_audio_finds_youtube_output(monkeypatch, tmp_path):
    created = tmp_path / "Episode.mp3"
    created.write_text("audio", encoding="utf-8")

    class MockYoutubeDL:
        def __init__(self, opts):
            self.opts = opts

        def download(self, urls):
            return None

    monkeypatch.setattr(ss.yt_dlp, "YoutubeDL", MockYoutubeDL)

    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as executor:
        result = asyncio.run(
            ss.download_resolved_audio(
                resolved={"source": "youtube", "title": "Episode", "youtube_url": "https://youtube.com/watch?v=abc"},
                audio_format="mp3",
                output_dir=str(tmp_path),
                executor=executor,
            )
        )

    assert result == str(created)
