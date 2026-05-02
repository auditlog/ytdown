"""Unit tests for security helpers."""

from collections import defaultdict
import time

from bot import security
from bot import security_authorization


def test_manage_authorized_user_add_remove(monkeypatch):
    authorized_users = set()
    monkeypatch.setattr(
        security_authorization,
        "add_runtime_authorized_user",
        lambda user_id: False if user_id in authorized_users else not authorized_users.add(user_id),
    )
    monkeypatch.setattr(
        security_authorization,
        "remove_runtime_authorized_user",
        lambda user_id: False if user_id not in authorized_users else not authorized_users.remove(user_id),
    )

    assert security.manage_authorized_user(1001, "add") is True
    assert 1001 in authorized_users

    # Adding the same user twice should remain idempotent
    assert security.manage_authorized_user(1001, "add") is True
    assert list(authorized_users).count(1001) == 1

    assert security.manage_authorized_user(1001, "remove") is True
    assert 1001 not in authorized_users

    assert security.manage_authorized_user(1001, "remove") is True
    assert 1001 not in authorized_users


def test_manage_authorized_user_unknown_action(monkeypatch):
    assert security.manage_authorized_user(1002, "invalid") is False


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


def test_validate_url_vimeo():
    assert security.validate_url("https://vimeo.com/123456") is True
    assert security.validate_url("https://player.vimeo.com/video/123") is True
    assert security.validate_url("http://vimeo.com/123") is False  # HTTP not allowed


def test_validate_url_tiktok():
    assert security.validate_url("https://www.tiktok.com/@user/video/123") is True
    assert security.validate_url("https://tiktok.com/@user/video/123") is True
    assert security.validate_url("https://vm.tiktok.com/abc123") is True
    assert security.validate_url("https://m.tiktok.com/@user/video/123") is True


def test_validate_url_instagram():
    assert security.validate_url("https://www.instagram.com/reel/abc123") is True
    assert security.validate_url("https://instagram.com/p/abc123") is True
    assert security.validate_url("http://instagram.com/p/abc") is False


def test_validate_url_linkedin():
    assert security.validate_url("https://www.linkedin.com/posts/user-123") is True
    assert security.validate_url("https://linkedin.com/posts/user-123") is True


def test_validate_url_rejects_unsupported():
    assert security.validate_url("https://example.com/video") is False
    assert security.validate_url("https://dailymotion.com/video/abc") is False


def test_detect_platform_youtube():
    assert security.detect_platform("https://www.youtube.com/watch?v=abc") == "youtube"
    assert security.detect_platform("https://youtu.be/abc") == "youtube"
    assert security.detect_platform("https://music.youtube.com/watch?v=abc") == "youtube"
    assert security.detect_platform("https://m.youtube.com/watch?v=abc") == "youtube"


def test_detect_platform_vimeo():
    assert security.detect_platform("https://vimeo.com/123") == "vimeo"
    assert security.detect_platform("https://player.vimeo.com/video/123") == "vimeo"


def test_detect_platform_tiktok():
    assert security.detect_platform("https://www.tiktok.com/@user/video/1") == "tiktok"
    assert security.detect_platform("https://vm.tiktok.com/abc") == "tiktok"


def test_detect_platform_instagram():
    assert security.detect_platform("https://www.instagram.com/reel/abc") == "instagram"
    assert security.detect_platform("https://instagram.com/p/abc") == "instagram"


def test_detect_platform_linkedin():
    assert security.detect_platform("https://www.linkedin.com/posts/user") == "linkedin"
    assert security.detect_platform("https://linkedin.com/posts/user") == "linkedin"


def test_validate_url_castbox():
    assert security.validate_url("https://castbox.fm/episode/Some-Episode-id123-id456") is True
    assert security.validate_url("https://www.castbox.fm/episode/id123-id456") is True


