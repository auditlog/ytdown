"""Playlist application service for Telegram handlers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.helpers import escape_markdown

from bot.config import DOWNLOAD_PATH
from bot.downloader_playlist import get_playlist_info, strip_playlist_params
from bot.security_limits import MAX_PLAYLIST_ITEMS_EXPANDED
from bot.services.download_service import (
    DownloadResult,
    ensure_size_within_limit,
    estimate_download_size,
    execute_download,
    find_downloaded_file,
    prepare_download_plan,
)


@dataclass
class PlaylistDownloadChoice:
    """Parsed playlist callback choice."""

    media_type: str
    format_choice: str
    as_archive: bool = False


def load_playlist(url: str, *, max_items: int) -> dict[str, Any] | None:
    """Load playlist metadata for the requested item limit."""

    return get_playlist_info(url, max_items=max_items)


def build_playlist_message(
    playlist_info: dict[str, Any],
    *,
    archive_available: bool = False,
) -> tuple[str, InlineKeyboardMarkup]:
    """Build playlist listing text and controls.

    When ``archive_available`` is True, four extra "... jako 7z" buttons
    are inserted alongside the existing per-item-send buttons.
    """

    entries = playlist_info['entries']
    total = playlist_info.get('playlist_count', len(entries))

    msg = f"*{escape_markdown(playlist_info['title'], version=1)}*\n"
    msg += f"Filmów: {len(entries)}"
    if total > len(entries):
        msg += f" (z {total})"
    msg += "\n\n"

    for i, entry in enumerate(entries, 1):
        title = escape_markdown(entry.get('title', 'Nieznany')[:50], version=1)
        duration = entry.get('duration')
        dur_str = f"{duration // 60}:{duration % 60:02d}" if duration else "?"
        msg += f"{i}. {title} ({dur_str})\n"

    options = [
        ("Pobierz wszystkie — Audio MP3", "pl_dl_audio_mp3", "pl_zip_dl_audio_mp3"),
        ("Pobierz wszystkie — Audio M4A", "pl_dl_audio_m4a", "pl_zip_dl_audio_m4a"),
        ("Pobierz wszystkie — Video (najlepsza)", "pl_dl_video_best", "pl_zip_dl_video_best"),
        ("Pobierz wszystkie — Video 720p", "pl_dl_video_720p", "pl_zip_dl_video_720p"),
    ]
    keyboard: list[list[InlineKeyboardButton]] = []
    for label, plain, archive_cb in options:
        keyboard.append([InlineKeyboardButton(label, callback_data=plain)])
        if archive_available:
            keyboard.append([
                InlineKeyboardButton(f"{label} jako 7z", callback_data=archive_cb)
            ])

    if total > len(entries) and len(entries) < MAX_PLAYLIST_ITEMS_EXPANDED:
        more_count = min(total, MAX_PLAYLIST_ITEMS_EXPANDED)
        keyboard.append([InlineKeyboardButton(
            f"Pokaż więcej (do {more_count})", callback_data="pl_more"
        )])

    keyboard.append([InlineKeyboardButton("Anuluj", callback_data="pl_cancel")])
    return msg, InlineKeyboardMarkup(keyboard)


def build_single_video_url(url: str) -> str:
    """Strip playlist parameters and return the standalone video URL."""

    return strip_playlist_params(url)


def parse_playlist_download_choice(callback_data: str) -> PlaylistDownloadChoice:
    """Parse playlist batch-download callback data.

    Recognizes two prefixes:
    - ``pl_dl_<media>_<format>``      → standard per-item send (legacy).
    - ``pl_zip_dl_<media>_<format>``  → archive (7z) flow.
    """

    if callback_data.startswith("pl_zip_dl_"):
        rest = callback_data.replace("pl_zip_dl_", "", 1)
        as_archive = True
    elif callback_data.startswith("pl_dl_"):
        rest = callback_data.replace("pl_dl_", "", 1)
        as_archive = False
    else:
        rest = callback_data
        as_archive = False

    parts = rest.split("_", 1)
    media_type = parts[0]
    format_choice = parts[1] if len(parts) > 1 else "best"
    return PlaylistDownloadChoice(
        media_type=media_type,
        format_choice=format_choice,
        as_archive=as_archive,
    )


def build_playlist_item_download_plan(
    *,
    chat_id: int,
    url: str,
    title: str,
    media_type: str,
    format_choice: str,
):
    """Build a reusable download plan for one playlist item."""

    chat_download_path = os.path.join(DOWNLOAD_PATH, str(chat_id))
    os.makedirs(chat_download_path, exist_ok=True)
    return prepare_download_plan(
        url=url,
        media_type=media_type,
        format_choice=format_choice,
        chat_download_path=chat_download_path,
    )


async def download_playlist_item(
    *,
    chat_id: int,
    url: str,
    title: str,
    media_type: str,
    format_choice: str,
    executor: Any,
) -> DownloadResult:
    """Download a single playlist item without Telegram-specific progress UI."""

    plan = build_playlist_item_download_plan(
        chat_id=chat_id,
        url=url,
        title=title,
        media_type=media_type,
        format_choice=format_choice,
    )
    if not plan:
        raise RuntimeError(f"Nie udało się pobrać informacji o pozycji playlisty: {title}")

    try:
        estimated_size_mb = estimate_download_size(plan)
    except Exception:
        estimated_size_mb = None

    if not ensure_size_within_limit(estimated_size_mb):
        raise RuntimeError(f"Plik za duży ({estimated_size_mb:.0f} MB, limit przekroczony)")

    return await execute_download(
        plan,
        chat_id=chat_id,
        executor=executor,
        progress_hook_factory=lambda _chat_id: (lambda _data: None),
        progress_state={},
        status_callback=_noop_status_update,
        format_bytes=lambda value: str(value),
        format_eta=lambda value: str(value),
    )


async def _noop_status_update(_: str) -> None:
    """Status callback placeholder for batch playlist downloads."""


def cleanup_downloaded_media(file_path: str) -> None:
    """Delete a temporary downloaded media file."""

    try:
        os.remove(file_path)
    except OSError:
        pass


def find_existing_playlist_item_file(plan) -> str | None:
    """Expose file detection for tests and handler compatibility."""

    return find_downloaded_file(plan)
