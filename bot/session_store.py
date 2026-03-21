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
