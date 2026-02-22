"""Unit tests for security helpers."""

from collections import defaultdict
import time

from bot import security


def test_manage_authorized_user_add_remove(monkeypatch):
    monkeypatch.setattr(security, "save_authorized_users", lambda users: None)

    original = set(security.authorized_users)
    try:
        security.authorized_users = set()

        assert security.manage_authorized_user(1001, "add") is True
        assert 1001 in security.authorized_users

        # Adding the same user twice should remain idempotent
        assert security.manage_authorized_user(1001, "add") is True
        assert list(security.authorized_users).count(1001) == 1

        assert security.manage_authorized_user(1001, "remove") is True
        assert 1001 not in security.authorized_users

        assert security.manage_authorized_user(1001, "remove") is True
        assert 1001 not in security.authorized_users
    finally:
        security.authorized_users = original


def test_manage_authorized_user_unknown_action(monkeypatch):
    monkeypatch.setattr(security, "save_authorized_users", lambda users: None)
    original = set(security.authorized_users)
    try:
        security.authorized_users = set()
        assert security.manage_authorized_user(1002, "invalid") is False
    finally:
        security.authorized_users = original


def test_check_rate_limit_resets_old_requests():
    user_id = 555
    security.user_requests.clear()
    security.user_requests[user_id] = [time.time() - 120, time.time() - 90]

    assert security.check_rate_limit(user_id) is True
    assert len(security.user_requests[user_id]) == 1


def test_check_rate_limit_blocks_after_threshold():
    security.user_requests.clear()
    user_id = 777

    # Allow exactly RATE_LIMIT_REQUESTS calls
    for _ in range(security.RATE_LIMIT_REQUESTS):
        assert security.check_rate_limit(user_id) is True

    # Next one must be blocked
    assert security.check_rate_limit(user_id) is False


def test_validate_youtube_url_edge_cases():
    assert security.validate_youtube_url("https://www.youtube.com/watch?v=abc") is True
    assert security.validate_youtube_url("https://youtu.be/abc") is True
    assert security.validate_youtube_url("http://www.youtube.com/watch?v=abc") is False
    assert security.validate_youtube_url("") is False
    assert security.validate_youtube_url(None) is False


def test_estimate_file_size_various_inputs():
    info_with_size = {"formats": [{"filesize": 50 * 1024 * 1024}]}
    assert security.estimate_file_size(info_with_size) == 50.0

    info_with_duration = {"duration": 120, "formats": [{}]}
    assert security.estimate_file_size(info_with_duration) == 75.0

    assert security.estimate_file_size({}) is None


def test_check_rate_limit_uses_explicit_time():
    security.user_requests.clear()
    user_id = 123
    now = 1_000.0

    security.user_requests[user_id] = [now - 30, now - 10]
    assert security.check_rate_limit(user_id, current_time=now) is True
    assert len(security.user_requests[user_id]) == 3


def test_security_state_snapshot_does_not_expose_mutable_references():
    security.failed_attempts.clear()
    security.block_until.clear()
    security.user_requests.clear()

    security.failed_attempts[42] = 2
    security.block_until[42] = 123.0
    security.user_requests[42] = [10.0, 20.0]

    snapshot = security.get_security_state()
    snapshot.failed_attempts[42] = 0
    snapshot.user_requests[42].append(30.0)

    assert security.failed_attempts[42] == 2
    assert security.user_requests[42] == [10.0, 20.0]


def test_set_security_state_keeps_public_dict_references():
    original_failed_attempts = security.failed_attempts
    original_block_until = security.block_until
    original_user_requests = security.user_requests

    next_state = security.SecurityState(
        failed_attempts=defaultdict(int, {11: 5}),
        block_until=defaultdict(float, {11: 42.0}),
        user_requests=defaultdict(list, {11: [99.0]}),
    )

    security.set_security_state(next_state)

    assert security.failed_attempts is original_failed_attempts
    assert security.block_until is original_block_until
    assert security.user_requests is original_user_requests
    assert security.failed_attempts[11] == 5
    assert security.block_until[11] == 42.0
    assert security.user_requests[11] == [99.0]
    assert 42 not in security.failed_attempts


def test_pin_failure_blocks_after_threshold():
    attempts = defaultdict(int)
    block_map = defaultdict(float)

    assert security.register_pin_failure(
        user_id=1,
        now=100.0,
        attempts=attempts,
        block_map=block_map,
        max_attempts=2,
        block_time=20,
    ) == 1
    assert security.is_user_blocked(1, now=105.0, block_map=block_map) is False

    assert security.register_pin_failure(
        user_id=1,
        now=106.0,
        attempts=attempts,
        block_map=block_map,
        max_attempts=2,
        block_time=20,
    ) == 0
    assert security.is_user_blocked(1, now=110.0, block_map=block_map) is True
    assert security.get_block_remaining_seconds(1, now=110.0, block_map=block_map) == 16

    security.clear_failed_attempts(1, attempts=attempts)
    assert attempts[1] == 0
