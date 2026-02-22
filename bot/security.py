"""
Security module for YouTube Downloader Telegram Bot.

Handles rate limiting, URL validation, user management, and file size estimation.
"""

import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import DefaultDict
from urllib.parse import urlparse

from bot.config import authorized_users, save_authorized_users


# Maximum failed attempts before blocking
MAX_ATTEMPTS = 3

# Block time in seconds (15 minutes)
BLOCK_TIME = 15 * 60

# Rate limiting - max requests per user
RATE_LIMIT_REQUESTS = 10  # number of requests
RATE_LIMIT_WINDOW = 60    # time window in seconds

# Maximum file size for download (in MB)
MAX_FILE_SIZE_MB = 1000  # 1GB limit

# Maximum MP3 part size for transcription (in MB)
# Groq API has 25MB limit, use 20MB for safety margin
MAX_MP3_PART_SIZE_MB = 20

# Allowed domains
ALLOWED_DOMAINS = [
    'youtube.com',
    'www.youtube.com',
    'youtu.be',
    'm.youtube.com',
    'music.youtube.com'
]


@dataclass
class SecurityState:
    """Container for in-memory security/runtime state."""

    failed_attempts: DefaultDict[int, int]
    block_until: DefaultDict[int, float]
    user_requests: DefaultDict[int, list[float]]


# Dictionary to store URLs (key: chat_id, value: url)
# Needed because callback_data has 64 byte limit
user_urls = {}

# Dictionary to store time ranges (key: chat_id, value: {"start": "0:30", "end": "5:45"})
user_time_ranges = {}


def _new_state() -> SecurityState:
    """Create a new empty security state."""

    return SecurityState(
        failed_attempts=defaultdict(int),
        block_until=defaultdict(float),
        user_requests=defaultdict(list),
    )


_security_state = _new_state()

# Public aliases kept for backward compatibility and test patchability.
failed_attempts = _security_state.failed_attempts
block_until = _security_state.block_until
user_requests = _security_state.user_requests


def get_security_state() -> SecurityState:
    """Returns an isolated copy of the active state."""

    return SecurityState(
        failed_attempts=defaultdict(int, failed_attempts),
        block_until=defaultdict(float, block_until),
        user_requests=defaultdict(list, {
            user_id: list(values) for user_id, values in user_requests.items()
        }),
    )


def set_security_state(state: SecurityState) -> SecurityState:
    """Replace active state values with values from provided state object."""

    failed_attempts.clear()
    block_until.clear()
    user_requests.clear()

    failed_attempts.update(state.failed_attempts)
    block_until.update(state.block_until)
    user_requests.update({
        user_id: list(values) for user_id, values in state.user_requests.items()
    })
    return get_security_state()


def reset_security_state() -> SecurityState:
    """Clears and returns a fresh security state."""

    return set_security_state(_new_state())


def check_rate_limit(
    user_id: int,
    state: SecurityState | None = None,
    current_time: float | None = None,
) -> bool:
    """
    Checks if user hasn't exceeded request limit.

    Args:
        user_id: Telegram user ID
        state: Optional test/runtime state override
        current_time: Optional explicit timestamp for deterministic tests

    Returns:
        bool: True if can continue, False if limit exceeded
    """
    if state is None:
        state = SecurityState(
            failed_attempts=failed_attempts,
            block_until=block_until,
            user_requests=user_requests,
        )

    now = current_time or time.time()

    # Remove old requests outside time window
    state.user_requests[user_id] = [
        request_at
        for request_at in state.user_requests[user_id]
        if now - request_at < RATE_LIMIT_WINDOW
    ]

    # Check if limit exceeded
    if len(state.user_requests[user_id]) >= RATE_LIMIT_REQUESTS:
        return False

    # Add new request
    state.user_requests[user_id].append(now)
    return True


def _as_attempt_map(attempts: DefaultDict[int, int] | None = None) -> DefaultDict[int, int]:
    return attempts if attempts is not None else failed_attempts


def _as_block_map(
    block_map: DefaultDict[int, float] | None = None,
) -> DefaultDict[int, float]:
    return block_map if block_map is not None else block_until


