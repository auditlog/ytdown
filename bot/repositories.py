"""
File-based persistence repositories for runtime application data.
"""

import json
import logging
import os
import shutil
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class DownloadRecord:
    """Structured download history record."""

    timestamp: str
    user_id: int
    title: str
    url: str
    format: str
    status: str = "success"
    file_size_mb: float | None = None
    time_range: str | None = None
    selected_format: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict:
        """Return a JSON-serializable record without empty optional fields."""
        data = asdict(self)
        return {key: value for key, value in data.items() if value is not None}


class AuthorizedUsersRepository:
    """Persistence for authorized Telegram users."""

    def __init__(self, path: str, lock: Any = None):
        self.path = path
        self.lock = lock

    def load(self) -> set[int]:
        """Load authorized user IDs from disk."""
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as file:
                    data = json.load(file)
                return {int(user_id) for user_id in data.get("authorized_users", [])}

            logging.info("File %s does not exist. Creating new.", self.path)
            return set()
        except (json.JSONDecodeError, ValueError, IOError) as exc:
            logging.warning("Error loading %s: %s", self.path, exc)
            logging.warning("Using empty authorized users list.")
            return set()

    def save(self, authorized_users_set: set[int]) -> None:
        """Persist authorized users to disk atomically."""
        payload = {
            "authorized_users": [str(user_id) for user_id in authorized_users_set],
            "last_updated": datetime.now().isoformat(),
            "version": "1.0",
        }

        def _write() -> None:
            temp_file = self.path + ".tmp"
            with open(temp_file, "w", encoding="utf-8") as file:
                json.dump(payload, file, indent=2)

            shutil.move(temp_file, self.path)

            if hasattr(os, "chmod"):
                os.chmod(self.path, 0o600)

            logging.debug("Saved %d authorized users to %s", len(authorized_users_set), self.path)

        try:
            if self.lock is None:
                _write()
            else:
                with self.lock:
                    _write()
        except (IOError, OSError) as exc:
            logging.error("Error saving %s: %s", self.path, exc)


class DownloadHistoryRepository:
    """Persistence for download history and derived statistics."""

    def __init__(self, path: str, max_entries: int, lock: Any = None):
        self.path = path
        self.max_entries = max_entries
        self.lock = lock

    def load(self) -> list[dict]:
        """Load download history from disk."""
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as file:
                    data = json.load(file)
                return data.get("downloads", [])
            return []
        except (json.JSONDecodeError, ValueError, IOError) as exc:
            logging.warning("Error loading %s: %s", self.path, exc)
            return []

    def save(self, history: list[dict]) -> None:
        """Persist download history to disk atomically."""
        truncated_history = history[-self.max_entries :] if len(history) > self.max_entries else history
        payload = {
            "downloads": truncated_history,
            "last_updated": datetime.now().isoformat(),
            "version": "1.0",
        }

        def _write() -> None:
            temp_file = self.path + ".tmp"
            with open(temp_file, "w", encoding="utf-8") as file:
                json.dump(payload, file, indent=2, ensure_ascii=False)

            shutil.move(temp_file, self.path)
            logging.debug("Saved %d download records to %s", len(truncated_history), self.path)

        try:
            if self.lock is None:
                _write()
            else:
                with self.lock:
                    _write()
        except (IOError, OSError) as exc:
            logging.error("Error saving %s: %s", self.path, exc)

    def append(self, record: DownloadRecord) -> None:
        """Append one record while holding the repository lock."""
        def _append() -> None:
            history = self.load()
            history.append(record.to_dict())
            self.save(history)

        if self.lock is None:
            _append()
        else:
            with self.lock:
                _append()

    def stats(self, user_id: int | None = None) -> dict:
        """Compute aggregate statistics for all or one user's history."""
        if self.lock is None:
            history = self.load()
        else:
            with self.lock:
                history = self.load()

        if user_id is not None:
            history = [item for item in history if item.get("user_id") == user_id]

        total_downloads = len(history)
        total_size = sum(item.get("file_size_mb", 0) for item in history)

        format_counts: dict[str, int] = {}
        for item in history:
            format_name = item.get("format", "unknown")
            format_counts[format_name] = format_counts.get(format_name, 0) + 1

        success_count = sum(1 for item in history if item.get("status", "success") == "success")
        failure_count = sum(1 for item in history if item.get("status", "success") == "failure")

        return {
            "total_downloads": total_downloads,
            "total_size_mb": round(total_size, 2),
            "format_counts": format_counts,
            "success_count": success_count,
            "failure_count": failure_count,
            "recent": history[-10:][::-1] if history else [],
        }
