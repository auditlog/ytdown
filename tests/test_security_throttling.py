"""Tests for stable rate-limiting helpers."""

from collections import defaultdict

from bot.security_throttling import check_rate_limit


def test_check_rate_limit_blocks_after_threshold_with_explicit_time():
    requests_map = defaultdict(list)
    user_id = 77
    now = 1_000.0

    assert check_rate_limit(user_id, requests_map, current_time=now, max_requests=2, window_seconds=60) is True
    assert check_rate_limit(user_id, requests_map, current_time=now + 1, max_requests=2, window_seconds=60) is True
    assert check_rate_limit(user_id, requests_map, current_time=now + 2, max_requests=2, window_seconds=60) is False


def test_check_rate_limit_discards_expired_requests():
    requests_map = defaultdict(list, {88: [10.0, 20.0]})
    assert check_rate_limit(88, requests_map, current_time=100.0, max_requests=2, window_seconds=30) is True
    assert requests_map[88] == [100.0]
