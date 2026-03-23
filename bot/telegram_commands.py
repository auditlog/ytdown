"""
Telegram commands module for YouTube Downloader Telegram Bot.

Contains command handlers (/start, /help, /status, /cleanup, /users)
and PIN authentication logic.
"""

import os
import logging
import subprocess

from telegram import Update, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from datetime import datetime

from bot.config import (
    DOWNLOAD_PATH,
    get_download_stats,
    get_runtime_value,
)
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
from bot.security import (
    MAX_ATTEMPTS,
    BLOCK_TIME,
    RATE_LIMIT_REQUESTS,
    RATE_LIMIT_WINDOW,
    MAX_FILE_SIZE_MB,
    MAX_PLAYLIST_ITEMS,
    FFMPEG_TIMEOUT,
    failed_attempts,
    block_until,
    user_urls,
    user_time_ranges,
    user_playlist_data,
    check_rate_limit,
    validate_url,
    validate_youtube_url,
    detect_platform,
    normalize_url,
    get_media_label,
    estimate_file_size,
    is_user_blocked,
    get_block_remaining_seconds,
)
from bot.cleanup import (
    cleanup_old_files,
    get_disk_usage,
)
from bot.downloader import (
    get_video_info,
    is_playlist_url,
    is_pure_playlist_url,
    get_instagram_post_info,
    is_photo_entry,
)
from bot.spotify import parse_spotify_episode_url
from bot.services.auth_service import (
    clear_auth_security_state,
    handle_pin_input,
    handle_start,
    logout_user,
    store_pending_action,
)
from bot.services.playlist_service import (
    build_playlist_message,
    load_playlist,
)
from bot.services.spotify_service import (
    build_episode_caption_data,
    get_resolution_error_message,
    resolve_episode,
)
from bot.session_context import (
    clear_transient_flow_state as _clear_transient_flow_state,
    clear_session_context_value as _clear_session_context_value,
    clear_session_value as _clear_session_value,
    get_auth_state as _get_auth_state,
    get_session_context_value as _get_session_context_value,
    get_session_value as _get_session_value,
    set_session_context_value as _set_session_context_value,
    set_session_value as _set_session_value,
)
from bot.runtime import (
    add_authorized_user_for,
    get_app_runtime,
    get_authorized_user_ids_for,
    remove_authorized_user_for,
)

def _build_main_keyboard(platform: str, large_file: bool = False) -> list:
    """Compatibility wrapper for the shared main keyboard builder."""

    return build_main_keyboard(platform, large_file=large_file)


def _build_instagram_photo_keyboard(photos: list, videos: list) -> list:
    """Compatibility wrapper for the shared Instagram keyboard builder."""

    return build_instagram_photo_keyboard(photos, videos)


def escape_md(text: str) -> str:
    """Compatibility wrapper for shared Markdown escaping."""

    return _shared_escape_md(text)


def get_runtime_authorized_users() -> set[int]:
    """Legacy compatibility shim for tests patching the old auth accessor."""

    return get_authorized_user_ids_for(None)


def _resolve_authorized_user_ids(source) -> set[int]:
    """Resolve authorized users through runtime-aware and legacy-compatible shims."""

    runtime = get_app_runtime(source)
    if runtime is not None:
        return runtime.authorized_users_set
    return get_runtime_authorized_users()


def _is_admin(user_id: int) -> bool:
    """Returns True if user_id matches ADMIN_CHAT_ID."""
    admin_chat_id = get_runtime_value("ADMIN_CHAT_ID", "")
    if not admin_chat_id:
        return True  # No admin configured — all authorized users are admin
    try:
        return user_id == int(admin_chat_id)
    except (ValueError, TypeError):
        return False


def _get_authorized_user_ids(context: ContextTypes.DEFAULT_TYPE) -> set[int]:
    """Return authorized users from runtime when available."""

    return get_authorized_user_ids_for(context)


