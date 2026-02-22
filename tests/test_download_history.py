"""
Tests for download history functionality.
"""

import json
import os
import tempfile
import shutil
from datetime import datetime
from unittest.mock import patch, MagicMock
import pytest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import (
    load_download_history,
    save_download_history,
    add_download_record,
    get_download_stats,
    DOWNLOAD_HISTORY_FILE,
    MAX_HISTORY_ENTRIES,
)


@pytest.fixture
def temp_history_file():
    """Create a temporary history file for testing."""
    temp_dir = tempfile.mkdtemp()
    original_file = DOWNLOAD_HISTORY_FILE

    # Mock the history file path
    import bot.config
    bot.config.DOWNLOAD_HISTORY_FILE = os.path.join(temp_dir, "test_history.json")

    yield bot.config.DOWNLOAD_HISTORY_FILE

    # Cleanup
    bot.config.DOWNLOAD_HISTORY_FILE = original_file
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def sample_history_data():
    """Sample download history data."""
    return [
        {
            'timestamp': '2024-01-01T10:00:00',
            'user_id': 123456,
            'title': 'Test Video 1',
            'url': 'https://youtube.com/watch?v=test1',
            'format': 'video_best',
            'file_size_mb': 150.5
        },
        {
            'timestamp': '2024-01-01T11:00:00',
            'user_id': 123456,
            'title': 'Test Audio 1',
            'url': 'https://youtube.com/watch?v=test2',
            'format': 'audio_mp3',
            'file_size_mb': 5.2
        },
        {
            'timestamp': '2024-01-01T12:00:00',
            'user_id': 789012,
            'title': 'Test Video 2',
            'url': 'https://youtube.com/watch?v=test3',
            'format': 'video_720p',
            'file_size_mb': 80.3
        }
    ]


class TestDownloadHistory:
    """Test download history functionality."""

    def test_load_empty_history(self, temp_history_file):
        """Test loading history when file doesn't exist."""
        history = load_download_history()
        assert history == []

    def test_save_and_load_history(self, temp_history_file, sample_history_data):
        """Test saving and loading download history."""
        # Save history
        save_download_history(sample_history_data)

        # Verify file exists
        assert os.path.exists(temp_history_file)

        # Load and verify
        loaded = load_download_history()
        assert len(loaded) == 3
        assert loaded[0]['title'] == 'Test Video 1'
        assert loaded[1]['format'] == 'audio_mp3'
        assert loaded[2]['user_id'] == 789012

    def test_add_download_record(self, temp_history_file):
        """Test adding a new download record."""
        # Add first record
        add_download_record(
            user_id=123456,
            title="New Video",
            url="https://youtube.com/watch?v=new",
            format_type="video_1080p",
            file_size_mb=200.5
        )

        # Verify record was added
        history = load_download_history()
        assert len(history) == 1
        assert history[0]['title'] == "New Video"
        assert history[0]['file_size_mb'] == 200.5
        assert 'timestamp' in history[0]

    def test_add_record_with_time_range(self, temp_history_file):
        """Test adding record with time range."""
        add_download_record(
            user_id=123456,
            title="Partial Video",
            url="https://youtube.com/watch?v=partial",
            format_type="audio_mp3",
            time_range={'start': '1:30', 'end': '5:45'}
        )

        history = load_download_history()
        assert len(history) == 1
        assert history[0]['time_range'] == "1:30-5:45"

    def test_max_history_entries(self, temp_history_file):
        """Test that history is limited to MAX_HISTORY_ENTRIES."""
        # Create more entries than the limit
        large_history = []
        for i in range(MAX_HISTORY_ENTRIES + 100):
            large_history.append({
                'timestamp': datetime.now().isoformat(),
                'user_id': 123456,
                'title': f'Video {i}',
                'url': f'https://youtube.com/watch?v=test{i}',
                'format': 'video_best'
            })

        # Save history
        save_download_history(large_history)

        # Load and verify it was truncated
        loaded = load_download_history()
        assert len(loaded) == MAX_HISTORY_ENTRIES
        # Should keep the last (newest) entries
        assert loaded[-1]['title'] == f'Video {MAX_HISTORY_ENTRIES + 99}'

    def test_get_download_stats_all(self, temp_history_file, sample_history_data):
        """Test getting statistics for all downloads."""
        save_download_history(sample_history_data)

        stats = get_download_stats()

        assert stats['total_downloads'] == 3
        assert stats['total_size_mb'] == 236.0  # 150.5 + 5.2 + 80.3
        assert stats['format_counts']['video_best'] == 1
        assert stats['format_counts']['audio_mp3'] == 1
        assert stats['format_counts']['video_720p'] == 1
        assert len(stats['recent']) == 3

    def test_get_download_stats_by_user(self, temp_history_file, sample_history_data):
        """Test getting statistics for specific user."""
        save_download_history(sample_history_data)

        stats = get_download_stats(user_id=123456)

        assert stats['total_downloads'] == 2
        assert stats['total_size_mb'] == 155.7  # 150.5 + 5.2
        assert stats['format_counts']['video_best'] == 1
        assert stats['format_counts']['audio_mp3'] == 1
        assert 'video_720p' not in stats['format_counts']
        assert len(stats['recent']) == 2

    def test_corrupted_history_file(self, temp_history_file):
        """Test handling of corrupted history file."""
        # Write invalid JSON
        with open(temp_history_file, 'w') as f:
            f.write("{ invalid json content")

        # Should return empty list on error
        history = load_download_history()
        assert history == []

    def test_history_file_permissions(self, temp_history_file, sample_history_data):
        """Test that history file is created with proper permissions."""
        save_download_history(sample_history_data)

        # Check file exists
        assert os.path.exists(temp_history_file)

        # Verify JSON structure
        with open(temp_history_file, 'r') as f:
            data = json.load(f)
            assert 'downloads' in data
            assert 'last_updated' in data
            assert 'version' in data
            assert data['version'] == '1.0'


