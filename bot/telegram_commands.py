"""
Telegram command compatibility module.

This module keeps the legacy public import surface stable while delegating
feature logic to the extracted handler modules.
"""

from __future__ import annotations

import subprocess

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.cleanup import cleanup_old_files, get_disk_usage
from bot.config import DOWNLOAD_PATH, get_download_stats, get_runtime_value
from bot.downloader_media import get_instagram_post_info, is_photo_entry
from bot.downloader_metadata import get_video_info
from bot.downloader_playlist import is_playlist_url, is_pure_playlist_url
from bot.handlers import command_access as _command_access_module
from bot.handlers import inbound_media as _inbound_media_module
from bot.handlers.command_access import (
    _get_authorized_user_ids as _extracted_get_authorized_user_ids,
    _get_history_stats as _extracted_get_history_stats,
    _is_admin as _extracted_is_admin,
    _is_authorized as _extracted_is_authorized,
    cleanup_command as _extracted_cleanup_command,
    handle_pin as _extracted_handle_pin,
    help_command as _extracted_help_command,
    history_command as _extracted_history_command,
    logout_command as _extracted_logout_command,
    notify_admin_pin_failure as _extracted_notify_admin_pin_failure,
    start as _extracted_start,
    status_command as _extracted_status_command,
    users_command as _extracted_users_command,
)
from bot.handlers.common_ui import (
    build_instagram_photo_keyboard,
    build_main_keyboard,
    escape_md as _shared_escape_md,
)
from bot.handlers.time_range import parse_time_range as _shared_parse_time_range
from bot.handlers.inbound_media import (
    _extract_audio_info as _extracted_extract_audio_info,
    _extract_video_info as _extracted_extract_video_info,
    extracted_process_audio_file as _extracted_process_audio_file,
    extracted_process_playlist_link as _extracted_process_playlist_link,
    extracted_process_spotify_episode as _extracted_process_spotify_episode,
    extracted_process_video_file as _extracted_process_video_file,
    extracted_process_youtube_link as _extracted_process_youtube_link,
    handle_audio_upload as _extracted_handle_audio_upload,
    handle_video_upload as _extracted_handle_video_upload,
    handle_youtube_link as _extracted_handle_youtube_link,
)
from bot.runtime import (
    add_authorized_user_for,
    get_app_runtime,
    get_authorized_user_ids_for,
    remove_authorized_user_for,
)
from bot.security_limits import (
    BLOCK_TIME,
    FFMPEG_TIMEOUT,
    MAX_ATTEMPTS,
    MAX_FILE_SIZE_MB,
    MAX_PLAYLIST_ITEMS,
    RATE_LIMIT_REQUESTS,
    RATE_LIMIT_WINDOW,
)
from bot.security_pin import get_block_remaining_seconds, is_user_blocked
from bot.security_policy import detect_platform, estimate_file_size, get_media_label, normalize_url, validate_url
from bot.security_throttling import check_rate_limit
from bot.session_store import block_until, failed_attempts, user_playlist_data, user_time_ranges, user_urls
validate_youtube_url = validate_url
from bot.services.playlist_service import build_playlist_message, load_playlist


def _build_main_keyboard(platform: str, large_file: bool = False) -> list:
    """Compatibility wrapper for the shared main keyboard builder."""

    return build_main_keyboard(platform, large_file=large_file)


def _build_instagram_photo_keyboard(photos: list, videos: list) -> list:
    """Compatibility wrapper for the shared Instagram keyboard builder."""

    return build_instagram_photo_keyboard(photos, videos)


def escape_md(text: str) -> str:
    """Compatibility wrapper for shared Markdown escaping."""

    return _shared_escape_md(text)


def parse_time_range(text: str) -> dict | None:
    """Compatibility wrapper for shared time-range parsing."""

    return _shared_parse_time_range(text)


