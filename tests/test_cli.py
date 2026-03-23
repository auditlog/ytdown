"""
Unit tests for CLI helpers.
"""

from argparse import Namespace
from unittest.mock import Mock

from bot import cli


def test_show_help_prints_instructions(capsys):
    cli.show_help()
    out = capsys.readouterr().out

    assert "YouTube Downloader - tool for downloading YouTube videos" in out
    assert "--audio-only" in out
    assert "--list-formats" in out


def test_cli_mode_without_url_prints_help(monkeypatch):
    args = Namespace(url=None, list_formats=False, format=None, audio_only=False, audio_format='mp3', audio_quality='192')

    shown = {"called": False}
    monkeypatch.setattr(cli, "show_help", lambda: shown.__setitem__("called", True))

    cli.cli_mode(args)

    assert shown["called"] is True


def test_cli_mode_invalid_url_skips_download(monkeypatch):
    args = Namespace(url="invalid", list_formats=False, format=None, audio_only=False, audio_format='mp3', audio_quality='192')
    execute_mock = Mock()

    monkeypatch.setattr(cli, "validate_url", lambda _url: False)
    monkeypatch.setattr(cli, "execute_download_plan", execute_mock)

    cli.cli_mode(args)

    execute_mock.assert_not_called()


def test_cli_mode_lists_formats(capsys, monkeypatch):
    args = Namespace(url="https://www.youtube.com/watch?v=ok", list_formats=True, format=None, audio_only=False, audio_format='mp3', audio_quality='192')

    sample_info = {
        "title": "Sample Video",
        "formats": [
            {
                "format_id": "22",
                "ext": "mp4",
                "resolution": "720p",
                "filesize": 10 * 1024 * 1024,
                "vcodec": "avc1",
                "format_note": "360p",
            },
            {
                "format_id": "140",
                "ext": "m4a",
                "resolution": "N/A",
                "filesize": 5 * 1024 * 1024,
                "vcodec": "none",
                "format_note": "audio",
            },
        ],
    }

    monkeypatch.setattr(cli, "get_video_info", lambda _url: sample_info)
    cli.cli_mode(args)

    out = capsys.readouterr().out
    assert "Available formats:" in out
    assert "22" in out
    assert "140" in out


def test_cli_mode_downloads_with_arguments(monkeypatch):
    args = Namespace(
        url="https://youtube.com/watch?v=ok",
        list_formats=False,
        format="1080p",
        audio_only=True,
        audio_format='wav',
        audio_quality='4',
        start=None,
        to=None,
    )
    execute_mock = Mock()
    captured = {}

    monkeypatch.setattr(cli, "validate_url", lambda _url: True)
    monkeypatch.setattr(cli, "prepare_download_plan", lambda **kwargs: captured.setdefault("kwargs", kwargs) or object())
    monkeypatch.setattr(cli, "execute_download_plan", execute_mock)
    monkeypatch.setattr(cli.os, "getcwd", lambda: "/tmp")
    monkeypatch.setattr(cli.os, "makedirs", lambda *_args, **_kwargs: None)

    cli.cli_mode(args)

    assert captured["kwargs"] == {
        "url": "https://youtube.com/watch?v=ok",
        "media_type": "audio",
        "format_choice": "wav",
        "chat_download_path": "/tmp",
        "time_range": None,
        "transcribe": False,
        "use_format_id": True,
        "audio_quality": "4",
    }
    execute_mock.assert_called_once()


def test_cli_mode_rejects_invalid_audio_format(monkeypatch):
    args = Namespace(url="https://youtube.com/watch?v=ok", list_formats=False, format=None, audio_only=True, audio_format="invalid", audio_quality='192')
    execute_mock = Mock()

    monkeypatch.setattr(cli, "validate_url", lambda _url: True)
    monkeypatch.setattr(cli, "execute_download_plan", execute_mock)

    cli.cli_mode(args)

    execute_mock.assert_not_called()


