"""Request throttling and transport-abuse protection helpers."""

from __future__ import annotations

import time

from bot.security_limits import RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW
from bot.session_store import user_requests

RATE_LIMIT_MESSAGE = (
    f"Przekroczono limit żądań ({RATE_LIMIT_REQUESTS} na {RATE_LIMIT_WINDOW} s). "
    "Poczekaj chwilę i spróbuj ponownie."
)

# Short text for callback-query toasts (answer(..., show_alert=True)); the menu
# message stays untouched. Telegram caps alert text at 200 characters.
RATE_LIMIT_TOAST = "Za dużo żądań — poczekaj chwilę i spróbuj ponownie."


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
