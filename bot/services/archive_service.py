"""End-to-end orchestration for 7z archive flows (playlist + single-file).

Boundaries:
- ``bot.archive`` — pure 7z wrapper, no Telegram/session knowledge.
- ``archive_service`` (this module) — knows about sessions, downloads,
  Telegram bot client, and gluing them together.
- ``bot.handlers.*`` — translate inline keyboard callbacks into calls
  on this service, never call ``bot.archive`` directly.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime
from pathlib import Path

from bot.archive import compute_archive_basename, transliterate_to_ascii
from bot.config import DOWNLOAD_PATH
from bot.downloader_validation import sanitize_filename
from bot.session_store import (
    ArchiveJobState,
    ArchivedDeliveryState,
    archived_deliveries,
    pending_archive_jobs,
)


_SLUG_MAX_LEN = 60


def _build_slug(title: str) -> str:
    """Translit-then-sanitize playlist/file title for use in filesystem path."""

    transliterated = transliterate_to_ascii(title)
    sanitized = sanitize_filename(transliterated)
    cleaned = sanitized.replace(" ", "_")
    return cleaned[:_SLUG_MAX_LEN] or "untitled"


def prepare_playlist_workspace(
    chat_id: int,
    playlist_title: str,
    *,
    prefix: str = "pl",
) -> Path:
    """Create ``downloads/<chat_id>/<prefix>_<slug>_<ts>/`` and return it."""

    slug = _build_slug(playlist_title)
    basename = compute_archive_basename(slug, datetime.now())
    chat_dir = Path(DOWNLOAD_PATH) / str(chat_id)
    chat_dir.mkdir(parents=True, exist_ok=True)
    workspace = chat_dir / f"{prefix}_{basename}"
    workspace.mkdir(parents=True, exist_ok=True)
    logging.info("Archive workspace ready: %s", workspace)
    return workspace


def register_pending_archive_job(chat_id: int, state: ArchiveJobState) -> str:
    """Store a pending archive job and return the lookup token (8 hex chars)."""

    token = secrets.token_hex(4)
    bucket = pending_archive_jobs.get(chat_id) or {}
    bucket[token] = state
    pending_archive_jobs[chat_id] = bucket
    return token


def register_archived_delivery(chat_id: int, state: ArchivedDeliveryState) -> str:
    """Store delivery metadata for retry/purge actions and return its token."""

    token = secrets.token_hex(4)
    bucket = archived_deliveries.get(chat_id) or {}
    bucket[token] = state
    archived_deliveries[chat_id] = bucket
    return token
