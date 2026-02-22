"""
Tests for YouTube subtitle/caption download and parsing.
"""

import os
import tempfile
import shutil
from unittest.mock import patch, MagicMock

import pytest

from bot.downloader import (
    get_available_subtitles,
    download_subtitles,
    parse_subtitle_file,
)
from bot.telegram_callbacks import _parse_subtitle_callback


# --- get_available_subtitles tests ---


class TestGetAvailableSubtitles:
    """Tests for get_available_subtitles()."""

    def test_no_subtitles(self, sample_video_info):
        """Returns empty when no subtitles exist."""
        result = get_available_subtitles(sample_video_info)
        assert result['has_any'] is False
        assert result['manual'] == {}
        assert result['auto'] == {}

    def test_manual_subtitles_only(self, sample_video_info):
        """Returns manual subtitles when available."""
        sample_video_info['subtitles'] = {
            'en': [{'ext': 'vtt', 'url': 'http://example.com/en.vtt'}],
            'pl': [{'ext': 'vtt', 'url': 'http://example.com/pl.vtt'}],
        }
        result = get_available_subtitles(sample_video_info)
        assert result['has_any'] is True
        assert 'pl' in result['manual']
        assert 'en' in result['manual']
        assert result['auto'] == {}

    def test_auto_subtitles_only(self, sample_video_info):
        """Returns auto captions when no manual subtitles."""
        sample_video_info['automatic_captions'] = {
            'en': [{'ext': 'vtt', 'url': 'http://example.com/en.vtt'}],
            'de': [{'ext': 'vtt', 'url': 'http://example.com/de.vtt'}],
        }
        result = get_available_subtitles(sample_video_info)
        assert result['has_any'] is True
        assert result['manual'] == {}
        assert 'en' in result['auto']
        assert 'de' in result['auto']

    def test_both_manual_and_auto(self, sample_video_info):
        """Returns both manual and auto subtitles."""
        sample_video_info['subtitles'] = {
            'en': [{'ext': 'vtt'}],
        }
        sample_video_info['automatic_captions'] = {
            'pl': [{'ext': 'vtt'}],
        }
        result = get_available_subtitles(sample_video_info)
        assert result['has_any'] is True
        assert 'en' in result['manual']
        assert 'pl' in result['auto']

    def test_priority_languages_first(self, sample_video_info):
        """Polish and English appear before other languages."""
        sample_video_info['subtitles'] = {
            'de': [{'ext': 'vtt'}],
            'fr': [{'ext': 'vtt'}],
            'pl': [{'ext': 'vtt'}],
            'en': [{'ext': 'vtt'}],
            'es': [{'ext': 'vtt'}],
        }
        result = get_available_subtitles(sample_video_info)
        manual_keys = list(result['manual'].keys())
        assert manual_keys[0] == 'pl'
        assert manual_keys[1] == 'en'

    def test_manual_limit_6(self, sample_video_info):
        """Manual subtitles limited to 6 languages."""
        sample_video_info['subtitles'] = {
            lang: [{'ext': 'vtt'}]
            for lang in ['pl', 'en', 'de', 'fr', 'es', 'it', 'pt', 'ja']
        }
        result = get_available_subtitles(sample_video_info)
        assert len(result['manual']) == 6

    def test_auto_limit_4(self, sample_video_info):
        """Auto captions limited to 4 languages."""
        sample_video_info['automatic_captions'] = {
            lang: [{'ext': 'vtt'}]
            for lang in ['pl', 'en', 'de', 'fr', 'es', 'it']
        }
        result = get_available_subtitles(sample_video_info)
        assert len(result['auto']) == 4

    def test_none_info(self):
        """Handles None info gracefully."""
        result = get_available_subtitles(None)
        assert result['has_any'] is False

    def test_empty_dict(self):
        """Handles empty dict gracefully."""
        result = get_available_subtitles({})
        assert result['has_any'] is False

    def test_non_dict_input(self):
        """Handles non-dict input gracefully."""
        result = get_available_subtitles("not a dict")
        assert result['has_any'] is False

    def test_empty_subtitles_dict(self, sample_video_info):
        """Handles empty subtitles dict."""
        sample_video_info['subtitles'] = {}
        sample_video_info['automatic_captions'] = {}
        result = get_available_subtitles(sample_video_info)
        assert result['has_any'] is False


