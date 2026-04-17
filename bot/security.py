"""Compatibility facade for security helpers and runtime state."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import DefaultDict

from bot.config import _auth_lock, add_runtime_authorized_user, remove_runtime_authorized_user
from bot.security_authorization import manage_authorized_user
from bot.security_limits import (
    BLOCK_TIME,
    FFMPEG_TIMEOUT,
    MAX_ATTEMPTS,
    MAX_FILE_SIZE_MB,
    MAX_MP3_PART_SIZE_MB,
    MAX_PLAYLIST_ITEMS,
    MAX_PLAYLIST_ITEMS_EXPANDED,
    RATE_LIMIT_REQUESTS,
    RATE_LIMIT_WINDOW,
)
from bot.security_pin import (
    clear_failed_attempts,
    get_block_remaining_seconds,
    is_user_blocked,
    register_pin_failure,
)
from bot.security_policy import (
    ALLOWED_DOMAINS,
    detect_platform,
    estimate_file_size,
    extract_url_from_text,
    get_media_label,
    normalize_url,
    validate_url,
)
from bot.security_throttling import check_rate_limit
from bot.session_store import (
    SecurityRuntimeState,
    block_until,
    failed_attempts,
    security_store,
    user_playlist_data,
    user_requests,
    user_time_ranges,
    user_urls,
)

validate_youtube_url = validate_url


@dataclass
class SecurityState:
    """Container for in-memory security/runtime state."""

    failed_attempts: DefaultDict[int, int]
    block_until: DefaultDict[int, float]
    user_requests: DefaultDict[int, list[float]]


def get_security_state() -> SecurityState:
    """Return an isolated copy of the active state."""

    snapshot = security_store.snapshot()
    return SecurityState(
        failed_attempts=defaultdict(int, {
            user_id: state.failed_attempts for user_id, state in snapshot.items()
        }),
        block_until=defaultdict(float, {
            user_id: state.block_until for user_id, state in snapshot.items()
        }),
        user_requests=defaultdict(list, {
            user_id: list(state.user_requests) for user_id, state in snapshot.items()
        }),
    )


def set_security_state(state: SecurityState) -> SecurityState:
    """Replace active state values with the provided state object."""

    next_state = {}
    for user_id in set(state.failed_attempts) | set(state.block_until) | set(state.user_requests):
        next_state[user_id] = SecurityRuntimeState(
            failed_attempts=state.failed_attempts.get(user_id, 0),
            block_until=state.block_until.get(user_id, 0.0),
            user_requests=list(state.user_requests.get(user_id, [])),
        )

    security_store.replace(next_state)
    return get_security_state()


def reset_security_state() -> SecurityState:
    """Clear and return a fresh security state."""

    security_store.reset()
    return get_security_state()



# manage_authorized_user re-exported from bot.security_authorization