def _is_authorized(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """Check user authorization against runtime-aware state."""

    return user_id in _get_authorized_user_ids(context)


def _get_history_stats(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> dict:
    """Read history stats from runtime when present, otherwise use legacy facade."""

    runtime = get_app_runtime(context)
    if runtime is not None:
        return runtime.download_history_repository.stats(user_id=user_id)
    return get_download_stats(user_id)


def parse_time_range(text: str) -> dict | None:
    """
    Parses time range input in formats like:
    - "0:30-5:45" (MM:SS-MM:SS)
    - "1:00:00-1:30:00" (HH:MM:SS-HH:MM:SS)
    - "30-5:45" (SS-MM:SS)
    
    Returns dict with start, end, start_sec, end_sec or None if invalid.
    """
    import re
    
    # Match pattern: time-time where time is either SS, MM:SS, or HH:MM:SS
    time_pattern = r'^(\d{1,2}(?::\d{2}){0,2})\s*-\s*(\d{1,2}(?::\d{2}){0,2})$'
    match = re.match(time_pattern, text.strip())
    
    if not match:
        return None
    
    def time_to_seconds(time_str: str) -> int:
        """Converts time string to seconds."""
        parts = time_str.split(':')
        if len(parts) == 1:
            return int(parts[0])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        return 0
    
    def format_time(seconds: int) -> str:
        """Formats seconds to MM:SS or HH:MM:SS."""
        if seconds >= 3600:
            return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"
        return f"{seconds // 60}:{seconds % 60:02d}"
    
    try:
        start_sec = time_to_seconds(match.group(1))
        end_sec = time_to_seconds(match.group(2))
        
        # Validate: start must be less than end
        if start_sec >= end_sec:
            return None
        
        return {
            'start': format_time(start_sec),
            'end': format_time(end_sec),
            'start_sec': start_sec,
            'end_sec': end_sec
        }
    except (ValueError, IndexError):
        return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /start command."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    user_name = update.effective_user.first_name
    result = handle_start(
        user_id=user_id,
        user_name=user_name,
        authorized_user_ids=_get_authorized_user_ids(context),
        user_data=_get_auth_state(context, chat_id),
        block_map=block_until,
    )
    await update.message.reply_text(result.message)


async def notify_admin_pin_failure(bot, user, attempt_count: int, blocked: bool):
    """
    Sends a Telegram notification to ADMIN_CHAT_ID about a failed PIN attempt.

    Non-blocking: any error is silently logged so the auth flow is never interrupted.
    """
    admin_chat_id = get_runtime_value("ADMIN_CHAT_ID", "")
    if not admin_chat_id:
        return

    try:
        admin_id = int(admin_chat_id)
    except (ValueError, TypeError):
        logging.warning("ADMIN_CHAT_ID is not a valid integer: %s", admin_chat_id)
        return

    try:
        emoji = "\U0001f6ab" if blocked else "\u26a0\ufe0f"  # 🚫 or ⚠️
        label = "[BLOCKED]" if blocked else "[Failed PIN attempt]"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        username = f"@{user.username}" if user.username else "n/a"
        text = (
            f"{emoji} {label}\n\n"
            f"User ID: {user.id}\n"
            f"Username: {username}\n"
            f"Name: {user.first_name or 'n/a'}\n"
            f"Language: {user.language_code or 'n/a'}\n"
            f"Attempts: {attempt_count}/{MAX_ATTEMPTS}\n"
            f"Time: {timestamp}"
        )

        await bot.send_message(chat_id=admin_id, text=text)
    except Exception as exc:
        logging.error("Failed to send admin PIN notification: %s", exc)


async def handle_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles PIN input from user."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    message_text = update.message.text
    result = handle_pin_input(
        user_id=user_id,
        message_text=message_text,
        user_data=_get_auth_state(context, chat_id),
        pin_code=get_runtime_value("PIN_CODE", ""),
        authorized_user_ids=_get_authorized_user_ids(context),
        attempts=failed_attempts,
        block_map=block_until,
        authorize_user=lambda auth_user_id: add_authorized_user_for(context, auth_user_id),
        max_attempts=MAX_ATTEMPTS,
        block_time=BLOCK_TIME,
    )

    if not result.handled:
        return False

    if result.notify_admin:
        await notify_admin_pin_failure(
            context.bot,
            update.effective_user,
            result.attempt_count,
            result.blocked,
        )

    if result.message:
        await update.message.reply_text(result.message)

    if result.delete_message:
        try:
            await update.message.delete()
        except Exception:
            pass

    pending_action = result.pending_action
    if pending_action:
        if pending_action.kind == "url":
            await process_youtube_link(update, context, pending_action.payload)
        elif pending_action.kind == "audio":
            await process_audio_file(update, context, pending_action.payload)
        elif pending_action.kind == "video":
            await process_video_file(update, context, pending_action.payload)

    return True


async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /logout command - removes user from authorized list."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    success = logout_user(
        user_id=user_id,
        chat_id=chat_id,
        authorized_user_ids=_get_authorized_user_ids(context),
        remove_authorized_user=lambda auth_user_id: remove_authorized_user_for(context, auth_user_id),
        user_data=_get_auth_state(context, chat_id),
        user_urls=user_urls,
        user_time_ranges=user_time_ranges,
        clear_security_state=lambda auth_user_id: clear_auth_security_state(
            user_id=auth_user_id,
            attempts=failed_attempts,
            block_map=block_until,
        ),
    )
    if not success:
        await update.message.reply_text("Nie jesteś zalogowany.")
        return

    _clear_transient_flow_state(
        context,
        chat_id,
        user_urls=user_urls,
        user_time_ranges=user_time_ranges,
        user_playlist_data=user_playlist_data,
    )

    await update.message.reply_text(
        "Wylogowano pomyślnie.\n\n"
        "Aby ponownie korzystać z bota, użyj /start i podaj PIN."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /help command."""
    await update.message.reply_text(
        "Jak korzystać z bota:\n\n"
        "📹 *Pobieranie video/audio:*\n"
        "1. Wyślij link z obsługiwanej platformy\n"
        "2. Wybierz format (video lub audio) i jakość\n"
        "3. Poczekaj na pobranie pliku\n\n"
        "🎤 *Transkrypcja plików audio/video:*\n"
        "1. Wyślij wiadomość głosową, plik audio lub video\n"
        "2. Wybierz: transkrypcja lub transkrypcja + podsumowanie\n"
        "3. Obsługiwane formaty audio: OGG, MP3, M4A, WAV, FLAC, OPUS\n"
        "4. Obsługiwane formaty video: MP4, MOV, MKV, AVI, WEBM\n\n"
        "🌐 *Obsługiwane platformy:*\n"
        "- YouTube (youtube.com, youtu.be)\n"
        "- Vimeo (vimeo.com)\n"
        "- TikTok (tiktok.com)\n"
        "- Instagram (instagram.com)\n"
        "- LinkedIn (linkedin.com)\n"
        "- Castbox (castbox.fm)\n"
        "- Spotify podcasty (open.spotify.com/episode)\n\n"
        "🔒 *Platformy wymagające logowania:*\n"
        "TikTok, Instagram i LinkedIn mogą wymagać pliku cookies.txt\n"
        "do pobierania treści z ograniczonym dostępem.\n\n"
        "Komendy administracyjne:\n"
        "- /status - sprawdź przestrzeń dyskową\n"
        "- /cleanup - usuń stare pliki (>24h)",
        parse_mode='Markdown'
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /status command - shows disk space status."""
    user_id = update.effective_user.id

    if not _is_authorized(context, user_id):
        await update.message.reply_text("Brak autoryzacji. Użyj /start aby się zalogować.")
        return

    used_gb, free_gb, total_gb, usage_percent = get_disk_usage()

    file_count = 0
    total_size_mb = 0

    try:
        for root, dirs, files in os.walk(DOWNLOAD_PATH):
            for file in files:
                file_count += 1
                file_path = os.path.join(root, file)
                total_size_mb += os.path.getsize(file_path) / (1024 * 1024)
    except:
        pass

    status_msg = (
        f"**Status systemu**\n\n"
        f"**Przestrzeń dyskowa:**\n"
        f"- Używane: {used_gb:.1f} GB / {total_gb:.1f} GB ({usage_percent:.1f}%)\n"
        f"- Wolne: {free_gb:.1f} GB\n\n"
        f"**Katalog downloads:**\n"
        f"- Plików: {file_count}\n"
        f"- Rozmiar: {total_size_mb:.1f} MB\n\n"
    )

    if free_gb < 10:
        status_msg += "**Uwaga:** Mało wolnej przestrzeni!\n"

    if free_gb < 5:
        status_msg += "**KRYTYCZNIE mało miejsca!**\n"

    # Show cookies.txt status
    from bot.downloader import COOKIES_FILE
    if os.path.exists(COOKIES_FILE):
        cookie_size = os.path.getsize(COOKIES_FILE)
        status_msg += f"\n**cookies.txt:** ✅ ({cookie_size} B)\n"
    else:
        status_msg += "\n**cookies.txt:** ❌ brak (TikTok/Instagram/LinkedIn mogą wymagać)\n"

    await update.message.reply_text(status_msg, parse_mode='Markdown')


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /history command - shows download history and statistics."""
    user_id = update.effective_user.id

    if not _is_authorized(context, user_id):
        await update.message.reply_text("Brak autoryzacji. Użyj /start aby się zalogować.")
        return

    # Get stats for this user
    stats = _get_history_stats(context, user_id)

    if stats['total_downloads'] == 0:
        await update.message.reply_text("Brak historii pobrań.")
        return

    # Format message
    msg = "📊 **Historia pobrań**\n\n"
    msg += f"**Twoje statystyki:**\n"
    msg += f"- Łączna liczba pobrań: {stats['total_downloads']}\n"
    msg += f"- Udane: {stats['success_count']} ✅  Nieudane: {stats['failure_count']} ❌\n"
    msg += f"- Łączny rozmiar: {stats['total_size_mb']:.1f} MB\n\n"

    # Format counts
    if stats['format_counts']:
        msg += "**Formaty:**\n"
        for fmt, count in sorted(stats['format_counts'].items(), key=lambda x: -x[1]):
            msg += f"- {fmt}: {count}\n"
        msg += "\n"

    # Recent downloads
    if stats['recent']:
        msg += "**Ostatnie pobrania:**\n"
        for record in stats['recent'][:5]:
            title = record.get('title', 'Nieznany')[:40]
            if len(record.get('title', '')) > 40:
                title += "..."
            timestamp = record.get('timestamp', '')[:10]  # Just date
            fmt = record.get('format', '?')
            size = record.get('file_size_mb', 0)
            status_icon = "✅" if record.get('status', 'success') == 'success' else "❌"
            time_range_str = ""
            if record.get('time_range'):
                time_range_str = f" ✂️{record['time_range']}"
            msg += f"- {status_icon} `{timestamp}` {title} ({fmt}, {size:.1f}MB){time_range_str}\n"

    await update.message.reply_text(msg, parse_mode='Markdown')


async def cleanup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /cleanup command - manually triggers file cleanup."""
    user_id = update.effective_user.id

    if not _is_authorized(context, user_id):
        await update.message.reply_text("Brak autoryzacji. Użyj /start aby się zalogować.")
        return

    await update.message.reply_text("Rozpoczynam czyszczenie starych plików...")

    deleted_count = cleanup_old_files(DOWNLOAD_PATH, max_age_hours=24)

    used_gb, free_gb, total_gb, usage_percent = get_disk_usage()

    if deleted_count > 0:
        await update.message.reply_text(
            f"Czyszczenie zakończone!\n\n"
            f"- Usunięto plików: {deleted_count}\n"
            f"- Wolna przestrzeń: {free_gb:.1f} GB"
        )
    else:
        await update.message.reply_text(
            "Brak plików do usunięcia.\n"
            "Wszystkie pliki są młodsze niż 24 godziny."
        )


async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /users command - user management (admin only)."""
    user_id = update.effective_user.id

    if not _is_authorized(context, user_id):
        await update.message.reply_text("Brak autoryzacji. Użyj /start aby się zalogować.")
        return

    if not _is_admin(user_id):
        await update.message.reply_text("Ta komenda jest dostępna tylko dla administratora.")
        return

    authorized_user_ids = _get_authorized_user_ids(context)
    user_count = len(authorized_user_ids)
    user_list = ', '.join(str(uid) for uid in sorted(authorized_user_ids))

    await update.message.reply_text(
        f"Autoryzowani użytkownicy\n\n"
        f"- Liczba: {user_count}\n"
        f"- Lista ID: {user_list if user_count <= 10 else str(user_count) + ' użytkowników'}\n"
        f"- Twoje ID: {user_id}"
    )


def _sync_command_access_dependencies() -> None:
    """Keep extracted command handlers aligned with this module globals."""

    _command_access_module.DOWNLOAD_PATH = DOWNLOAD_PATH
    _command_access_module.get_runtime_value = get_runtime_value
    _command_access_module.get_authorized_user_ids_for = _resolve_authorized_user_ids
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
    _inbound_media_module.validate_url = validate_url
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


def _build_playlist_message(playlist_info: dict) -> tuple[str, InlineKeyboardMarkup]:
    return build_playlist_message(playlist_info)


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


async def process_audio_file(update: Update, context: ContextTypes.DEFAULT_TYPE, audio_info: dict | None = None):
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


async def process_video_file(update: Update, context: ContextTypes.DEFAULT_TYPE, video_info: dict | None = None):
    _sync_inbound_media_dependencies()
    return await _extracted_process_video_file(update, context, video_info)