def test_detect_platform_castbox():
    assert security.detect_platform("https://castbox.fm/episode/id123-id456") == "castbox"
    assert security.detect_platform("https://www.castbox.fm/episode/id123") == "castbox"


def test_validate_url_spotify():
    assert security.validate_url("https://open.spotify.com/episode/4rOoJ6Egrf8K2IrywzwOMk") is True


def test_detect_platform_spotify():
    assert security.detect_platform("https://open.spotify.com/episode/abc123") == "spotify"


def test_detect_platform_unknown():
    assert security.detect_platform("https://example.com/video") is None
    assert security.detect_platform("") is None
    assert security.detect_platform(None) is None


def test_validate_url_is_backward_compatible_alias():
    """validate_youtube_url and validate_url are the same function."""
    assert security.validate_youtube_url is security.validate_url


def test_extract_url_from_text_pulls_link_out_of_prose():
    msg = (
        "nie mam na kompie służbowym premium - czy mógłbyś mi pobrać "
        "w wysokiej rozdzielczości ten film i wysłać na wetransfer? "
        "nie jest to pilne https://www.youtube.com/watch?v=zCq3xb2Hmqs"
    )
    assert security.extract_url_from_text(msg) == "https://www.youtube.com/watch?v=zCq3xb2Hmqs"


def test_extract_url_from_text_returns_bare_url_unchanged():
    url = "https://youtu.be/abc123"
    assert security.extract_url_from_text(url) == url


def test_extract_url_from_text_picks_first_supported_platform():
    msg = "see https://example.com/not-supported and https://vimeo.com/999"
    assert security.extract_url_from_text(msg) == "https://vimeo.com/999"


def test_extract_url_from_text_strips_trailing_punctuation():
    msg = "Check this (https://www.tiktok.com/@user/video/123)."
    assert security.extract_url_from_text(msg) == "https://www.tiktok.com/@user/video/123"


def test_extract_url_from_text_rejects_messages_without_supported_url():
    assert security.extract_url_from_text("just some text, no link") is None
    assert security.extract_url_from_text("https://example.com/only") is None
    assert security.extract_url_from_text("") is None
    assert security.extract_url_from_text(None) is None


def test_extract_url_from_text_resolves_castbox_share_redirect():
    # d.castbox.fm is the share/redirect host; validate_url rejects it, so the
    # extractor must unwrap the inner ?link= target before validating.
    share_url = (
        "https://d.castbox.fm/ch/1234?link="
        "https://castbox.fm/episode/Some-Episode-id12345"
    )
    expected = "https://castbox.fm/episode/Some-Episode-id12345"

    assert security.extract_url_from_text(share_url) == expected
    assert security.extract_url_from_text(f"posłuchaj: {share_url} dzięki") == expected


def test_extract_url_from_text_handles_all_supported_platforms():
    platforms = {
        "youtube": "https://www.youtube.com/watch?v=abc",
        "youtu.be": "https://youtu.be/abc",
        "vimeo": "https://vimeo.com/123",
        "tiktok": "https://www.tiktok.com/@u/video/1",
        "instagram": "https://www.instagram.com/p/abc/",
        "linkedin": "https://www.linkedin.com/posts/abc",
        "castbox": "https://castbox.fm/episode/abc",
        "spotify": "https://open.spotify.com/episode/abc",
    }
    for _, url in platforms.items():
        assert security.extract_url_from_text(f"prefix text {url}") == url


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
    ) == (1, 1)
    assert security.is_user_blocked(1, now=105.0, block_map=block_map) is False

    assert security.register_pin_failure(
        user_id=1,
        now=106.0,
        attempts=attempts,
        block_map=block_map,
        max_attempts=2,
        block_time=20,
    ) == (0, 2)
    assert security.is_user_blocked(1, now=110.0, block_map=block_map) is True
    assert security.get_block_remaining_seconds(1, now=110.0, block_map=block_map) == 16

    security.clear_failed_attempts(1, attempts=attempts)
    assert attempts[1] == 0