# --- parse_subtitle_file tests ---


class TestParseSubtitleFile:
    """Tests for parse_subtitle_file()."""

    @pytest.fixture
    def temp_dir(self):
        d = tempfile.mkdtemp()
        yield d
        shutil.rmtree(d, ignore_errors=True)

    def _write_file(self, temp_dir, filename, content):
        path = os.path.join(temp_dir, filename)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return path

    def test_parse_vtt_basic(self, temp_dir):
        """Parses basic VTT file."""
        vtt_content = (
            "WEBVTT\n"
            "Kind: captions\n"
            "Language: en\n"
            "\n"
            "00:00:01.000 --> 00:00:04.000\n"
            "Hello world\n"
            "\n"
            "00:00:05.000 --> 00:00:08.000\n"
            "This is a test\n"
        )
        path = self._write_file(temp_dir, "test.vtt", vtt_content)
        result = parse_subtitle_file(path)
        assert "Hello world" in result
        assert "This is a test" in result
        assert "WEBVTT" not in result
        assert "-->" not in result

    def test_parse_srt_basic(self, temp_dir):
        """Parses basic SRT file."""
        srt_content = (
            "1\n"
            "00:00:01,000 --> 00:00:04,000\n"
            "Hello world\n"
            "\n"
            "2\n"
            "00:00:05,000 --> 00:00:08,000\n"
            "This is a test\n"
        )
        path = self._write_file(temp_dir, "test.srt", srt_content)
        result = parse_subtitle_file(path)
        assert "Hello world" in result
        assert "This is a test" in result
        assert "00:00" not in result

    def test_deduplication(self, temp_dir):
        """Deduplicates consecutive identical lines (auto-captions)."""
        vtt_content = (
            "WEBVTT\n"
            "\n"
            "00:00:01.000 --> 00:00:02.000\n"
            "Hello world\n"
            "\n"
            "00:00:02.000 --> 00:00:03.000\n"
            "Hello world\n"
            "\n"
            "00:00:03.000 --> 00:00:04.000\n"
            "Hello world\n"
            "\n"
            "00:00:04.000 --> 00:00:05.000\n"
            "Something new\n"
            "\n"
            "00:00:05.000 --> 00:00:06.000\n"
            "Something new\n"
        )
        path = self._write_file(temp_dir, "test.vtt", vtt_content)
        result = parse_subtitle_file(path)
        lines = result.strip().split('\n')
        assert lines.count("Hello world") == 1
        assert lines.count("Something new") == 1

    def test_html_tags_removal(self, temp_dir):
        """Removes HTML tags from subtitle text."""
        vtt_content = (
            "WEBVTT\n"
            "\n"
            "00:00:01.000 --> 00:00:04.000\n"
            "<c>Hello</c> <b>world</b>\n"
            "\n"
            "00:00:05.000 --> 00:00:08.000\n"
            "<i>Italic text</i> and <font color='red'>colored</font>\n"
        )
        path = self._write_file(temp_dir, "test.vtt", vtt_content)
        result = parse_subtitle_file(path)
        assert "<c>" not in result
        assert "<b>" not in result
        assert "<i>" not in result
        assert "<font" not in result
        assert "Hello" in result
        assert "world" in result
        assert "Italic text" in result

    def test_empty_file(self, temp_dir):
        """Handles empty file."""
        path = self._write_file(temp_dir, "empty.vtt", "")
        result = parse_subtitle_file(path)
        assert result == ""

    def test_nonexistent_file(self):
        """Handles nonexistent file."""
        result = parse_subtitle_file("/nonexistent/path/file.vtt")
        assert result == ""

    def test_none_path(self):
        """Handles None path."""
        result = parse_subtitle_file(None)
        assert result == ""

    def test_vtt_with_note_blocks(self, temp_dir):
        """Skips NOTE blocks in VTT."""
        vtt_content = (
            "WEBVTT\n"
            "\n"
            "NOTE This is a comment\n"
            "\n"
            "00:00:01.000 --> 00:00:04.000\n"
            "Actual text\n"
        )
        path = self._write_file(temp_dir, "test.vtt", vtt_content)
        result = parse_subtitle_file(path)
        assert "NOTE" not in result
        assert "Actual text" in result

    def test_vtt_with_style_blocks(self, temp_dir):
        """Skips STYLE blocks in VTT."""
        vtt_content = (
            "WEBVTT\n"
            "\n"
            "STYLE\n"
            "::cue { color: white }\n"
            "\n"
            "00:00:01.000 --> 00:00:04.000\n"
            "Styled text\n"
        )
        path = self._write_file(temp_dir, "test.vtt", vtt_content)
        result = parse_subtitle_file(path)
        assert "STYLE" not in result
        assert "Styled text" in result

    def test_multiline_subtitle_cue(self, temp_dir):
        """Handles multi-line subtitle cues."""
        vtt_content = (
            "WEBVTT\n"
            "\n"
            "00:00:01.000 --> 00:00:04.000\n"
            "First line of subtitle\n"
            "Second line of subtitle\n"
            "\n"
            "00:00:05.000 --> 00:00:08.000\n"
            "Next cue\n"
        )
        path = self._write_file(temp_dir, "test.vtt", vtt_content)
        result = parse_subtitle_file(path)
        assert "First line of subtitle" in result
        assert "Second line of subtitle" in result
        assert "Next cue" in result

    def test_sequence_numbers_removed_srt(self, temp_dir):
        """SRT sequence numbers are removed."""
        srt_content = (
            "1\n"
            "00:00:01,000 --> 00:00:04,000\n"
            "First cue\n"
            "\n"
            "2\n"
            "00:00:05,000 --> 00:00:08,000\n"
            "Second cue\n"
        )
        path = self._write_file(temp_dir, "test.srt", srt_content)
        result = parse_subtitle_file(path)
        lines = result.strip().split('\n')
        # Sequence numbers (pure digits) should not appear
        assert all(not line.strip().isdigit() for line in lines)