def test_cli_mode_rejects_invalid_format(monkeypatch):
    args = Namespace(url="https://youtube.com/watch?v=ok", list_formats=False, format="bad-format", audio_only=False, audio_format='mp3', audio_quality='192')
    execute_mock = Mock()

    monkeypatch.setattr(cli, "validate_url", lambda _url: True)
    monkeypatch.setattr(cli, "execute_download_plan", execute_mock)

    cli.cli_mode(args)

    execute_mock.assert_not_called()


def test_cli_mode_rejects_invalid_audio_quality(monkeypatch):
    args = Namespace(url="https://youtube.com/watch?v=ok", list_formats=False, format=None, audio_only=True, audio_format="mp3", audio_quality='999')
    execute_mock = Mock()

    monkeypatch.setattr(cli, "validate_url", lambda _url: True)
    monkeypatch.setattr(cli, "execute_download_plan", execute_mock)

    cli.cli_mode(args)

    execute_mock.assert_not_called()


def test_cli_mode_rejects_unpaired_start_or_to(monkeypatch):
    args = Namespace(
        url="https://youtube.com/watch?v=ok",
        list_formats=False,
        format=None,
        audio_only=False,
        audio_format='mp3',
        audio_quality='192',
        start='0:30',
        to=None,
    )
    execute_mock = Mock()

    monkeypatch.setattr(cli, "validate_url", lambda _url: True)
    monkeypatch.setattr(cli, "execute_download_plan", execute_mock)

    cli.cli_mode(args)

    execute_mock.assert_not_called()


def test_cli_mode_rejects_invalid_time_range(monkeypatch):
    args = Namespace(
        url="https://youtube.com/watch?v=ok",
        list_formats=False,
        format=None,
        audio_only=False,
        audio_format='mp3',
        audio_quality='192',
        start='bad',
        to='1:00',
    )
    execute_mock = Mock()

    monkeypatch.setattr(cli, "validate_url", lambda _url: True)
    monkeypatch.setattr(cli, "execute_download_plan", execute_mock)

    cli.cli_mode(args)

    execute_mock.assert_not_called()


def test_cli_mode_rejects_start_after_end(monkeypatch):
    args = Namespace(
        url="https://youtube.com/watch?v=ok",
        list_formats=False,
        format=None,
        audio_only=False,
        audio_format='mp3',
        audio_quality='192',
        start='2:00',
        to='1:00',
    )
    execute_mock = Mock()

    monkeypatch.setattr(cli, "validate_url", lambda _url: True)
    monkeypatch.setattr(cli, "execute_download_plan", execute_mock)

    cli.cli_mode(args)

    execute_mock.assert_not_called()


def test_cli_mode_downloads_with_time_range(monkeypatch):
    args = Namespace(
        url="https://youtube.com/watch?v=ok",
        list_formats=False,
        format="1080p",
        audio_only=False,
        audio_format='mp3',
        audio_quality='192',
        start='0:30',
        to='1:00',
    )
    execute_mock = Mock()
    captured = {}

    monkeypatch.setattr(cli, "validate_url", lambda _url: True)
    monkeypatch.setattr(cli, "prepare_download_plan", lambda **kwargs: captured.setdefault("kwargs", kwargs) or object())
    monkeypatch.setattr(cli, "execute_download_plan", execute_mock)
    monkeypatch.setattr(cli.os, "getcwd", lambda: "/tmp")
    monkeypatch.setattr(cli.os, "makedirs", lambda *_args, **_kwargs: None)

    cli.cli_mode(args)

    assert captured["kwargs"] == {
        "url": "https://youtube.com/watch?v=ok",
        "media_type": "video",
        "format_choice": "1080p",
        "chat_download_path": "/tmp",
        "time_range": {
            "start": "0:30",
            "end": "1:00",
            "start_sec": 30,
            "end_sec": 60,
        },
        "transcribe": False,
        "use_format_id": False,
        "audio_quality": "192",
    }
    execute_mock.assert_called_once()
