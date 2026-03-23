"""Tests for stable PIN-throttling helpers."""

from collections import defaultdict

from bot.security_pin import (
    clear_failed_attempts,
    get_block_remaining_seconds,
    is_user_blocked,
    register_pin_failure,
)


def test_register_pin_failure_increments_and_blocks():
    attempts = defaultdict(int)
    block_map = defaultdict(float)

    remaining, actual = register_pin_failure(
        123,
        now=100.0,
        attempts=attempts,
        block_map=block_map,
        max_attempts=2,
        block_time=30,
    )
    assert (remaining, actual) == (1, 1)
    assert is_user_blocked(123, now=100.0, block_map=block_map) is False

    remaining, actual = register_pin_failure(
        123,
        now=110.0,
        attempts=attempts,
        block_map=block_map,
        max_attempts=2,
        block_time=30,
    )
    assert (remaining, actual) == (0, 2)
    assert is_user_blocked(123, now=115.0, block_map=block_map) is True
    assert get_block_remaining_seconds(123, now=115.0, block_map=block_map) == 25


def test_clear_failed_attempts_resets_attempt_counter():
    attempts = defaultdict(int, {5: 3})
    clear_failed_attempts(5, attempts=attempts)
    assert attempts[5] == 0
