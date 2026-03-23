"""Request throttling and transport-abuse protection helpers."""

from __future__ import annotations

import time

from bot.security_limits import RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW
from bot.session_store import user_requests


def check_rate_limit(
    user_id: int,
    requests_map=None,
    current_time: float | None = None,
    *,
    window_seconds: int = RATE_LIMIT_WINDOW,
    max_requests: int = RATE_LIMIT_REQUESTS,
) -> bool:
    """Return True when the user is still within the configured rate limit."""

    active_requests = requests_map if requests_map is not None else user_requests
    now = current_time or time.time()

    active_requests[user_id] = [
        request_at for request_at in active_requests[user_id]
        if now - request_at < window_seconds
    ]

    if len(active_requests[user_id]) >= max_requests:
        return False

    active_requests[user_id].append(now)
    return True
