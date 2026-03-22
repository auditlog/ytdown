"""Authentication service for PIN-based Telegram access control.

The service operates on a mutable auth/session mapping supplied by the
Telegram layer. In runtime-aware handlers this mapping is backed by
``SessionStore`` and only mirrors to ``context.user_data`` when compatibility
requires it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from bot.security import (
    BLOCK_TIME,
    MAX_ATTEMPTS,
    clear_failed_attempts,
    get_block_remaining_seconds,
    is_user_blocked,
    register_pin_failure,
)


@dataclass
class PendingAction:
    """Deferred post-login action stored in auth/session state."""

    kind: str
    payload: Any


@dataclass
class StartResult:
    """Decision for the /start command."""

    message: str
    awaiting_pin: bool = False


@dataclass
class PinResult:
    """Outcome of a PIN-processing attempt."""

    handled: bool
    message: str | None = None
    delete_message: bool = False
    notify_admin: bool = False
    blocked: bool = False
    attempt_count: int = 0
    pending_action: PendingAction | None = None


def build_blocked_message(user_id: int, *, block_map) -> str:
    """Build a standard blocked-user message."""

    remaining_time = get_block_remaining_seconds(user_id, block_map=block_map)
    minutes = remaining_time // 60
    seconds = remaining_time % 60
    return (
        "Dostęp zablokowany z powodu zbyt wielu nieudanych prób. "
        f"Spróbuj ponownie za {minutes} min {seconds} s."
    )


def handle_start(
    *,
    user_id: int,
    user_name: str,
    authorized_user_ids: Iterable[int],
    user_data: dict[str, Any],
    block_map,
) -> StartResult:
    """Return the appropriate /start response and mutate auth state when needed."""

    if is_user_blocked(user_id, block_map=block_map):
        return StartResult(
            message=(
                f"Witaj, {user_name}!\n\n"
                f"{build_blocked_message(user_id, block_map=block_map)}"
            )
        )

    if user_id in authorized_user_ids:
        return StartResult(
            message=(
                f"Witaj, {user_name}!\n\n"
                "Jesteś już zalogowany. Wyślij link (YouTube, Vimeo, TikTok, Instagram, LinkedIn, Castbox, Spotify) "
                "aby pobrać film lub audio."
            )
        )

    user_data["awaiting_pin"] = True
    return StartResult(
        message=(
            f"Witaj, {user_name}!\n\n"
            "To jest bot chroniony PIN-em.\n"
            "Aby korzystać z bota, podaj 8-cyfrowy kod PIN."
        ),
        awaiting_pin=True,
    )


def store_pending_action(user_data: dict[str, Any], *, kind: str, payload: Any) -> None:
    """Store a deferred action that should continue after successful login."""

    user_data[f"pending_{kind}"] = payload
    user_data["awaiting_pin"] = True


def consume_pending_action(user_data: dict[str, Any]) -> PendingAction | None:
    """Pop and return the next deferred action in stable priority order."""

    for kind in ("url", "audio", "video"):
        key = f"pending_{kind}"
        if key in user_data:
            payload = user_data.pop(key)
            return PendingAction(kind=kind, payload=payload)
    return None


def handle_pin_input(
    *,
    user_id: int,
    message_text: str | None,
    user_data: dict[str, Any],
    pin_code: str,
    authorized_user_ids: Iterable[int],
    attempts,
    block_map,
    authorize_user: Callable[[int], Any],
    max_attempts: int = MAX_ATTEMPTS,
    block_time: int = BLOCK_TIME,
) -> PinResult:
    """Process a potential PIN input and return a structured auth result."""

    if is_user_blocked(user_id, block_map=block_map):
        return PinResult(
            handled=True,
            message=build_blocked_message(user_id, block_map=block_map),
            delete_message=True,
            blocked=True,
        )

    awaiting_pin = user_data.get("awaiting_pin", False)
    is_authorized = user_id in authorized_user_ids
    if not (awaiting_pin or not is_authorized):
        return PinResult(handled=False)

    if not message_text or not message_text.isdigit():
        return PinResult(handled=False)

    if message_text == pin_code:
        clear_failed_attempts(user_id, attempts=attempts)
        authorize_user(user_id)
        user_data.pop("awaiting_pin", None)
        pending_action = consume_pending_action(user_data)
        return PinResult(
            handled=True,
            message=(
                "PIN poprawny! Możesz teraz korzystać z bota.\n\n"
                "Wyślij link (YouTube, Vimeo, TikTok, Instagram, LinkedIn, Castbox, Spotify) "
                "aby pobrać film lub audio."
            ),
            delete_message=True,
            pending_action=pending_action,
        )

    remaining_attempts, attempt_count = register_pin_failure(
        user_id,
        attempts=attempts,
        block_map=block_map,
        max_attempts=max_attempts,
        block_time=block_time,
    )
    blocked = remaining_attempts == 0
    if blocked:
        message = (
            "Niepoprawny PIN!\n\n"
            f"Przekroczono maksymalną liczbę prób ({max_attempts}).\n"
            f"Dostęp zablokowany na {block_time // 60} minut."
        )
    else:
        message = (
            "Niepoprawny PIN!\n\n"
            f"Pozostało prób: {remaining_attempts}"
        )

    return PinResult(
        handled=True,
        message=message,
        delete_message=True,
        notify_admin=True,
        blocked=blocked,
        attempt_count=attempt_count,
    )


def logout_user(
    *,
    user_id: int,
    chat_id: int,
    authorized_user_ids: Iterable[int],
    remove_authorized_user: Callable[[int], Any],
    user_data: dict[str, Any],
    user_urls: dict[int, Any],
    user_time_ranges: dict[int, Any],
) -> bool:
    """Clear auth/session state for a logged-in user."""

    if user_id not in authorized_user_ids:
        return False

    remove_authorized_user(user_id)
    user_urls.pop(chat_id, None)
    user_time_ranges.pop(chat_id, None)
    user_data.clear()
    return True
