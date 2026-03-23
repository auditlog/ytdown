"""Tests for time range parsing — covers parse_time_range and its internal helpers."""

from bot.handlers.time_range import parse_time_range


class TestParseTimeRange:
    """Test time range parsing."""

    def test_valid_mm_ss_format(self):
        result = parse_time_range("0:30-5:45")
        assert result is not None
        assert result['start'] == "0:30"
        assert result['end'] == "5:45"
        assert result['start_sec'] == 30
        assert result['end_sec'] == 345

    def test_valid_hh_mm_ss_format(self):
        result = parse_time_range("1:00:00-1:30:00")
        assert result is not None
        assert result['start'] == "1:00:00"
        assert result['end'] == "1:30:00"
        assert result['start_sec'] == 3600
        assert result['end_sec'] == 5400

    def test_valid_mixed_format(self):
        result = parse_time_range("30-5:45")
        assert result is not None
        assert result['start_sec'] == 30
        assert result['end_sec'] == 345

    def test_with_spaces(self):
        result = parse_time_range("0:30 - 5:45")
        assert result is not None
        assert result['start_sec'] == 30
        assert result['end_sec'] == 345

    def test_invalid_start_greater_than_end(self):
        assert parse_time_range("5:00-2:00") is None
        assert parse_time_range("5:00-5:00") is None

    def test_invalid_format(self):
        assert parse_time_range("invalid") is None
        assert parse_time_range("https://youtube.com") is None
        assert parse_time_range("12345678") is None
        assert parse_time_range("") is None

    def test_edge_cases(self):
        result = parse_time_range("0:00-0:01")
        assert result is not None
        assert result['start_sec'] == 0
        assert result['end_sec'] == 1

        result = parse_time_range("0:00-99:59")
        assert result is not None


class TestTimeToSeconds:
    """Test internal time_to_seconds via parse_time_range outputs."""

    def test_seconds_only_input(self):
        result = parse_time_range("5-30")
        assert result['start_sec'] == 5
        assert result['end_sec'] == 30

    def test_minutes_seconds_input(self):
        result = parse_time_range("2:30-10:15")
        assert result['start_sec'] == 150
        assert result['end_sec'] == 615

    def test_hours_minutes_seconds_input(self):
        result = parse_time_range("1:30:00-2:00:00")
        assert result['start_sec'] == 5400
        assert result['end_sec'] == 7200

    def test_zero_start(self):
        result = parse_time_range("0:00-1:00")
        assert result['start_sec'] == 0
        assert result['end_sec'] == 60

    def test_large_hours_value(self):
        result = parse_time_range("9:59:59-10:00:00")
        assert result['start_sec'] == 35999
        assert result['end_sec'] == 36000


class TestFormatTime:
    """Test internal format_time via parse_time_range formatted start/end."""

    def test_under_one_minute_formatted_as_mm_ss(self):
        result = parse_time_range("0:00-0:45")
        assert result['end'] == "0:45"

    def test_exact_one_minute(self):
        result = parse_time_range("0:00-1:00")
        assert result['end'] == "1:00"

    def test_under_one_hour_formatted_as_mm_ss(self):
        result = parse_time_range("0:00-59:59")
        assert result['end'] == "59:59"

    def test_one_hour_formatted_as_hh_mm_ss(self):
        result = parse_time_range("0:00-1:00:00")
        assert result['end'] == "1:00:00"

    def test_over_one_hour_formatted_as_hh_mm_ss(self):
        result = parse_time_range("0:00-1:30:45")
        assert result['end'] == "1:30:45"

    def test_roundtrip_seconds_to_format(self):
        result = parse_time_range("0:00-2:03:07")
        assert result['end_sec'] == 7387
        assert result['end'] == "2:03:07"