# --- download_subtitles tests ---


class TestDownloadSubtitles:
    """Tests for download_subtitles() with mocked yt-dlp."""

    @pytest.fixture
    def temp_dir(self):
        d = tempfile.mkdtemp()
        yield d
        shutil.rmtree(d, ignore_errors=True)

    @patch('bot.downloader.yt_dlp.YoutubeDL')
    def test_download_manual_subtitles(self, mock_ytdl_class, temp_dir):
        """Downloads manual subtitles and returns file path."""
        mock_ydl = MagicMock()
        mock_ytdl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ytdl_class.return_value.__exit__ = MagicMock(return_value=False)

        # Simulate yt-dlp creating a subtitle file
        from datetime import datetime
        date_str = datetime.now().strftime("%Y-%m-%d")
        sub_file = os.path.join(temp_dir, f"{date_str} Test Video.en.vtt")

        def fake_download(urls):
            with open(sub_file, 'w') as f:
                f.write("WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nHello\n")

        mock_ydl.download.side_effect = fake_download

        result = download_subtitles(
            "https://youtube.com/watch?v=test",
            "en",
            temp_dir,
            auto=False,
            title="Test Video"
        )
        assert result == sub_file
        assert os.path.exists(result)

    @patch('bot.downloader.yt_dlp.YoutubeDL')
    def test_download_auto_subtitles(self, mock_ytdl_class, temp_dir):
        """Downloads auto-generated subtitles."""
        mock_ydl = MagicMock()
        mock_ytdl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ytdl_class.return_value.__exit__ = MagicMock(return_value=False)

        from datetime import datetime
        date_str = datetime.now().strftime("%Y-%m-%d")
        sub_file = os.path.join(temp_dir, f"{date_str} Test Video.pl.vtt")

        def fake_download(urls):
            with open(sub_file, 'w') as f:
                f.write("WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nCześć\n")

        mock_ydl.download.side_effect = fake_download

        result = download_subtitles(
            "https://youtube.com/watch?v=test",
            "pl",
            temp_dir,
            auto=True,
            title="Test Video"
        )
        assert result == sub_file

        # Verify auto-sub options were set
        call_args = mock_ytdl_class.call_args[0][0]
        assert call_args['writeautomaticsub'] is True
        assert call_args['writesubtitles'] is False

    @patch('bot.downloader.yt_dlp.YoutubeDL')
    def test_download_subtitles_not_found(self, mock_ytdl_class, temp_dir):
        """Returns None when subtitle file not created."""
        mock_ydl = MagicMock()
        mock_ytdl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ytdl_class.return_value.__exit__ = MagicMock(return_value=False)
        mock_ydl.download.return_value = None  # No file created

        result = download_subtitles(
            "https://youtube.com/watch?v=test",
            "en",
            temp_dir,
            title="Test Video"
        )
        assert result is None

    @patch('bot.downloader.yt_dlp.YoutubeDL')
    def test_download_subtitles_exception(self, mock_ytdl_class, temp_dir):
        """Returns None on yt-dlp exception."""
        mock_ytdl_class.side_effect = Exception("Network error")

        result = download_subtitles(
            "https://youtube.com/watch?v=test",
            "en",
            temp_dir,
            title="Test Video"
        )
        assert result is None

    @patch('bot.downloader.yt_dlp.YoutubeDL')
    def test_download_subtitles_skip_download(self, mock_ytdl_class, temp_dir):
        """Verifies skip_download=True is set in yt-dlp options."""
        mock_ydl = MagicMock()
        mock_ytdl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ytdl_class.return_value.__exit__ = MagicMock(return_value=False)
        mock_ydl.download.return_value = None

        download_subtitles(
            "https://youtube.com/watch?v=test",
            "en",
            temp_dir,
            title="Test"
        )

        call_args = mock_ytdl_class.call_args[0][0]
        assert call_args['skip_download'] is True

    @patch('bot.downloader.yt_dlp.YoutubeDL')
    def test_download_subtitles_srt_format(self, mock_ytdl_class, temp_dir):
        """Finds SRT subtitle files too."""
        mock_ydl = MagicMock()
        mock_ytdl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ytdl_class.return_value.__exit__ = MagicMock(return_value=False)

        from datetime import datetime
        date_str = datetime.now().strftime("%Y-%m-%d")
        sub_file = os.path.join(temp_dir, f"{date_str} Test.en.srt")

        def fake_download(urls):
            with open(sub_file, 'w') as f:
                f.write("1\n00:00:01,000 --> 00:00:04,000\nHello\n")

        mock_ydl.download.side_effect = fake_download

        result = download_subtitles(
            "https://youtube.com/watch?v=test",
            "en",
            temp_dir,
            title="Test"
        )
        assert result == sub_file


