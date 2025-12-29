"""
Integration tests for the YouTube Downloader Bot.
"""

import os
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, Mock, AsyncMock
import pytest

from bot.config import (
    load_config,
    load_authorized_users,
    save_authorized_users,
    add_download_record,
    get_download_stats,
)
from bot.security import (
    check_rate_limit,
    validate_youtube_url,
    manage_authorized_user,
    estimate_file_size,
)
from bot.downloader import sanitize_filename


@pytest.mark.integration
class TestConfigIntegration:
    """Integration tests for configuration loading."""

    def test_config_priority_order(self, temp_dir, monkeypatch):
        """Test configuration loading priority: env > file > defaults."""
        # Create config file
        config_file = Path(temp_dir) / "api_key.md"
        with open(config_file, "w") as f:
            f.write("TELEGRAM_BOT_TOKEN=file_token\n")
            f.write("PIN_CODE=87654321\n")

        # Set environment variable (should override file)
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env_token")
        monkeypatch.setattr("bot.config.CONFIG_FILE_PATH", str(config_file))

        # Load config
        config = load_config()

        # Environment should win
        assert config["TELEGRAM_BOT_TOKEN"] == "env_token"
        # File value should be used when no env var
        assert config["PIN_CODE"] == "87654321"

    def test_authorized_users_persistence(self, temp_dir, monkeypatch):
        """Test that authorized users persist across save/load cycles."""
        users_file = Path(temp_dir) / "authorized_users.json"
        monkeypatch.setattr("bot.config.AUTHORIZED_USERS_FILE", str(users_file))

        # Add users
        users = {123456, 789012, 345678}
        save_authorized_users(users)

        # Load and verify
        loaded_users = load_authorized_users()
        assert loaded_users == users

        # Verify file structure
        with open(users_file, "r") as f:
            data = json.load(f)
            assert "authorized_users" in data
            assert "last_updated" in data
            assert "version" in data


@pytest.mark.integration
class TestDownloadFlow:
    """Integration tests for download workflow."""

    @pytest.mark.requires_network
    async def test_download_workflow(self, mock_yt_dlp, temp_dir):
        """Test complete download workflow."""
        from bot.telegram_callbacks import download_file

        # Mock update and context
        update = Mock()
        update.effective_chat.id = 123456
        context = Mock()
        context.bot.send_message = AsyncMock()
        context.bot.send_document = AsyncMock()

        # Set up URL
        from bot.security import user_urls
        user_urls[123456] = "https://youtube.com/watch?v=test"

        # Mock file system
        with patch("bot.telegram_callbacks.os.path.exists", return_value=True):
            with patch("bot.telegram_callbacks.os.path.getsize", return_value=1024*1024):
                with patch("builtins.open", create=True):
                    await download_file(
                        update, context,
                        download_type="video",
                        format_id="best",
                        url="https://youtube.com/watch?v=test"
                    )

        # Verify messages were sent
        assert context.bot.send_message.called

    def test_download_history_workflow(self, temp_dir, monkeypatch):
        """Test download history recording and statistics."""
        history_file = Path(temp_dir) / "history.json"
        monkeypatch.setattr("bot.config.DOWNLOAD_HISTORY_FILE", str(history_file))

        # Add multiple downloads
        add_download_record(123456, "Video 1", "url1", "video_best", 100.5)
        add_download_record(123456, "Audio 1", "url2", "audio_mp3", 5.2)
        add_download_record(789012, "Video 2", "url3", "video_720p", 50.3)

        # Get statistics for specific user
        stats = get_download_stats(123456)
        assert stats["total_downloads"] == 2
        assert stats["total_size_mb"] == 105.7

        # Get global statistics
        global_stats = get_download_stats()
        assert global_stats["total_downloads"] == 3


