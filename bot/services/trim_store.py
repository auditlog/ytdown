"""On-disk store of audio sources that can be trimmed for 24 hours.

Layout: <DOWNLOAD_PATH>/<chat_id>/trim_<token>/source.<ext> + meta.json.
State lives on disk instead of SessionStore so ✂️ buttons keep working after
a bot restart. Expiry is enforced here and by bot/cleanup.py, whose
cleanup_old_files also deletes any file older than 24 h (6 h when disk is low).

See also: bot/handlers/audio_delivery.py, bot/handlers/trim_callbacks.py.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bot.config import DOWNLOAD_PATH
from bot.security_limits import TRIM_MIN_FREE_DISK_GB, TRIM_SOURCE_RETENTION_HOURS

TRIM_DIR_PREFIX = "trim_"
SUPPORTED_EXTENSIONS = (".mp3", ".m4a", ".flac")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{11}")
_META_NAME = "meta.json"
# retain_source creates the directory a moment before meta.json and the
# source file exist; never purge a workspace that young.
_PURGE_GRACE_SEC = 300


@dataclass(frozen=True)
class TrimSource:
    token: str
    chat_id: int
    path: Path
    title: str
    performer: str | None
    duration_sec: int
    created_at: datetime  # timezone-aware UTC

    @property
    def workspace(self) -> Path:
        return self.path.parent


def _now() -> datetime:
    return datetime.now(UTC)


def _chat_dir(chat_id: int) -> Path:
    return Path(DOWNLOAD_PATH) / str(chat_id)


def expires_at(source: TrimSource) -> datetime:
    return source.created_at + timedelta(hours=TRIM_SOURCE_RETENTION_HOURS)


def has_room_for_sources() -> bool:
    """False when keeping another source would push the disk into the low zone."""

    root = Path(DOWNLOAD_PATH)
    try:
        root.mkdir(parents=True, exist_ok=True)
        free_gb = shutil.disk_usage(root).free / (1024 ** 3)
    except OSError as exc:
        logging.warning("Cannot check free disk space for trim sources: %s", exc)
        return False
    return free_gb >= TRIM_MIN_FREE_DISK_GB


def _link_or_copy(source: Path, dest: Path) -> None:
    try:
        os.link(source, dest)
    except OSError:
        shutil.copy2(source, dest)


def retain_source(
    chat_id: int,
    file_path,
    *,
    title: str,
    performer: str | None,
    duration_sec: int,
    link: bool = False,
) -> TrimSource | None:
    """Move (or hardlink) a file into the store; None when it cannot be kept.

    Never raises for expected refusals (format, disk); the caller then falls
    back to its previous behaviour. On failure the original file is untouched.
    """

    source_path = Path(file_path)
    ext = source_path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        logging.info("Trim store skipped %s: unsupported extension", source_path.name)
        return None
    if not has_room_for_sources():
        logging.warning(
            "Trim store skipped %s: free disk below %s GB", source_path.name, TRIM_MIN_FREE_DISK_GB
        )
        return None

    token = secrets.token_urlsafe(8)
    workspace = _chat_dir(chat_id) / f"{TRIM_DIR_PREFIX}{token}"
    dest = workspace / f"source{ext}"
    created_at = _now()
    meta = {
        "title": title,
        "performer": performer,
        "duration_sec": int(duration_sec),
        "created_at": created_at.isoformat(),
        "file": dest.name,
    }
    try:
        workspace.mkdir(parents=True)
        # meta.json goes first so a failed move leaves the original in place.
        (workspace / _META_NAME).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        if link:
            _link_or_copy(source_path, dest)
        else:
            shutil.move(str(source_path), str(dest))
    except OSError as exc:
        logging.error("Could not retain trim source %s: %s", source_path, exc)
        shutil.rmtree(workspace, ignore_errors=True)
        return None

    # bot/cleanup.py ages files by mtime, and a download may carry an old
    # mtime (e.g. from the server's Last-Modified); restart the clock so a
    # fresh source survives its 24 h. With link=True this also touches the
    # original upload through the shared inode, which is fresh anyway.
    # Outside the try above: on failure the file is already in the store and
    # must not be rolled back with the workspace.
    try:
        os.utime(dest)
    except OSError as exc:
        logging.warning("Could not refresh mtime of trim source %s: %s", dest, exc)

    logging.info(
        "Trim source retained: chat=%d token=%s size=%.1f MB",
        chat_id, token, dest.stat().st_size / (1024 * 1024),
    )
    return TrimSource(token, chat_id, dest, title, performer, int(duration_sec), created_at)


def _read_source(workspace: Path, chat_id: int, token: str) -> TrimSource | None:
    try:
        meta = json.loads((workspace / _META_NAME).read_text(encoding="utf-8"))
        created_at = datetime.fromisoformat(meta["created_at"])
        path = workspace / Path(str(meta["file"])).name
        title = str(meta["title"])
        performer = meta.get("performer") or None
        duration_sec = int(meta["duration_sec"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    source = TrimSource(token, chat_id, path, title, performer, duration_sec, created_at)
    if _now() >= expires_at(source) or not path.is_file():
        return None
    return source


def load_source(chat_id: int, token: str | None) -> TrimSource | None:
    """Return a live source for this chat, or None when unknown or expired."""

    if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
        return None
    return _read_source(_chat_dir(chat_id) / f"{TRIM_DIR_PREFIX}{token}", chat_id, token)


def discard_source(source: TrimSource) -> None:
    shutil.rmtree(source.workspace, ignore_errors=True)


def purge_expired_sources(chat_dir: Path) -> int:
    """Remove trim workspaces that expired or lost their source file."""

    chat_dir = Path(chat_dir)
    try:
        chat_id = int(chat_dir.name)
    except ValueError:
        return 0
    if not chat_dir.is_dir():
        return 0

    removed = 0
    now = time.time()
    for entry in chat_dir.iterdir():
        if not entry.is_dir() or not entry.name.startswith(TRIM_DIR_PREFIX):
            continue
        token = entry.name.removeprefix(TRIM_DIR_PREFIX)
        if _read_source(entry, chat_id, token) is not None:
            continue
        try:
            age = now - entry.stat().st_mtime
        except OSError:
            continue
        if age < _PURGE_GRACE_SEC:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        removed += 1
        logging.info("Removed expired trim source: %s", entry)
    return removed
