"""
Tests for time range parsing functionality.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.telegram_commands import parse_time_range


class TestParseTimeRange:
    """Test time range parsing."""

    def test_valid_mm_ss_format(self):
        """Test MM:SS-MM:SS format."""
        result = parse_time_range("0:30-5:45")
        assert result is not None
        assert result['start'] == "0:30"
        assert result['end'] == "5:45"
        assert result['start_sec'] == 30
        assert result['end_sec'] == 345

    def test_valid_hh_mm_ss_format(self):
        """Test HH:MM:SS-HH:MM:SS format."""
        result = parse_time_range("1:00:00-1:30:00")
        assert result is not None
        assert result['start'] == "1:00:00"
        assert result['end'] == "1:30:00"
        assert result['start_sec'] == 3600
        assert result['end_sec'] == 5400

    def test_valid_mixed_format(self):
        """Test mixed format (SS-MM:SS)."""
        result = parse_time_range("30-5:45")
        assert result is not None
        assert result['start_sec'] == 30
        assert result['end_sec'] == 345

    def test_with_spaces(self):
        """Test format with spaces around dash."""
        result = parse_time_range("0:30 - 5:45")
        assert result is not None
        assert result['start_sec'] == 30
        assert result['end_sec'] == 345

    def test_invalid_start_greater_than_end(self):
        """Test that start >= end returns None."""
        assert parse_time_range("5:00-2:00") is None
        assert parse_time_range("5:00-5:00") is None

    def test_invalid_format(self):
        """Test invalid formats return None."""
        assert parse_time_range("invalid") is None
        assert parse_time_range("https://youtube.com") is None
        assert parse_time_range("12345678") is None
        assert parse_time_range("") is None

    def test_edge_cases(self):
        """Test edge cases."""
        # Very short range
        result = parse_time_range("0:00-0:01")
        assert result is not None
        assert result['start_sec'] == 0
        assert result['end_sec'] == 1
        
        # Long video
        result = parse_time_range("0:00-99:59")
        assert result is not None