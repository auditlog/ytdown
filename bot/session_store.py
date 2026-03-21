"""Central in-memory session store for chat-scoped runtime state."""

from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from copy import deepcopy
from dataclasses import dataclass, field
from threading import RLock
from typing import Any


@dataclass
class SessionState:
    """Chat-scoped runtime state used by Telegram handlers."""

    current_url: str | None = None
    time_range: dict[str, Any] | None = None
    playlist_data: dict[str, Any] | None = None
    download_progress: dict[str, Any] | None = None


@dataclass
class SecurityRuntimeState:
    """User-scoped runtime state for auth throttling and blocking."""

    failed_attempts: int = 0
    block_until: float = 0.0
    user_requests: list[float] = field(default_factory=list)


class SessionStore:
    """Thread-safe store for chat sessions and lightweight runtime state."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._sessions: dict[int, SessionState] = {}

    def get_session(self, chat_id: int) -> SessionState:
        """Return the active session, creating an empty one when missing."""

        with self._lock:
            session = self._sessions.get(chat_id)
            if session is None:
                session = SessionState()
                self._sessions[chat_id] = session
            return session

    def get_session_copy(self, chat_id: int) -> SessionState:
        """Return a detached copy of a chat session for safe inspection."""

        with self._lock:
            return deepcopy(self.get_session(chat_id))

    def update_session(self, chat_id: int, **fields: Any) -> SessionState:
        """Update selected session fields and return the live session."""

        with self._lock:
            session = self.get_session(chat_id)
            for field_name, value in fields.items():
                setattr(session, field_name, value)
            self._cleanup_if_empty(chat_id)
            return session

    def clear_session(self, chat_id: int) -> None:
        """Remove all stored state for a chat."""

        with self._lock:
            self._sessions.pop(chat_id, None)

    def reset(self) -> None:
        """Remove all sessions from the store."""

        with self._lock:
            self._sessions.clear()

    def iter_field_items(self, field_name: str) -> list[tuple[int, Any]]:
        """Return a stable snapshot of all non-empty values for one field."""

        with self._lock:
            items = []
            for chat_id, session in self._sessions.items():
                value = getattr(session, field_name)
                if value is not None:
                    items.append((chat_id, value))
            return items

    def get_field(self, chat_id: int, field_name: str) -> Any:
        """Read one field from a chat session."""

        with self._lock:
            session = self._sessions.get(chat_id)
            if session is None:
                return None
            return getattr(session, field_name)

    def set_field(self, chat_id: int, field_name: str, value: Any) -> None:
        """Set one field in a chat session."""

        with self._lock:
            session = self.get_session(chat_id)
            setattr(session, field_name, value)
            self._cleanup_if_empty(chat_id)

    def pop_field(self, chat_id: int, field_name: str, default: Any = None) -> Any:
        """Pop one field from a chat session."""

        with self._lock:
            session = self._sessions.get(chat_id)
            if session is None:
                return default
            value = getattr(session, field_name)
            if value is None:
                return default
            setattr(session, field_name, None)
            self._cleanup_if_empty(chat_id)
            return value

    def clear_field(self, field_name: str) -> None:
        """Clear one field from every chat session."""

        with self._lock:
            for chat_id, session in list(self._sessions.items()):
                setattr(session, field_name, None)
                self._cleanup_if_empty(chat_id)

    def _cleanup_if_empty(self, chat_id: int) -> None:
        session = self._sessions.get(chat_id)
        if session is None:
            return
        if (
            session.current_url is None
            and session.time_range is None
            and session.playlist_data is None
            and session.download_progress is None
        ):
            self._sessions.pop(chat_id, None)


class SessionFieldMap(MutableMapping[int, Any]):
    """MutableMapping proxy exposing one session field as a dict-like view."""

    def __init__(self, store: SessionStore, field_name: str) -> None:
        self._store = store
        self._field_name = field_name

    def __getitem__(self, chat_id: int) -> Any:
        value = self._store.get_field(chat_id, self._field_name)
        if value is None:
            raise KeyError(chat_id)
        return value

    def __setitem__(self, chat_id: int, value: Any) -> None:
        self._store.set_field(chat_id, self._field_name, value)

    def __delitem__(self, chat_id: int) -> None:
        value = self._store.pop_field(chat_id, self._field_name, default=None)
        if value is None:
            raise KeyError(chat_id)

    def __iter__(self) -> Iterator[int]:
        for chat_id, _value in self._store.iter_field_items(self._field_name):
            yield chat_id

    def __len__(self) -> int:
        return len(self._store.iter_field_items(self._field_name))

    def clear(self) -> None:
        self._store.clear_field(self._field_name)

    def pop(self, chat_id: int, default: Any = None) -> Any:
        return self._store.pop_field(chat_id, self._field_name, default)


session_store = SessionStore()
user_urls = SessionFieldMap(session_store, "current_url")
user_time_ranges = SessionFieldMap(session_store, "time_range")
user_playlist_data = SessionFieldMap(session_store, "playlist_data")
download_progress = SessionFieldMap(session_store, "download_progress")


class SecurityStore:
    """Thread-safe store for user-scoped security runtime state."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._state: dict[int, SecurityRuntimeState] = {}

    def get_state(self, user_id: int) -> SecurityRuntimeState:
        with self._lock:
            state = self._state.get(user_id)
            if state is None:
                state = SecurityRuntimeState()
                self._state[user_id] = state
            return state

    def get_field(self, user_id: int, field_name: str) -> Any:
        with self._lock:
            state = self._state.get(user_id)
            if state is None:
                return None
            value = getattr(state, field_name)
            if field_name == "user_requests":
                return list(value)
            return value

    def get_live_field(self, user_id: int, field_name: str) -> Any:
        with self._lock:
            return getattr(self.get_state(user_id), field_name)

    def set_field(self, user_id: int, field_name: str, value: Any) -> None:
        with self._lock:
            state = self.get_state(user_id)
            if field_name == "user_requests":
                value = list(value)
            setattr(state, field_name, value)
            self._cleanup_if_empty(user_id)

    def pop_field(self, user_id: int, field_name: str, default: Any = None) -> Any:
        with self._lock:
            state = self._state.get(user_id)
            if state is None:
                return default
            value = getattr(state, field_name)
            empty_value = [] if field_name == "user_requests" else (0.0 if field_name == "block_until" else 0)
            if value == empty_value:
                return default
            if field_name == "user_requests":
                setattr(state, field_name, [])
                value = list(value)
            else:
                setattr(state, field_name, empty_value)
            self._cleanup_if_empty(user_id)
            return value

    def iter_field_items(self, field_name: str) -> list[tuple[int, Any]]:
        with self._lock:
            items = []
            for user_id, state in self._state.items():
                value = getattr(state, field_name)
                if field_name == "user_requests":
                    if value:
                        items.append((user_id, list(value)))
                elif value not in (0, 0.0, None):
                    items.append((user_id, value))
            return items

    def clear_field(self, field_name: str) -> None:
        with self._lock:
            for user_id, state in list(self._state.items()):
                if field_name == "user_requests":
                    state.user_requests = []
                elif field_name == "block_until":
                    state.block_until = 0.0
                else:
                    state.failed_attempts = 0
                self._cleanup_if_empty(user_id)

    def reset(self) -> None:
        with self._lock:
            self._state.clear()

    def snapshot(self) -> dict[int, SecurityRuntimeState]:
        with self._lock:
            return deepcopy(self._state)

    def replace(self, next_state: dict[int, SecurityRuntimeState]) -> None:
        with self._lock:
            self._state = deepcopy(next_state)

    def _cleanup_if_empty(self, user_id: int) -> None:
        state = self._state.get(user_id)
        if state is None:
            return
        if (
            state.failed_attempts == 0
            and state.block_until == 0.0
            and not state.user_requests
        ):
            self._state.pop(user_id, None)


