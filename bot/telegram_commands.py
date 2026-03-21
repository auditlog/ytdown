"""
Telegram commands module for YouTube Downloader Telegram Bot.

Contains command handlers (/start, /help, /status, /cleanup, /users)
and PIN authentication logic.
"""

import os
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

from datetime import datetime

from bot.config import (
    DOWNLOAD_PATH,
    authorized_users,
    get_download_stats,
    get_runtime_value,
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
    manage_authorized_user,
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
from bot.runtime import get_app_runtime


def escape_md(text: str) -> str:
    """Escapes Markdown v1 special characters in text."""
    return escape_markdown(text, version=1)


def _build_main_keyboard(platform: str, large_file: bool = False) -> list:
    """Builds the main format selection keyboard, conditional on platform.

    Args:
        platform: Detected platform ('youtube', 'tiktok', etc.)
        large_file: If True, shows resolution options instead of "best quality"

    Returns:
        List of InlineKeyboardButton rows for InlineKeyboardMarkup.
    """
    is_podcast = platform in ('castbox', 'spotify')
    hide_flac = platform in ('tiktok', 'castbox', 'spotify')
    hide_time_range = platform in ('tiktok', 'castbox', 'spotify')

    if is_podcast:
        # Audio-only platform — no video options, no format list
        keyboard = [
            [InlineKeyboardButton("Audio (MP3)", callback_data="dl_audio_mp3")],
            [InlineKeyboardButton("Audio (M4A)", callback_data="dl_audio_m4a")],
            [InlineKeyboardButton("Transkrypcja audio", callback_data="transcribe")],
            [InlineKeyboardButton("Transkrypcja + Podsumowanie", callback_data="transcribe_summary")],
        ]
        return keyboard

    if large_file:
        keyboard = [
            [InlineKeyboardButton("Video 1080p (Full HD)", callback_data="dl_video_1080p")],
            [InlineKeyboardButton("Video 720p (HD)", callback_data="dl_video_720p")],
            [InlineKeyboardButton("Video 480p (SD)", callback_data="dl_video_480p")],
            [InlineKeyboardButton("Video 360p (Niska jakość)", callback_data="dl_video_360p")],
            [InlineKeyboardButton("Audio (MP3)", callback_data="dl_audio_mp3")],
            [InlineKeyboardButton("Audio (M4A)", callback_data="dl_audio_m4a")],
            [InlineKeyboardButton("Transkrypcja audio", callback_data="transcribe")],
            [InlineKeyboardButton("Transkrypcja + Podsumowanie", callback_data="transcribe_summary")],
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("Najlepsza jakość video", callback_data="dl_video_best")],
            [InlineKeyboardButton("Audio (MP3)", callback_data="dl_audio_mp3")],
            [InlineKeyboardButton("Audio (M4A)", callback_data="dl_audio_m4a")],
        ]
        if not hide_flac:
            keyboard.append([InlineKeyboardButton("Audio (FLAC)", callback_data="dl_audio_flac")])
        keyboard.extend([
            [InlineKeyboardButton("Transkrypcja audio", callback_data="transcribe")],
            [InlineKeyboardButton("Transkrypcja + Podsumowanie", callback_data="transcribe_summary")],
        ])

    if not hide_time_range:
        keyboard.append([InlineKeyboardButton("✂️ Zakres czasowy", callback_data="time_range")])
    keyboard.append([
        InlineKeyboardButton("Lista formatów", callback_data="formats"),
        InlineKeyboardButton("Miniaturka", callback_data="thumbnail"),
    ])

    return keyboard


def _build_instagram_photo_keyboard(photos: list, videos: list) -> list:
    """Builds keyboard for Instagram photo/carousel posts.

    Args:
        photos: List of photo entries from yt-dlp.
        videos: List of video entries from yt-dlp.

    Returns:
        List of InlineKeyboardButton rows.
    """
    keyboard = []

    if photos:
        label = f"Pobierz zdjęcia ({len(photos)})" if len(photos) > 1 else "Pobierz zdjęcie"
        keyboard.append([InlineKeyboardButton(label, callback_data="dl_ig_photos")])

    if videos:
        label = f"Pobierz filmy ({len(videos)})" if len(videos) > 1 else "Pobierz film"
        keyboard.append([InlineKeyboardButton(label, callback_data="dl_ig_videos")])

    if photos and videos:
        keyboard.append([InlineKeyboardButton("Pobierz wszystko", callback_data="dl_ig_all")])

    return keyboard


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

    runtime = get_app_runtime(context)
    if runtime is not None:
        return runtime.authorized_users_set
    return authorized_users