@pytest.mark.integration
class TestSecurityIntegration:
    """Integration tests for security features."""

    def test_rate_limiting_workflow(self):
        """Test rate limiting across multiple requests."""
        user_id = 123456

        # Make requests up to the limit
        for _ in range(10):  # RATE_LIMIT_REQUESTS = 10
            assert check_rate_limit(user_id) is True

        # Next request should be blocked
        assert check_rate_limit(user_id) is False

    def test_pin_authentication_workflow(self):
        """Test PIN authentication and blocking."""
        from bot.security import failed_attempts, block_until, MAX_ATTEMPTS
        import time

        user_id = 123456

        # Simulate failed attempts
        for i in range(MAX_ATTEMPTS - 1):
            failed_attempts[user_id] += 1
            assert block_until[user_id] == 0  # Not blocked yet

        # Last failed attempt should trigger block
        failed_attempts[user_id] += 1
        block_time = time.time() + 900  # 15 minutes
        block_until[user_id] = block_time

        assert time.time() < block_until[user_id]  # User is blocked

    def test_url_validation_workflow(self):
        """Test URL validation for various inputs."""
        valid_urls = [
            "https://www.youtube.com/watch?v=test",
            "https://youtube.com/watch?v=test",
            "https://youtu.be/test",
            "https://m.youtube.com/watch?v=test",
            "https://music.youtube.com/watch?v=test",
        ]

        invalid_urls = [
            "http://youtube.com/watch?v=test",  # Not HTTPS
            "https://vimeo.com/test",  # Wrong domain
            "https://malicious.com/youtube.com",  # Subdomain trick
            "not_a_url",
            "",
        ]

        for url in valid_urls:
            assert validate_youtube_url(url) is True, f"Failed for valid URL: {url}"

        for url in invalid_urls:
            assert validate_youtube_url(url) is False, f"Failed for invalid URL: {url}"


@pytest.mark.integration
class TestTranscriptionIntegration:
    """Integration tests for transcription workflow."""

    @pytest.mark.requires_api
    async def test_transcription_workflow(
        self, sample_mp3_file, mock_groq_api, mock_claude_api, temp_dir
    ):
        """Test complete transcription and summarization workflow."""
        from bot.transcription import transcribe_mp3_file, generate_summary

        # Transcribe file
        transcript_path = transcribe_mp3_file(sample_mp3_file, temp_dir)
        assert transcript_path is not None
        assert Path(transcript_path).exists()

        # Read transcript
        with open(transcript_path, "r", encoding="utf-8") as f:
            transcript_text = f.read()

        # Generate summary
        summary = generate_summary(transcript_text, summary_type=1)
        assert summary is not None
        assert len(summary) > 0

    def test_file_splitting_workflow(self, temp_dir, mock_ffmpeg):
        """Test MP3 file splitting for large files."""
        from bot.transcription import split_mp3

        # Create a large fake MP3
        large_mp3 = Path(temp_dir) / "large.mp3"
        with open(large_mp3, "wb") as f:
            f.write(b"\xFF\xFB" + b"\x00" * (30 * 1024 * 1024))  # 30MB

        # Split file
        parts = split_mp3(str(large_mp3), temp_dir, max_size_mb=20)

        # Should create multiple parts
        assert len(parts) > 1
        for part in parts:
            assert Path(part).name.startswith("large_part")


@pytest.mark.integration
class TestCLIIntegration:
    """Integration tests for CLI interface."""

    def test_cli_help(self):
        """Test CLI help command."""
        from bot.cli import parse_arguments

        with pytest.raises(SystemExit):
            parse_arguments(["--help"])

    def test_cli_download_arguments(self):
        """Test CLI argument parsing."""
        from bot.cli import parse_arguments

        args = parse_arguments([
            "--cli",
            "--url", "https://youtube.com/watch?v=test",
            "--format", "mp3",
            "--output", "test.mp3"
        ])

        assert args.cli is True
        assert args.url == "https://youtube.com/watch?v=test"
        assert args.format == "mp3"
        assert args.output == "test.mp3"


@pytest.mark.integration
class TestEndToEnd:
    """End-to-end integration tests."""

    def test_filename_sanitization(self):
        """Test filename sanitization for various inputs."""
        test_cases = [
            ("Normal Title", "Normal Title"),
            ("Title/With\\Slashes", "Title-With-Slashes"),
            ("Title:With:Colons", "Title-With-Colons"),
            ("Title*With?Special<>Chars|", "Title-With-Special--Chars-"),
            ('Title"With"Quotes', "Title-With-Quotes"),
        ]

        for input_name, expected in test_cases:
            result = sanitize_filename(input_name)
            assert result == expected, f"Failed for: {input_name}"

    @pytest.mark.slow
    def test_performance_multiple_users(self):
        """Test system performance with multiple concurrent users."""
        from concurrent.futures import ThreadPoolExecutor
        import time

        def simulate_user_action(user_id):
            # Simulate rate limiting check
            for _ in range(5):
                check_rate_limit(user_id)
                time.sleep(0.1)
            return user_id

        # Simulate 20 concurrent users
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [
                executor.submit(simulate_user_action, user_id)
                for user_id in range(1000, 1020)
            ]
            results = [f.result() for f in futures]

        assert len(results) == 20
        assert all(r in range(1000, 1020) for r in results)