class SecurityFieldMap(MutableMapping[int, Any]):
    """MutableMapping proxy exposing one security field as a dict-like view."""

    # Default values matching defaultdict(int) / defaultdict(float) / defaultdict(list)
    _DEFAULTS = {
        "failed_attempts": 0,
        "block_until": 0.0,
        "user_requests": None,  # handled via get_live_field (auto-creates)
    }

    def __init__(self, store: SecurityStore, field_name: str) -> None:
        self._store = store
        self._field_name = field_name

    def __getitem__(self, user_id: int) -> Any:
        if self._field_name == "user_requests":
            return self._store.get_live_field(user_id, self._field_name)
        value = self._store.get_field(user_id, self._field_name)
        if value is None:
            return self._DEFAULTS.get(self._field_name, 0)
        return value

    def __setitem__(self, user_id: int, value: Any) -> None:
        self._store.set_field(user_id, self._field_name, value)

    def __delitem__(self, user_id: int) -> None:
        value = self._store.pop_field(user_id, self._field_name, default=None)
        if value is None:
            raise KeyError(user_id)

    def __contains__(self, user_id: object) -> bool:
        if not isinstance(user_id, int):
            return False
        return self._store.get_field(user_id, self._field_name) is not None

    def __iter__(self) -> Iterator[int]:
        for user_id, _value in self._store.iter_field_items(self._field_name):
            yield user_id

    def __len__(self) -> int:
        return len(self._store.iter_field_items(self._field_name))

    def clear(self) -> None:
        self._store.clear_field(self._field_name)

    def pop(self, user_id: int, default: Any = None) -> Any:
        return self._store.pop_field(user_id, self._field_name, default)


security_store = SecurityStore()
failed_attempts = SecurityFieldMap(security_store, "failed_attempts")
block_until = SecurityFieldMap(security_store, "block_until")
user_requests = SecurityFieldMap(security_store, "user_requests")