def _resolve_authorized_user_ids(source) -> set[int]:
    """Resolve authorized users through runtime-aware helpers."""

    runtime = get_app_runtime(source)
    if runtime is not None:
        return runtime.authorized_users_set
    return get_authorized_user_ids_for(None)


def _sync_command_access_dependencies() -> None:
    """Keep extracted command handlers aligned with this module globals."""

    _command_access_module.DOWNLOAD_PATH = DOWNLOAD_PATH
    _command_access_module.get_runtime_value = get_runtime_value
    _command_access_module.get_authorized_user_ids_for = _resolve_authorized_user_ids
    _command_access_module.add_authorized_user_for = add_authorized_user_for
    _command_access_module.remove_authorized_user_for = remove_authorized_user_for
    _command_access_module.get_download_stats = get_download_stats
    _command_access_module.MAX_ATTEMPTS = MAX_ATTEMPTS
    _command_access_module.BLOCK_TIME = BLOCK_TIME
    _command_access_module.failed_attempts = failed_attempts
    _command_access_module.block_until = block_until
    _command_access_module.get_disk_usage = get_disk_usage
    _command_access_module.cleanup_old_files = cleanup_old_files
    _command_access_module.process_youtube_link = process_youtube_link
    _command_access_module.process_audio_file = process_audio_file
    _command_access_module.process_video_file = process_video_file


def _sync_inbound_media_dependencies() -> None:
    """Keep extracted inbound-media handlers aligned with this module globals."""

    _inbound_media_module.DOWNLOAD_PATH = DOWNLOAD_PATH
    _inbound_media_module.get_runtime_value = get_runtime_value
    _inbound_media_module.FFMPEG_TIMEOUT = FFMPEG_TIMEOUT
    _inbound_media_module.MAX_FILE_SIZE_MB = MAX_FILE_SIZE_MB
    _inbound_media_module.MAX_PLAYLIST_ITEMS = MAX_PLAYLIST_ITEMS
    _inbound_media_module.RATE_LIMIT_REQUESTS = RATE_LIMIT_REQUESTS
    _inbound_media_module.RATE_LIMIT_WINDOW = RATE_LIMIT_WINDOW
    _inbound_media_module.block_until = block_until
    _inbound_media_module.user_urls = user_urls
    _inbound_media_module.user_time_ranges = user_time_ranges
    _inbound_media_module.user_playlist_data = user_playlist_data
    _inbound_media_module.check_rate_limit = check_rate_limit
    _inbound_media_module.validate_url = validate_youtube_url
    _inbound_media_module.detect_platform = detect_platform
    _inbound_media_module.normalize_url = normalize_url
    _inbound_media_module.get_media_label = get_media_label
    _inbound_media_module.estimate_file_size = estimate_file_size
    _inbound_media_module.is_user_blocked = is_user_blocked
    _inbound_media_module.get_block_remaining_seconds = get_block_remaining_seconds
    _inbound_media_module.get_video_info = get_video_info
    _inbound_media_module.is_playlist_url = is_playlist_url
    _inbound_media_module.is_pure_playlist_url = is_pure_playlist_url
    _inbound_media_module.get_instagram_post_info = get_instagram_post_info
    _inbound_media_module.is_photo_entry = is_photo_entry
    _inbound_media_module.load_playlist = load_playlist
    _inbound_media_module.build_playlist_message = build_playlist_message
    _inbound_media_module.parse_time_range = parse_time_range
    _inbound_media_module.handle_pin = handle_pin
    _inbound_media_module._is_authorized = _is_authorized
    _inbound_media_module._build_main_keyboard = _build_main_keyboard
    _inbound_media_module._build_instagram_photo_keyboard = _build_instagram_photo_keyboard
    _inbound_media_module.process_youtube_link = process_youtube_link
    _inbound_media_module.process_playlist_link = process_playlist_link
    _inbound_media_module._process_spotify_episode = _process_spotify_episode
    _inbound_media_module.process_audio_file = process_audio_file
    _inbound_media_module.process_video_file = process_video_file
    _inbound_media_module.subprocess = subprocess