def _is_authorized(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """Check user authorization against runtime-aware state."""

    return user_id in _get_authorized_user_ids(context)


def _get_session_value(
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


def _set_session_value(
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


def _clear_session_value(
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


def _get_session_context_value(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    field_name: str,
    *,
    legacy_key: str,
    default=None,
):
    """Read one session-scoped context value from runtime or legacy user_data."""

    runtime = get_app_runtime(context)
    if runtime is not None:
        value = runtime.session_store.get_field(chat_id, field_name)
        if value is not None:
            return value
    return context.user_data.get(legacy_key, default)


def _set_session_context_value(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    field_name: str,
    value,
    *,
    legacy_key: str,
) -> None:
    """Write one session-scoped context value to runtime and legacy user_data."""

    runtime = get_app_runtime(context)
    if runtime is not None:
        runtime.session_store.set_field(chat_id, field_name, value)
    context.user_data[legacy_key] = value


def _clear_session_context_value(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    field_name: str,
    *,
    legacy_key: str,
) -> None:
    """Clear one session-scoped context value from runtime and legacy user_data."""

    runtime = get_app_runtime(context)
    if runtime is not None:
        runtime.session_store.pop_field(chat_id, field_name, None)
    context.user_data.pop(legacy_key, None)


def _clear_transient_flow_state(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
) -> None:
    """Clear temporary Telegram flow state from runtime and legacy user_data."""

    runtime = get_app_runtime(context)
    if runtime is not None:
        runtime.session_store.clear_fields(
            chat_id,
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

    for legacy_key in (
        "platform",
        "spotify_resolved",
        "ig_carousel",
        "audio_file_path",
        "audio_file_title",
        "subtitle_pending",
    ):
        context.user_data.pop(legacy_key, None)

    if runtime is None:
        user_urls.pop(chat_id, None)
        user_time_ranges.pop(chat_id, None)
        user_playlist_data.pop(chat_id, None)


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
    user_name = update.effective_user.first_name
    result = handle_start(
        user_id=user_id,
        user_name=user_name,
        authorized_user_ids=_get_authorized_user_ids(context),
        user_data=context.user_data,
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
    message_text = update.message.text
    result = handle_pin_input(
        user_id=user_id,
        message_text=message_text,
        user_data=context.user_data,
        pin_code=get_runtime_value("PIN_CODE", ""),
        authorized_user_ids=_get_authorized_user_ids(context),
        attempts=failed_attempts,
        block_map=block_until,
        authorize_user=lambda auth_user_id: manage_authorized_user(auth_user_id, 'add'),
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
        remove_authorized_user=lambda auth_user_id: manage_authorized_user(auth_user_id, 'remove'),
        user_data=context.user_data,
        user_urls=user_urls,
        user_time_ranges=user_time_ranges,
    )
    if not success:
        await update.message.reply_text("Nie jesteś zalogowany.")
        return

    _clear_transient_flow_state(context, chat_id)

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


async def handle_youtube_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles YouTube links and custom time range input."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    message_text = update.message.text

    # First check if message is handled as PIN
    pin_handled = await handle_pin(update, context)
    if pin_handled:
        return

    # Check if user is authorized
    if not _is_authorized(context, user_id):
        store_pending_action(context.user_data, kind="url", payload=message_text)

        await update.message.reply_text(
            "Wymagane uwierzytelnienie!\n\n"
            "Proszę podaj 8-cyfrowy kod PIN, aby uzyskać dostęp."
        )
        return

    # Check if user has an active URL session and message looks like a time range
    current_url = _get_session_value(context, chat_id, "current_url", user_urls)
    if current_url:
        time_range = parse_time_range(message_text)
        if time_range:
            # Get video info to validate time range against duration
            info = get_video_info(current_url)
            if info:
                duration = int(info.get('duration') or 0)
                title = info.get('title', 'Nieznany tytuł')
                duration_str = f"{duration // 60}:{duration % 60:02d}" if duration else "?"
                
                # Validate end time doesn't exceed duration
                if duration and time_range['end_sec'] > duration:
                    await update.message.reply_text(
                        f"❌ Nieprawidłowy zakres!\n\n"
                        f"Czas końcowy ({time_range['end']}) przekracza czas trwania filmu ({duration_str})."
                    )
                    return
                
                # Apply the custom time range
                _set_session_value(context, chat_id, "time_range", time_range, user_time_ranges)

                # Send confirmation and show main menu with updated time range
                cur_platform = _get_session_context_value(
                    context,
                    chat_id,
                    "platform",
                    legacy_key="platform",
                    default="youtube",
                )
                keyboard = _build_main_keyboard(cur_platform)
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.message.reply_text(
                    f"✅ Ustawiono zakres: {time_range['start']} - {time_range['end']}\n\n"
                    f"*{escape_md(title)}*\nCzas trwania: {duration_str}\n"
                    f"✂️ Zakres: {time_range['start']} - {time_range['end']}\n\n"
                    f"Wybierz format do pobrania:",
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
                return

    # Check if user is blocked (before any further processing)
    if is_user_blocked(user_id, block_map=block_until):
        remaining_time = get_block_remaining_seconds(user_id, block_map=block_until)
        minutes = remaining_time // 60
        seconds = remaining_time % 60

        await update.message.reply_text(
            f"Dostęp zablokowany z powodu zbyt wielu nieudanych prób. "
            f"Spróbuj ponownie za {minutes} min {seconds} s."
        )
        return

    # Check rate limit
    if not check_rate_limit(user_id):
        await update.message.reply_text(
            "Przekroczono limit requestów!\n\n"
            f"Możesz wysłać maksymalnie {RATE_LIMIT_REQUESTS} requestów "
            f"w ciągu {RATE_LIMIT_WINDOW} sekund.\n"
            "Spróbuj ponownie za chwilę."
        )
        return

    # Only Castbox links require redirect normalization that may hit the network.
    if "castbox.fm" in message_text:
        import asyncio
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            message_text = await loop.run_in_executor(executor, normalize_url, message_text)

    # Validate URL
    if not validate_url(message_text):
        await update.message.reply_text(
            "Nieprawidłowy URL!\n\n"
            "Obsługiwane platformy:\n"
            "- YouTube (youtube.com, youtu.be)\n"
            "- Vimeo (vimeo.com)\n"
            "- TikTok (tiktok.com)\n"
            "- Instagram (instagram.com)\n"
            "- LinkedIn (linkedin.com)\n"
            "- Castbox (castbox.fm)\n"
            "- Spotify podcasty (open.spotify.com/episode)"
        )
        return

    await process_youtube_link(update, context, message_text)


def _build_playlist_message(playlist_info: dict) -> tuple[str, InlineKeyboardMarkup]:
    """Compatibility wrapper around the playlist service message builder."""

    return build_playlist_message(playlist_info)


async def process_playlist_link(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    """Handles playlist URL — fetches info and shows playlist menu."""
    chat_id = update.effective_chat.id

    progress_message = await update.message.reply_text(
        "Wykryto playlistę! Pobieranie informacji..."
    )

    playlist_info = load_playlist(url, max_items=MAX_PLAYLIST_ITEMS)

    if not playlist_info:
        await progress_message.edit_text(
            "Nie udało się pobrać informacji o playliście."
        )
        return

    if not playlist_info['entries']:
        await progress_message.edit_text("Playlista jest pusta.")
        return

    # Store playlist data in session
    _set_session_value(context, chat_id, "playlist_data", playlist_info, user_playlist_data)
    _set_session_value(context, chat_id, "current_url", url, user_urls)

    msg, reply_markup = _build_playlist_message(playlist_info)
    await progress_message.edit_text(msg, reply_markup=reply_markup, parse_mode='Markdown')


async def _process_spotify_episode(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    """Resolves a Spotify episode URL and shows download options."""
    chat_id = update.effective_chat.id
    progress_message = await update.message.reply_text(
        "Spotify: wyszukiwanie odcinka podcastu..."
    )

    resolved = await resolve_episode(url)
    error_message = get_resolution_error_message(resolved)
    if error_message:
        await progress_message.edit_text(error_message)
        return

    # Store resolved info for callback handlers
    _set_session_context_value(
        context, chat_id, "spotify_resolved", resolved,
        legacy_key="spotify_resolved",
    )
    _set_session_value(context, chat_id, "current_url", url, user_urls)

    caption_data = build_episode_caption_data(resolved)
    title = caption_data['title']
    show_name = caption_data['show_name']
    duration_str = caption_data['duration_str']
    source_label = caption_data['source_label']

    show_info = f"\nPodcast: {escape_md(show_name)}" if show_name else ""

    keyboard = _build_main_keyboard('spotify')
    reply_markup = InlineKeyboardMarkup(keyboard)

    await progress_message.edit_text(
        f"*{escape_md(title)}*{show_info}\n"
        f"Czas trwania: {duration_str}\n"
        f"Źródło audio: {source_label}\n\n"
        f"Wybierz opcję:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def process_youtube_link(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    """Processes a media link after PIN authorization."""
    chat_id = update.effective_chat.id
    # Only Castbox links require redirect normalization that may hit the network.
    if "castbox.fm" in url:
        import asyncio
        with ThreadPoolExecutor(max_workers=1) as executor:
            url = await asyncio.get_event_loop().run_in_executor(executor, normalize_url, url)
    _set_session_value(context, chat_id, "current_url", url, user_urls)
    # Clear any previous time range
    _clear_session_value(context, chat_id, "time_range", user_time_ranges)

    # Detect and store platform for conditional UI
    platform = detect_platform(url) or 'youtube'
    _set_session_context_value(
        context, chat_id, "platform", platform,
        legacy_key="platform",
    )
    _clear_session_context_value(context, chat_id, "spotify_resolved", legacy_key="spotify_resolved")
    _clear_session_context_value(context, chat_id, "instagram_carousel", legacy_key="ig_carousel")
    _clear_session_context_value(context, chat_id, "subtitle_pending", legacy_key="subtitle_pending")

    # Castbox: channel URLs are not supported, only episode URLs
    if platform == 'castbox' and '/channel/' in url:
        await update.message.reply_text(
            "Castbox: link do kanału nie jest obsługiwany.\n\n"
            "Wyślij link do konkretnego odcinka podcastu\n"
            "(np. castbox.fm/episode/...)."
        )
        return

    # Spotify: only episode URLs, not show/playlist/track
    if platform == 'spotify':
        if not parse_spotify_episode_url(url):
            await update.message.reply_text(
                "Spotify: obsługiwane są tylko linki do odcinków podcastów.\n\n"
                "Wyślij link w formacie:\n"
                "open.spotify.com/episode/..."
            )
            return
        await _process_spotify_episode(update, context, url)
        return

    # Instagram: detect photo/carousel posts before standard video flow
    if platform == 'instagram':
        progress_message = await update.message.reply_text("Pobieranie informacji o poście...")
        import asyncio
        ig_info = await asyncio.get_event_loop().run_in_executor(
            None, get_instagram_post_info, url
        )
        if ig_info:
            # Carousel post — multiple entries (photos and/or videos)
            if ig_info.get('_type') == 'playlist' and ig_info.get('entries'):
                entries = [e for e in ig_info.get('entries', []) if e]
                photos = [e for e in entries if is_photo_entry(e)]
                videos = [e for e in entries if not is_photo_entry(e)]

                if photos:
                    carousel_state = {
                        'photos': photos,
                        'videos': videos,
                        'title': ig_info.get('title', 'Instagram post'),
                    }
                    _set_session_context_value(
                        context, chat_id, "instagram_carousel", carousel_state,
                        legacy_key="ig_carousel",
                    )
                    keyboard = _build_instagram_photo_keyboard(photos, videos)
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    title = escape_md(ig_info.get('title', 'Instagram post'))
                    parts = []
                    if photos:
                        parts.append(f"{len(photos)} zdjęć" if len(photos) > 1 else "1 zdjęcie")
                    if videos:
                        parts.append(f"{len(videos)} filmów" if len(videos) > 1 else "1 film")
                    await progress_message.edit_text(
                        f"*{title}*\nKaruzela: {', '.join(parts)}\n\nWybierz co pobrać:",
                        reply_markup=reply_markup,
                        parse_mode='Markdown'
                    )
                    return
                # Carousel with only videos — fall through to normal flow

            # Single photo post
            elif is_photo_entry(ig_info):
                carousel_state = {
                    'photos': [ig_info],
                    'videos': [],
                    'title': ig_info.get('title', 'Instagram photo'),
                }
                _set_session_context_value(
                    context, chat_id, "instagram_carousel", carousel_state,
                    legacy_key="ig_carousel",
                )
                keyboard = [[InlineKeyboardButton("Pobierz zdjęcie", callback_data="dl_ig_photos")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                title = escape_md(ig_info.get('title', 'Instagram photo'))
                await progress_message.edit_text(
                    f"*{title}*\nTyp: zdjęcie\n\nWybierz opcję:",
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
                return

        # Video post or failed info — fall through to standard flow
        # Delete progress message to avoid duplicate
        await progress_message.delete()

    # Playlist detection — offer choice or go straight to playlist view
    if is_playlist_url(url):
        if is_pure_playlist_url(url):
            await process_playlist_link(update, context, url)
            return
        else:
            # URL has both video and playlist — let user choose
            keyboard = [
                [InlineKeyboardButton("Pojedynczy film", callback_data="pl_single")],
                [InlineKeyboardButton("Cała playlista", callback_data="pl_full")],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                "Ten link zawiera zarówno film jak i playlistę.\n\n"
                "Co chcesz pobrać?",
                reply_markup=reply_markup
            )
            return

    media_name = get_media_label(platform)
    progress_message = await update.message.reply_text(f"Pobieranie informacji o {media_name}...")

    info = get_video_info(url)
    if not info:
        await progress_message.edit_text(f"Wystąpił błąd podczas pobierania informacji o {media_name}.")
        return

    title = info.get('title', 'Nieznany tytuł')
    duration = int(info.get('duration') or 0)
    duration_str = f"{duration // 60}:{duration % 60:02d}" if duration else "?"

    estimated_size = estimate_file_size(info)
    size_warning = ""

    if estimated_size and estimated_size > MAX_FILE_SIZE_MB:
        size_warning = f"\n*Uwaga:* Szacowany rozmiar najlepszej jakości: {estimated_size:.1f} MB (limit: {MAX_FILE_SIZE_MB} MB)\n"
        keyboard = _build_main_keyboard(platform, large_file=True)
    else:
        keyboard = _build_main_keyboard(platform)

    reply_markup = InlineKeyboardMarkup(keyboard)

    # Show time range info if set
    time_range = _get_session_value(context, chat_id, "time_range", user_time_ranges)
    time_range_info = ""
    if time_range:
        time_range_info = f"\n✂️ Zakres: {time_range['start']} - {time_range['end']}"

    await progress_message.edit_text(
        f"*{escape_md(title)}*\nCzas trwania: {duration_str}{size_warning}{time_range_info}\n\nWybierz format do pobrania:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


# Telegram Bot API download limit (bots can download files up to 20MB via getFile)
TELEGRAM_DOWNLOAD_LIMIT_MB = 20

# Maximum file size for MTProto downloads (reasonable limit for transcription)
MTPROTO_MAX_FILE_SIZE_MB = 200


def _extract_audio_info(message) -> dict | None:
    """
    Extracts audio file metadata from a Telegram message.

    Handles voice messages, audio files, and documents with audio MIME types
    (e.g. WhatsApp forwarded voice notes).
    """
    if message.voice:
        voice = message.voice
        return {
            'file_id': voice.file_id,
            'file_size': voice.file_size,
            'duration': voice.duration,
            'mime_type': voice.mime_type or 'audio/ogg',
            'title': 'Wiadomość głosowa',
        }

    if message.audio:
        audio = message.audio
        return {
            'file_id': audio.file_id,
            'file_size': audio.file_size,
            'duration': audio.duration,
            'mime_type': audio.mime_type or 'audio/mpeg',
            'title': audio.title or audio.file_name or 'Plik audio',
        }

    if message.document:
        doc = message.document
        mime = doc.mime_type or ''
        if mime.startswith('audio/'):
            return {
                'file_id': doc.file_id,
                'file_size': doc.file_size,
                'duration': None,
                'mime_type': mime,
                'title': doc.file_name or 'Dokument audio',
            }

    return None


async def handle_audio_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles voice messages, audio files, and audio documents."""
    user_id = update.effective_user.id
    message = update.message

    audio_info = _extract_audio_info(message)
    if not audio_info:
        return

    # PIN authentication — same pattern as handle_youtube_link
    pin_handled = await handle_pin(update, context)
    if pin_handled:
        return

    if not _is_authorized(context, user_id):
        store_pending_action(context.user_data, kind="audio", payload=audio_info)
        await message.reply_text(
            "Wymagane uwierzytelnienie!\n\n"
            "Proszę podaj 8-cyfrowy kod PIN, aby uzyskać dostęp."
        )
        return

    # Rate limiting
    if not check_rate_limit(user_id):
        await message.reply_text(
            "Przekroczono limit requestów!\n\n"
            f"Możesz wysłać maksymalnie {RATE_LIMIT_REQUESTS} requestów "
            f"w ciągu {RATE_LIMIT_WINDOW} sekund.\n"
            "Spróbuj ponownie za chwilę."
        )
        return

    await process_audio_file(update, context, audio_info)


async def process_audio_file(update: Update, context: ContextTypes.DEFAULT_TYPE, audio_info: dict | None = None):
    """
    Downloads an uploaded audio file from Telegram, converts to MP3 if needed,
    and shows transcription options.
    """
    chat_id = update.effective_chat.id
    message = update.message

    if not audio_info:
        audio_info = _extract_audio_info(message)
    if not audio_info:
        await message.reply_text("Nie rozpoznano pliku audio.")
        return

    file_size = audio_info.get('file_size') or 0
    file_size_mb = file_size / (1024 * 1024) if file_size else 0
    use_mtproto = file_size_mb > TELEGRAM_DOWNLOAD_LIMIT_MB

    if use_mtproto:
        from bot.mtproto import is_mtproto_available
        if not is_mtproto_available():
            await message.reply_text(
                f"Plik jest za duży do pobrania przez Telegram Bot API.\n\n"
                f"Rozmiar: {file_size_mb:.1f} MB\n"
                f"Limit: {TELEGRAM_DOWNLOAD_LIMIT_MB} MB\n\n"
                f"Aby pobierać większe pliki, skonfiguruj TELEGRAM_API_ID "
                f"i TELEGRAM_API_HASH (z my.telegram.org) oraz zainstaluj pyrogram."
            )
            return
        if file_size_mb > MTPROTO_MAX_FILE_SIZE_MB:
            await message.reply_text(
                f"Plik jest zbyt duży.\n\n"
                f"Rozmiar: {file_size_mb:.1f} MB\n"
                f"Limit: {MTPROTO_MAX_FILE_SIZE_MB} MB"
            )
            return

    progress_msg = await message.reply_text(
        f"Pobieranie pliku audio ({file_size_mb:.1f} MB)..."
        + (" (MTProto)" if use_mtproto else "")
    )

    chat_download_path = os.path.join(DOWNLOAD_PATH, str(chat_id))
    os.makedirs(chat_download_path, exist_ok=True)

    try:
        # Determine extension from MIME type
        mime_to_ext = {
            'audio/ogg': '.ogg',
            'audio/opus': '.opus',
            'audio/mpeg': '.mp3',
            'audio/mp4': '.m4a',
            'audio/x-m4a': '.m4a',
            'audio/wav': '.wav',
            'audio/x-wav': '.wav',
            'audio/flac': '.flac',
            'audio/webm': '.webm',
            'audio/aac': '.aac',
            'audio/amr': '.amr',
            'audio/x-caf': '.caf',
        }
        ext = mime_to_ext.get(audio_info['mime_type'], '.ogg')
        title = audio_info['title']

        # Sanitize title for filename
        safe_title = "".join(c if c.isalnum() or c in ' -_' else '_' for c in title)[:80]
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        raw_path = os.path.join(chat_download_path, f"{timestamp}_{safe_title}{ext}")

        if use_mtproto:
            from bot.mtproto import download_file_mtproto
            success = await download_file_mtproto(
                bot_token=get_runtime_value("TELEGRAM_BOT_TOKEN", ""),
                chat_id=chat_id,
                message_id=message.message_id,
                dest_path=raw_path,
            )
            if not success:
                await progress_msg.edit_text("Błąd pobierania pliku przez MTProto.")
                return
        else:
            tg_file = await context.bot.get_file(audio_info['file_id'])
            await tg_file.download_to_drive(raw_path)

        # Convert to MP3 if not already
        if ext == '.mp3':
            mp3_path = raw_path
        else:
            mp3_path = os.path.splitext(raw_path)[0] + '.mp3'
            await progress_msg.edit_text("Konwersja do MP3...")
            result = subprocess.run(
                ['ffmpeg', '-i', raw_path, '-vn', '-acodec', 'libmp3lame', '-q:a', '2', mp3_path],
                capture_output=True, timeout=FFMPEG_TIMEOUT
            )
            if result.returncode != 0:
                logging.error(f"ffmpeg conversion failed: {result.stderr.decode()}")
                await progress_msg.edit_text("Błąd konwersji pliku audio.")
                return
            # Remove original after successful conversion
            os.remove(raw_path)

        mp3_size_mb = os.path.getsize(mp3_path) / (1024 * 1024)

        # Store info for callback handlers
        _set_session_context_value(
            context, chat_id, "audio_file_path", mp3_path,
            legacy_key="audio_file_path",
        )
        _set_session_context_value(
            context, chat_id, "audio_file_title", title,
            legacy_key="audio_file_title",
        )

        duration_info = ""
        if audio_info.get('duration'):
            mins = audio_info['duration'] // 60
            secs = audio_info['duration'] % 60
            duration_info = f"\nCzas trwania: {mins}:{secs:02d}"

        keyboard = [
            [InlineKeyboardButton("Transkrypcja", callback_data="audio_transcribe")],
            [InlineKeyboardButton("Transkrypcja + Podsumowanie", callback_data="audio_transcribe_summary")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await progress_msg.edit_text(
            f"*{escape_md(title)}*{duration_info}\n"
            f"Rozmiar: {mp3_size_mb:.1f} MB\n\n"
            f"Wybierz opcję:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    except Exception as e:
        logging.error(f"Error processing audio upload: {e}")
        await progress_msg.edit_text("Błąd przetwarzania pliku audio. Spróbuj ponownie.")


def _extract_video_info(message) -> dict | None:
    """
    Extracts video file metadata from a Telegram message.

    Handles native video messages and documents with video MIME types.
    """
    video_mime_to_ext = {
        'video/mp4': '.mp4',
        'video/quicktime': '.mov',
        'video/x-matroska': '.mkv',
        'video/x-msvideo': '.avi',
        'video/webm': '.webm',
    }

    if message.video:
        vid = message.video
        mime = vid.mime_type or 'video/mp4'
        return {
            'file_id': vid.file_id,
            'file_size': vid.file_size,
            'duration': vid.duration,
            'mime_type': mime,
            'title': vid.file_name or 'Video',
            'ext': video_mime_to_ext.get(mime, '.mp4'),
        }

    if message.document:
        doc = message.document
        mime = doc.mime_type or ''
        if mime in video_mime_to_ext:
            return {
                'file_id': doc.file_id,
                'file_size': doc.file_size,
                'duration': None,
                'mime_type': mime,
                'title': doc.file_name or 'Video',
                'ext': video_mime_to_ext[mime],
            }

    return None


async def handle_video_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles video file uploads — extracts audio and offers transcription."""
    user_id = update.effective_user.id
    message = update.message

    video_info = _extract_video_info(message)
    if not video_info:
        return

    # PIN authentication — same pattern as handle_audio_upload
    pin_handled = await handle_pin(update, context)
    if pin_handled:
        return

    if not _is_authorized(context, user_id):
        store_pending_action(context.user_data, kind="video", payload=video_info)
        await message.reply_text(
            "Wymagane uwierzytelnienie!\n\n"
            "Proszę podaj 8-cyfrowy kod PIN, aby uzyskać dostęp."
        )
        return

    # Rate limiting
    if not check_rate_limit(user_id):
        await message.reply_text(
            "Przekroczono limit requestów!\n\n"
            f"Możesz wysłać maksymalnie {RATE_LIMIT_REQUESTS} requestów "
            f"w ciągu {RATE_LIMIT_WINDOW} sekund.\n"
            "Spróbuj ponownie za chwilę."
        )
        return

    await process_video_file(update, context, video_info)


async def process_video_file(update: Update, context: ContextTypes.DEFAULT_TYPE, video_info: dict | None = None):
    """
    Downloads a video file from Telegram, extracts audio via ffmpeg,
    and shows transcription options.
    """
    chat_id = update.effective_chat.id
    message = update.message

    if not video_info:
        video_info = _extract_video_info(message)
    if not video_info:
        await message.reply_text("Nie rozpoznano pliku video.")
        return

    file_size = video_info.get('file_size') or 0
    file_size_mb = file_size / (1024 * 1024) if file_size else 0
    use_mtproto = file_size_mb > TELEGRAM_DOWNLOAD_LIMIT_MB

    if use_mtproto:
        from bot.mtproto import is_mtproto_available
        if not is_mtproto_available():
            await message.reply_text(
                f"Plik jest za duży do pobrania przez Telegram Bot API.\n\n"
                f"Rozmiar: {file_size_mb:.1f} MB\n"
                f"Limit: {TELEGRAM_DOWNLOAD_LIMIT_MB} MB\n\n"
                f"Aby pobierać większe pliki, skonfiguruj TELEGRAM_API_ID "
                f"i TELEGRAM_API_HASH (z my.telegram.org) oraz zainstaluj pyrogram."
            )
            return
        if file_size_mb > MTPROTO_MAX_FILE_SIZE_MB:
            await message.reply_text(
                f"Plik jest zbyt duży.\n\n"
                f"Rozmiar: {file_size_mb:.1f} MB\n"
                f"Limit: {MTPROTO_MAX_FILE_SIZE_MB} MB"
            )
            return

    progress_msg = await message.reply_text(
        f"Pobieranie pliku video ({file_size_mb:.1f} MB)..."
        + (" (MTProto)" if use_mtproto else "")
    )

    chat_download_path = os.path.join(DOWNLOAD_PATH, str(chat_id))
    os.makedirs(chat_download_path, exist_ok=True)

    try:
        title = video_info['title']
        ext = video_info['ext']

        # Sanitize title for filename
        safe_title = "".join(c if c.isalnum() or c in ' -_' else '_' for c in title)[:80]
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        video_path = os.path.join(chat_download_path, f"{timestamp}_{safe_title}{ext}")

        if use_mtproto:
            from bot.mtproto import download_file_mtproto
            success = await download_file_mtproto(
                bot_token=get_runtime_value("TELEGRAM_BOT_TOKEN", ""),
                chat_id=chat_id,
                message_id=message.message_id,
                dest_path=video_path,
            )
            if not success:
                await progress_msg.edit_text("Błąd pobierania pliku przez MTProto.")
                return
        else:
            tg_file = await context.bot.get_file(video_info['file_id'])
            await tg_file.download_to_drive(video_path)

        # Extract audio from video
        await progress_msg.edit_text("Ekstrakcja audio z video...")
        mp3_path = os.path.splitext(video_path)[0] + '.mp3'
        result = subprocess.run(
            ['ffmpeg', '-i', video_path, '-vn', '-acodec', 'libmp3lame', '-q:a', '2', mp3_path],
            capture_output=True, timeout=FFMPEG_TIMEOUT
        )
        if result.returncode != 0:
            logging.error(f"ffmpeg video audio extraction failed: {result.stderr.decode()}")
            await progress_msg.edit_text("Błąd ekstrakcji audio z pliku video.")
            return

        # Remove original video after successful extraction
        os.remove(video_path)

        mp3_size_mb = os.path.getsize(mp3_path) / (1024 * 1024)

        # Store info for callback handlers
        _set_session_context_value(
            context, chat_id, "audio_file_path", mp3_path,
            legacy_key="audio_file_path",
        )
        _set_session_context_value(
            context, chat_id, "audio_file_title", title,
            legacy_key="audio_file_title",
        )

        duration_info = ""
        if video_info.get('duration'):
            mins = video_info['duration'] // 60
            secs = video_info['duration'] % 60
            duration_info = f"\nCzas trwania: {mins}:{secs:02d}"

        keyboard = [
            [InlineKeyboardButton("Transkrypcja", callback_data="audio_transcribe")],
            [InlineKeyboardButton("Transkrypcja + Podsumowanie", callback_data="audio_transcribe_summary")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await progress_msg.edit_text(
            f"*{escape_md(title)}*{duration_info}\n"
            f"Rozmiar audio: {mp3_size_mb:.1f} MB\n\n"
            f"Wybierz opcję:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    except Exception as e:
        logging.error(f"Error processing video upload: {e}")
        await progress_msg.edit_text("Błąd przetwarzania pliku video. Spróbuj ponownie.")