def is_user_blocked(
    user_id: int,
    *,
    now: float | None = None,
    block_map: DefaultDict[int, float] | None = None,
) -> bool:
    """
    Checks whether the user is currently blocked.
    """

    current_time = now or time.time()
    return current_time < _as_block_map(block_map).get(user_id, 0.0)


def get_block_remaining_seconds(
    user_id: int,
    *,
    now: float | None = None,
    block_map: DefaultDict[int, float] | None = None,
) -> int:
    """Returns remaining blocked seconds for user."""

    current_time = now or time.time()
    block_until_time = _as_block_map(block_map).get(user_id, 0.0)
    remaining = block_until_time - current_time
    return int(remaining) if remaining > 0 else 0


def clear_failed_attempts(
    user_id: int,
    *,
    attempts: DefaultDict[int, int] | None = None,
) -> None:
    """Reset failed PIN attempts for user."""

    _as_attempt_map(attempts)[user_id] = 0


def register_pin_failure(
    user_id: int,
    *,
    now: float | None = None,
    attempts: DefaultDict[int, int] | None = None,
    block_map: DefaultDict[int, float] | None = None,
    max_attempts: int = MAX_ATTEMPTS,
    block_time: int = BLOCK_TIME,
) -> int:
    """
    Increments failed PIN attempts and optionally sets block time.

    Returns remaining attempts until block (0 when blocked now).
    """

    attempts_map = _as_attempt_map(attempts)
    block_until_map = _as_block_map(block_map)
    current_time = now or time.time()

    attempts_map[user_id] += 1
    current_attempt = attempts_map[user_id]

    if current_attempt >= max_attempts:
        block_until_map[user_id] = current_time + block_time
        logging.warning(
            "User %s BLOCKED after %d failed PIN attempts",
            user_id, current_attempt,
        )
        return 0

    logging.warning(
        "Failed PIN attempt for user %s (attempt %d/%d)",
        user_id, current_attempt, max_attempts,
    )
    return max_attempts - current_attempt


def validate_youtube_url(url):
    """
    Validates YouTube URL.

    Args:
        url: URL to validate

    Returns:
        bool: True if URL is valid, False otherwise
    """
    try:
        # Only HTTPS is allowed (secure connection)
        if not url.startswith('https://'):
            return False

        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        # Remove 'www.' if exists
        if domain.startswith('www.'):
            domain = domain[4:]

        # Check if domain is in allowed list
        return domain in ALLOWED_DOMAINS
    except Exception:
        return False


def manage_authorized_user(user_id, action='add'):
    """
    Manages authorized users.

    Args:
        user_id (int): User ID
        action (str): 'add' or 'remove'

    Returns:
        bool: True if operation succeeded
    """
    try:
        if action == 'add':
            if user_id not in authorized_users:
                authorized_users.add(user_id)
                save_authorized_users(authorized_users)
                logging.info(f"Added user {user_id} to authorized")
                return True
            logging.info(f"User {user_id} is already authorized")
            return True

        if action == 'remove':
            if user_id in authorized_users:
                authorized_users.discard(user_id)
                save_authorized_users(authorized_users)
                logging.info(f"Removed user {user_id} from authorized")
                return True
            logging.info(f"User {user_id} was not authorized")
            return True

        logging.error(f"Unknown action: {action}")
        return False

    except Exception as exc:
        logging.error(f"Error managing user {user_id}: {exc}")
        return False


def estimate_file_size(info):
    """
    Estimates file size based on yt-dlp info.

    Args:
        info: Video info dictionary from yt-dlp

    Returns:
        float or None: Size in MB or None if cannot estimate
    """
    try:
        # Try to find format with size
        formats = info.get('formats', [])
        for fmt in formats:
            if fmt.get('filesize'):
                return fmt['filesize'] / (1024 * 1024)

        # If no exact size, try to estimate
        duration = info.get('duration', 0)
        if duration:
            # Assume average bitrate for different qualities
            bitrate_mbps = 5  # 5 Mbps for average quality video
            estimated_mb = (duration * bitrate_mbps * 0.125)
            return estimated_mb

        return None
    except Exception:
        return None