def _is_admin(user_id: int) -> bool:
    _sync_command_access_dependencies()
    return _extracted_is_admin(user_id)


def _get_authorized_user_ids(context: ContextTypes.DEFAULT_TYPE) -> set[int]:
    _sync_command_access_dependencies()
    return _extracted_get_authorized_user_ids(context)


def _is_authorized(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    _sync_command_access_dependencies()
    return _extracted_is_authorized(context, user_id)


def _get_history_stats(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> dict:
    _sync_command_access_dependencies()
    return _extracted_get_history_stats(context, user_id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _sync_command_access_dependencies()
    return await _extracted_start(update, context)


async def notify_admin_pin_failure(bot, user, attempt_count: int, blocked: bool):
    _sync_command_access_dependencies()
    return await _extracted_notify_admin_pin_failure(bot, user, attempt_count, blocked)


async def handle_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _sync_command_access_dependencies()
    return await _extracted_handle_pin(update, context)


async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _sync_command_access_dependencies()
    return await _extracted_logout_command(update, context)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _sync_command_access_dependencies()
    return await _extracted_help_command(update, context)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _sync_command_access_dependencies()
    return await _extracted_status_command(update, context)


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _sync_command_access_dependencies()
    return await _extracted_history_command(update, context)


async def cleanup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _sync_command_access_dependencies()
    return await _extracted_cleanup_command(update, context)


async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _sync_command_access_dependencies()
    return await _extracted_users_command(update, context)


async def handle_youtube_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _sync_inbound_media_dependencies()
    return await _extracted_handle_youtube_link(update, context)


def _build_playlist_message(playlist_info: dict, context=None) -> tuple[str, InlineKeyboardMarkup]:
    """Compatibility wrapper for playlist menu rendering.

    Resolves ``archive_available`` from the live runtime when ``context`` is
    provided, so callers don't need to import ``bot.runtime`` themselves.
    """
    runtime = get_app_runtime(context) if context is not None else None
    archive_available = runtime.archive_available if runtime is not None else False
    return build_playlist_message(playlist_info, archive_available=archive_available)


async def process_playlist_link(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    _sync_inbound_media_dependencies()
    return await _extracted_process_playlist_link(update, context, url)


async def _process_spotify_episode(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    _sync_inbound_media_dependencies()
    return await _extracted_process_spotify_episode(update, context, url)


async def process_youtube_link(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    _sync_inbound_media_dependencies()
    return await _extracted_process_youtube_link(update, context, url)


TELEGRAM_DOWNLOAD_LIMIT_MB = _inbound_media_module.TELEGRAM_DOWNLOAD_LIMIT_MB
MTPROTO_MAX_FILE_SIZE_MB = _inbound_media_module.MTPROTO_MAX_FILE_SIZE_MB


def _extract_audio_info(message) -> dict | None:
    _sync_inbound_media_dependencies()
    return _extracted_extract_audio_info(message)


async def handle_audio_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _sync_inbound_media_dependencies()
    return await _extracted_handle_audio_upload(update, context)


async def process_audio_file(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    audio_info: dict | None = None,
):
    """Compatibility wrapper preserving user-safe audio upload error messaging.

    User-facing fallback remains: "Błąd przetwarzania pliku audio. Spróbuj ponownie."
    """

    _sync_inbound_media_dependencies()
    return await _extracted_process_audio_file(update, context, audio_info)


def _extract_video_info(message) -> dict | None:
    _sync_inbound_media_dependencies()
    return _extracted_extract_video_info(message)


async def handle_video_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _sync_inbound_media_dependencies()
    return await _extracted_handle_video_upload(update, context)


async def process_video_file(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    video_info: dict | None = None,
):
    _sync_inbound_media_dependencies()
    return await _extracted_process_video_file(update, context, video_info)