# --- _parse_subtitle_callback tests ---


class TestParseSubtitleCallback:
    """Tests for _parse_subtitle_callback() — callback data parsing."""

    def test_manual_lang_no_summary(self):
        assert _parse_subtitle_callback("sub_lang_en") == ("en", False, False)

    def test_manual_lang_with_summary(self):
        assert _parse_subtitle_callback("sub_lang_en_sum") == ("en", False, True)

    def test_auto_lang_no_summary(self):
        assert _parse_subtitle_callback("sub_auto_pl") == ("pl", True, False)

    def test_auto_lang_with_summary(self):
        assert _parse_subtitle_callback("sub_auto_pl_sum") == ("pl", True, True)

    def test_lang_ending_in_s_no_summary(self):
        """Spanish (es) must work without summary — was broken by '_s' suffix."""
        assert _parse_subtitle_callback("sub_lang_es") == ("es", False, False)

    def test_lang_ending_in_s_with_summary(self):
        """Spanish (es) must work with summary."""
        assert _parse_subtitle_callback("sub_lang_es_sum") == ("es", False, True)

    def test_auto_lang_ending_in_s_no_summary(self):
        """Auto Malay (ms) without summary."""
        assert _parse_subtitle_callback("sub_auto_ms") == ("ms", True, False)

    def test_auto_lang_ending_in_s_with_summary(self):
        """Auto Bosnian (bs) with summary."""
        assert _parse_subtitle_callback("sub_auto_bs_sum") == ("bs", True, True)

    def test_invalid_prefix(self):
        assert _parse_subtitle_callback("sub_unknown_en") is None

    def test_empty_lang(self):
        assert _parse_subtitle_callback("sub_lang_") is None

    def test_empty_lang_with_summary(self):
        assert _parse_subtitle_callback("sub_lang__sum") is None