def test_clear_auth_security_state_resets_auth_throttle_but_not_rate_limit_history():
    from bot.services.auth_service import clear_auth_security_state

    attempts = defaultdict(int, {7: 2})
    block_map = defaultdict(float, {7: 123.0})
    security.user_requests.clear()
    security.user_requests[7] = [1.0, 2.0]

    clear_auth_security_state(user_id=7, attempts=attempts, block_map=block_map)

    assert attempts[7] == 0
    assert block_map[7] == 0.0
    assert security.user_requests[7] == [1.0, 2.0]


def test_register_pin_failure_logs_warning_on_failed_attempt(caplog):
    """Verify logging.warning is emitted for a non-blocking failed PIN attempt."""
    import logging

    attempts = defaultdict(int)
    block_map = defaultdict(float)

    with caplog.at_level(logging.WARNING):
        security.register_pin_failure(
            user_id=9001,
            now=100.0,
            attempts=attempts,
            block_map=block_map,
            max_attempts=3,
            block_time=60,
        )

    assert any(
        "Failed PIN attempt for user 9001" in rec.message and "1/3" in rec.message
        for rec in caplog.records
    )


def test_register_pin_failure_logs_warning_on_block(caplog):
    """Verify logging.warning is emitted when user gets blocked."""
    import logging

    attempts = defaultdict(int)
    block_map = defaultdict(float)

    # Use up first attempt
    security.register_pin_failure(
        user_id=9002, now=100.0, attempts=attempts, block_map=block_map,
        max_attempts=2, block_time=60,
    )

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        security.register_pin_failure(
            user_id=9002, now=101.0, attempts=attempts, block_map=block_map,
            max_attempts=2, block_time=60,
        )

    assert any(
        "User 9002 BLOCKED" in rec.message and "2 failed PIN attempts" in rec.message
        for rec in caplog.records
    )


def test_register_pin_failure_returns_actual_attempt_after_reblock():
    """After block expires, actual attempt count keeps incrementing (not reset)."""
    attempts = defaultdict(int)
    block_map = defaultdict(float)

    # First block at attempt 2
    security.register_pin_failure(
        user_id=1, now=100.0, attempts=attempts, block_map=block_map,
        max_attempts=2, block_time=20,
    )
    remaining, actual = security.register_pin_failure(
        user_id=1, now=101.0, attempts=attempts, block_map=block_map,
        max_attempts=2, block_time=20,
    )
    assert remaining == 0
    assert actual == 2

    # Block expires, attempt 3 → immediate re-block
    remaining, actual = security.register_pin_failure(
        user_id=1, now=200.0, attempts=attempts, block_map=block_map,
        max_attempts=2, block_time=20,
    )
    assert remaining == 0
    assert actual == 3  # Real attempt count, not capped at max_attempts


def test_archive_volume_size_constants_defined():
    from bot import security_limits

    assert security_limits.MTPROTO_VOLUME_SIZE_MB == 1900
    assert security_limits.BOTAPI_VOLUME_SIZE_MB == 49
    assert security_limits.BOTAPI_VOLUME_SIZE_MB < security_limits.TELEGRAM_UPLOAD_LIMIT_MB


def test_archive_item_size_limit_defined():
    from bot import security_limits

    assert security_limits.MAX_ARCHIVE_ITEM_SIZE_MB == 10240
    assert security_limits.MAX_ARCHIVE_ITEM_SIZE_MB > security_limits.MAX_FILE_SIZE_MB


def test_playlist_archive_retention_defined():
    from bot import security_limits

    assert security_limits.PLAYLIST_ARCHIVE_RETENTION_MIN == 60
    # Retention must expire before the 24h cleanup safety net so workspaces
    # eligible for retry never collide with the daily eviction.
    assert security_limits.PLAYLIST_ARCHIVE_RETENTION_MIN < 24 * 60
