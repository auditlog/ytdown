"""PIN blocking and failed-attempt tracking helpers."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import DefaultDict

from bot.security_limits import BLOCK_TIME, MAX_ATTEMPTS
from bot.session_store import block_until, failed_attempts


def _as_attempt_map(attempts: DefaultDict[int, int] | None = None) -> DefaultDict[int, int]:
    return attempts if attempts is not None else failed_attempts


def _as_block_map(block_map: DefaultDict[int, float] | None = None) -> DefaultDict[int, float]:
    return block_map if block_map is not None else block_until


def is_user_blocked(
    user_id: int,
    *,
    now: float | None = None,
    block_map: DefaultDict[int, float] | None = None,
) -> bool:
    """Return True when a user is still within an active block interval."""

    current_time = now or time.time()
    return current_time < _as_block_map(block_map).get(user_id, 0.0)


def get_block_remaining_seconds(
    user_id: int,
    *,
    now: float | None = None,
    block_map: DefaultDict[int, float] | None = None,
) -> int:
    """Return remaining block time in whole seconds."""

    current_time = now or time.time()
    remaining = _as_block_map(block_map).get(user_id, 0.0) - current_time
    return int(remaining) if remaining > 0 else 0


def clear_failed_attempts(
    user_id: int,
    *,
    attempts: DefaultDict[int, int] | None = None,
) -> None:
    """Reset failed PIN attempts for the user."""

    _as_attempt_map(attempts)[user_id] = 0


def register_pin_failure(
    user_id: int,
    *,
    now: float | None = None,
    attempts: DefaultDict[int, int] | None = None,
    block_map: DefaultDict[int, float] | None = None,
    max_attempts: int = MAX_ATTEMPTS,
    block_time: int = BLOCK_TIME,
) -> tuple[int, int]:
    """Record failed PIN attempt and return remaining attempts plus actual count."""

    attempts_map = _as_attempt_map(attempts)
    block_until_map = _as_block_map(block_map)
    current_time = now or time.time()

    attempts_map[user_id] += 1
    current_attempt = attempts_map[user_id]

    if current_attempt >= max_attempts:
        block_until_map[user_id] = current_time + block_time
        logging.warning("User %s BLOCKED after %d failed PIN attempts", user_id, current_attempt)
        return (0, current_attempt)

    logging.warning(
        "Failed PIN attempt for user %s (attempt %d/%d)",
        user_id, current_attempt, max_attempts,
    )
    return (max_attempts - current_attempt, current_attempt)
