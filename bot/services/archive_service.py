"""End-to-end orchestration for 7z archive flows (playlist + single-file).

Boundaries:
- ``bot.archive`` — pure 7z wrapper, no Telegram/session knowledge.
- ``archive_service`` (this module) — knows about sessions, downloads,
  Telegram bot client, and gluing them together.
- ``bot.handlers.*`` — translate inline keyboard callbacks into calls
  on this service, never call ``bot.archive`` directly.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from bot.archive import compute_archive_basename, transliterate_to_ascii
from bot.config import DOWNLOAD_PATH
from bot.downloader_validation import sanitize_filename
from bot.security_limits import MAX_ARCHIVE_ITEM_SIZE_MB
from bot.services.download_service import (
    DownloadResult,
    ensure_size_within_limit,
    estimate_download_size,
    execute_download,
    prepare_download_plan,
)
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


async def _noop_status(_text: str) -> None:
    """No-op async status callback used internally by _download_one_into_workspace."""
    return None


async def _download_one_into_workspace(
    entry: dict,
    workspace: Path,
    *,
    media_type: str,
    format_choice: str,
    executor: ThreadPoolExecutor,
) -> tuple[Path | None, float | None]:
    """Download one playlist item into ``workspace``. Returns (path, size_mb).

    Returns (None, size_mb) when the estimated size exceeds
    MAX_ARCHIVE_ITEM_SIZE_MB; the caller should record this as a failure
    with a descriptive title.
    Raises on metadata fetch failure or yt-dlp errors.
    """

    plan = prepare_download_plan(
        url=entry["url"],
        media_type=media_type,
        format_choice=format_choice,
        chat_download_path=str(workspace),
    )
    if plan is None:
        raise RuntimeError(f"could not fetch metadata for {entry.get('title')}")

    try:
        estimated = estimate_download_size(plan)
    except Exception:
        estimated = None

    if estimated is not None and not ensure_size_within_limit(
        estimated, max_size_mb=MAX_ARCHIVE_ITEM_SIZE_MB
    ):
        return None, estimated

    result = await execute_download(
        plan,
        chat_id=0,  # not used for progress reporting in archive flow
        executor=executor,
        progress_hook_factory=lambda _cid: (lambda _data: None),
        progress_state={},
        status_callback=_noop_status,
        format_bytes=lambda v: str(v),
        format_eta=lambda v: str(v),
    )
    return Path(result.file_path), result.file_size_mb


async def download_playlist_into(
    workspace: Path,
    entries: list[dict],
    *,
    media_type: str,
    format_choice: str,
    executor: ThreadPoolExecutor,
    status_cb: Callable[[str], Awaitable[None]],
) -> tuple[list[Path], list[str]]:
    """Download every entry into workspace, keeping the files (no os.remove).

    Returns (downloaded_paths, failed_titles). Items exceeding the
    MAX_ARCHIVE_ITEM_SIZE_MB cap are reported on failed_titles with a
    ``(za duzy: X MB)`` suffix. Network failures are similarly recorded
    with the original title.
    """

    downloaded: list[Path] = []
    failed: list[str] = []
    total = len(entries)

    for idx, entry in enumerate(entries, 1):
        title = entry.get("title", f"item_{idx}")
        await status_cb(f"[{idx}/{total}] Pobieranie: {title}...")
        try:
            path, size = await _download_one_into_workspace(
                entry,
                workspace,
                media_type=media_type,
                format_choice=format_choice,
                executor=executor,
            )
        except Exception as exc:
            logging.error("Archive download failed for %s: %s", title, exc)
            failed.append(title)
            continue

        if path is None:
            mb_str = f"{size:.0f} MB" if size is not None else "?"
            failed.append(f"{title} (za duzy: {mb_str})")
            continue

        downloaded.append(path)

    return downloaded, failed
