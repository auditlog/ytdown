"""Runtime-aware helpers for chat/session state used by Telegram handlers.

Ownership rules:
- chat-scoped flow state belongs to ``SessionStore`` when ``AppRuntime`` is
  attached,
- ``context.user_data`` is only a compatibility bridge for legacy PTB paths,
- legacy dict-like maps from ``bot.security`` remain fallback storage for
  code paths that run without runtime attachment.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from telegram.ext import ContextTypes

from bot.runtime import get_app_runtime

TRANSIENT_FLOW_FIELDS = (
    "current_url",
    "time_range",
    "playlist_data",
    "platform",
    "spotify_resolved",
    "instagram_carousel",
    "audio_file_path",
    "audio_file_title",
    "subtitle_pending",
)

TRANSIENT_FLOW_LEGACY_KEYS = (
    "platform",
    "spotify_resolved",
    "ig_carousel",
    "audio_file_path",
    "audio_file_title",
    "subtitle_pending",
)


class AuthSessionData(MutableMapping[str, object]):
    """Mutable auth state view backed by SessionStore when runtime is present."""

    _PENDING_KEYS = ("pending_url", "pending_audio", "pending_video")

    def __init__(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
        self._context = context
        self._chat_id = chat_id

    def _runtime(self):
        return get_app_runtime(self._context)

    def _legacy(self) -> dict:
        return self._context.user_data

    def _get_pending(self):
        runtime = self._runtime()
        if runtime is not None:
            pending = runtime.session_store.get_field(self._chat_id, "pending_action")
            if pending is not None:
                return pending

        legacy = self._legacy()
        for kind in ("url", "audio", "video"):
            key = f"pending_{kind}"
            if key in legacy:
                return {"kind": kind, "payload": legacy[key]}
        return None

    def _set_pending(self, kind: str, payload: Any) -> None:
        runtime = self._runtime()
        if runtime is not None:
            runtime.session_store.set_field(
                self._chat_id,
                "pending_action",
                {"kind": kind, "payload": payload},
            )
            for key in self._PENDING_KEYS:
                self._legacy().pop(key, None)
            return

        self._legacy()[f"pending_{kind}"] = payload

    def _clear_pending(self) -> None:
        runtime = self._runtime()
        if runtime is not None:
            runtime.session_store.pop_field(self._chat_id, "pending_action", None)
        for key in self._PENDING_KEYS:
            self._legacy().pop(key, None)

    def __getitem__(self, key: str):
        value = self.get(key, None)
        if value is None:
            raise KeyError(key)
        return value

    def __setitem__(self, key: str, value) -> None:
        if key == "awaiting_pin":
            runtime = self._runtime()
            if runtime is not None:
                runtime.session_store.set_field(self._chat_id, "awaiting_pin", bool(value))
                self._legacy().pop(key, None)
            else:
                self._legacy()[key] = value
            return

        if key.startswith("pending_"):
            self._set_pending(key.removeprefix("pending_"), value)
            return

        self._legacy()[key] = value

    def __delitem__(self, key: str) -> None:
        marker = object()
        value = self.pop(key, marker)
        if value is marker:
            raise KeyError(key)

    def __iter__(self):
        keys = set(self._legacy().keys())
        if self.get("awaiting_pin", None):
            keys.add("awaiting_pin")
        pending = self._get_pending()
        if pending is not None:
            keys.add(f"pending_{pending['kind']}")
        return iter(keys)

    def __len__(self) -> int:
        return len(list(iter(self)))

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        return self.get(key, None) is not None

    def get(self, key: str, default=None):
        if key == "awaiting_pin":
            runtime = self._runtime()
            if runtime is not None:
                value = runtime.session_store.get_field(self._chat_id, "awaiting_pin")
                if value is not None:
                    return value
            return self._legacy().get(key, default)

        if key.startswith("pending_"):
            pending = self._get_pending()
            kind = key.removeprefix("pending_")
            if pending is not None and pending.get("kind") == kind:
                return pending.get("payload")
            return self._legacy().get(key, default)

        return self._legacy().get(key, default)

    def pop(self, key: str, default=None):
        if key == "awaiting_pin":
            runtime = self._runtime()
            if runtime is not None:
                value = runtime.session_store.pop_field(self._chat_id, "awaiting_pin", None)
                self._legacy().pop(key, None)
                return default if value is None else value
            return self._legacy().pop(key, default)

        if key.startswith("pending_"):
            kind = key.removeprefix("pending_")
            pending = self._get_pending()
            if pending is not None and pending.get("kind") == kind:
                self._clear_pending()
                return pending.get("payload")
            return self._legacy().pop(key, default)

        return self._legacy().pop(key, default)

    def clear(self) -> None:
        runtime = self._runtime()
        if runtime is not None:
            runtime.session_store.clear_fields(
                self._chat_id,
                "awaiting_pin",
                "pending_action",
            )
        self._legacy().clear()


def get_auth_state(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
) -> MutableMapping[str, object]:
    """Return mutable auth flow state backed by runtime session when available."""

    return AuthSessionData(context, chat_id)


def get_session_value(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    field_name: str,
    legacy_map,
):
    """Read one chat-scoped value from runtime session store when available."""

    runtime = get_app_runtime(context)
    if runtime is not None:
        return runtime.session_store.get_field(chat_id, field_name)
    return legacy_map.get(chat_id)


def set_session_value(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    field_name: str,
    value,
    legacy_map,
) -> None:
    """Write one chat-scoped value through runtime session store when available."""

    runtime = get_app_runtime(context)
    if runtime is not None:
        runtime.session_store.set_field(chat_id, field_name, value)
        return
    legacy_map[chat_id] = value


def clear_session_value(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    field_name: str,
    legacy_map,
) -> None:
    """Clear one chat-scoped value through runtime session store when available."""

    runtime = get_app_runtime(context)
    if runtime is not None:
        runtime.session_store.pop_field(chat_id, field_name, None)
        return
    legacy_map.pop(chat_id, None)


def get_session_context_value(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    field_name: str,
    *,
    legacy_key: str,
    default=None,
):
    """Read one session-scoped value from runtime or legacy ``user_data``."""

    runtime = get_app_runtime(context)
    if runtime is not None:
        value = runtime.session_store.get_field(chat_id, field_name)
        if value is not None:
            return value
    return context.user_data.get(legacy_key, default)


def set_session_context_value(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    field_name: str,
    value,
    *,
    legacy_key: str,
) -> None:
    """Write one session-scoped value to runtime and clear legacy bridge data."""

    runtime = get_app_runtime(context)
    if runtime is not None:
        runtime.session_store.set_field(chat_id, field_name, value)
        context.user_data.pop(legacy_key, None)
        return
    context.user_data[legacy_key] = value


def clear_session_context_value(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    field_name: str,
    *,
    legacy_key: str,
) -> None:
    """Clear one session-scoped value from runtime and legacy ``user_data``."""

    runtime = get_app_runtime(context)
    if runtime is not None:
        runtime.session_store.pop_field(chat_id, field_name, None)
    context.user_data.pop(legacy_key, None)


def clear_transient_flow_state(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    *,
    user_urls,
    user_time_ranges,
    user_playlist_data,
) -> None:
    """Clear transient chat flow state after completion, cancellation, or logout."""

    runtime = get_app_runtime(context)
    if runtime is not None:
        runtime.session_store.clear_fields(chat_id, *TRANSIENT_FLOW_FIELDS)

    for legacy_key in TRANSIENT_FLOW_LEGACY_KEYS:
        context.user_data.pop(legacy_key, None)

    if runtime is None:
        user_urls.pop(chat_id, None)
        user_time_ranges.pop(chat_id, None)
        user_playlist_data.pop(chat_id, None)


def clear_uploaded_audio_state(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
) -> None:
    """Clear temporary audio-upload state after transcription completion or failure."""

    clear_session_context_value(
        context,
        chat_id,
        "audio_file_path",
        legacy_key="audio_file_path",
    )
    clear_session_context_value(
        context,
        chat_id,
        "audio_file_title",
        legacy_key="audio_file_title",
    )