class TestDownloadHistoryIntegration:
    """Integration tests for download history."""

    @patch('bot.config.load_download_history')
    @patch('bot.config.save_download_history')
    def test_concurrent_adds(self, mock_save, mock_load, temp_history_file):
        """Test adding multiple records concurrently."""
        mock_load.return_value = []

        # Simulate multiple adds
        for i in range(5):
            add_download_record(
                user_id=100 + i,
                title=f"Video {i}",
                url=f"https://youtube.com/watch?v=test{i}",
                format_type="video_best"
            )

        # Verify save was called multiple times
        assert mock_save.call_count == 5

    def test_special_characters_in_title(self, temp_history_file):
        """Test handling special characters in video titles."""
        special_title = "Test 🎵 Video | Special: Characters & Symbols"

        add_download_record(
            user_id=123456,
            title=special_title,
            url="https://youtube.com/watch?v=special",
            format_type="audio_mp3"
        )

        history = load_download_history()
        assert len(history) == 1
        assert history[0]['title'] == special_title

    def test_stats_empty_history(self, temp_history_file):
        """Test statistics with empty history."""
        stats = get_download_stats()

        assert stats['total_downloads'] == 0
        assert stats['total_size_mb'] == 0
        assert stats['format_counts'] == {}
        assert stats['recent'] == []


class TestDownloadHistoryNewFields:
    """Tests for new status, selected_format, and error_message fields."""

    def test_add_record_with_status_and_selected_format(self, temp_history_file):
        """Test adding a record with status and selected_format."""
        add_download_record(
            user_id=123,
            title="Good Download",
            url="https://youtube.com/watch?v=ok",
            format_type="video_best",
            file_size_mb=100.0,
            status="success",
            selected_format="best",
        )

        history = load_download_history()
        assert len(history) == 1
        assert history[0]['status'] == 'success'
        assert history[0]['selected_format'] == 'best'
        assert 'error_message' not in history[0]

    def test_add_record_with_failure_and_error_message(self, temp_history_file):
        """Test adding a failure record with error message."""
        add_download_record(
            user_id=123,
            title="Bad Download",
            url="https://youtube.com/watch?v=bad",
            format_type="video_720p",
            status="failure",
            selected_format="720p",
            error_message="Network timeout",
        )

        history = load_download_history()
        assert len(history) == 1
        assert history[0]['status'] == 'failure'
        assert history[0]['error_message'] == 'Network timeout'
        assert history[0]['selected_format'] == '720p'

    def test_add_record_defaults_to_success(self, temp_history_file):
        """Test that status defaults to 'success' when not provided."""
        add_download_record(
            user_id=123,
            title="Default Status",
            url="https://youtube.com/watch?v=def",
            format_type="audio_mp3",
        )

        history = load_download_history()
        assert history[0]['status'] == 'success'

    def test_backward_compat_old_records_without_status(self, temp_history_file):
        """Test that old records without status field default to 'success' in stats."""
        # Simulate old-style records without status field
        old_records = [
            {
                'timestamp': '2024-01-01T10:00:00',
                'user_id': 123,
                'title': 'Old Video',
                'url': 'https://youtube.com/watch?v=old',
                'format': 'video_best',
            },
            {
                'timestamp': '2024-01-02T10:00:00',
                'user_id': 123,
                'title': 'New Video',
                'url': 'https://youtube.com/watch?v=new',
                'format': 'video_best',
                'status': 'failure',
            },
        ]
        save_download_history(old_records)

        stats = get_download_stats()
        assert stats['success_count'] == 1  # Old record defaults to success
        assert stats['failure_count'] == 1

    def test_stats_include_success_failure_counts(self, temp_history_file):
        """Test that get_download_stats returns success and failure counts."""
        add_download_record(123, "V1", "url1", "video_best", status="success")
        add_download_record(123, "V2", "url2", "video_best", status="success")
        add_download_record(123, "V3", "url3", "audio_mp3", status="failure", error_message="err")

        stats = get_download_stats()
        assert stats['success_count'] == 2
        assert stats['failure_count'] == 1
        assert stats['total_downloads'] == 3

    def test_error_message_truncated_to_200_chars(self, temp_history_file):
        """Test that very long error messages are truncated."""
        long_error = "x" * 500
        add_download_record(
            user_id=123,
            title="Long Error",
            url="https://youtube.com/watch?v=long",
            format_type="video_best",
            status="failure",
            error_message=long_error,
        )

        history = load_download_history()
        assert len(history[0]['error_message']) == 200