"""
Telegram commands module for YouTube Downloader Telegram Bot.

Contains command handlers (/start, /help, /status, /cleanup, /users)
and PIN authentication logic.
"""

import os
import logging
import subprocess

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

from datetime import datetime

from bot.config import (
    ADMIN_CHAT_ID,
    DOWNLOAD_PATH,
    PIN_CODE,
    authorized_users,
    get_download_stats,
)
from bot.security import (
    MAX_ATTEMPTS,
    BLOCK_TIME,
    RATE_LIMIT_REQUESTS,
    RATE_LIMIT_WINDOW,
    MAX_FILE_SIZE_MB,
    MAX_PLAYLIST_ITEMS,
    MAX_PLAYLIST_ITEMS_EXPANDED,
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
    manage_authorized_user,
    estimate_file_size,
    is_user_blocked,
    get_block_remaining_seconds,
    clear_failed_attempts,
    register_pin_failure,
)
from bot.cleanup import (
    cleanup_old_files,
    get_disk_usage,
)
from bot.downloader import get_video_info, is_playlist_url, is_pure_playlist_url, get_playlist_info, strip_playlist_params


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
    is_podcast = platform == 'castbox'
    hide_flac = platform in ('tiktok', 'castbox')
    hide_time_range = platform in ('tiktok', 'castbox')

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
    keyboard.append([InlineKeyboardButton("Lista formatów", callback_data="formats")])

    return keyboard


def _is_admin(user_id: int) -> bool:
    """Returns True if user_id matches ADMIN_CHAT_ID."""
    if not ADMIN_CHAT_ID:
        return True  # No admin configured — all authorized users are admin
    try:
        return user_id == int(ADMIN_CHAT_ID)
    except (ValueError, TypeError):
        return False


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

    # Check if user is blocked
    if is_user_blocked(user_id, block_map=block_until):
        remaining_time = get_block_remaining_seconds(user_id, block_map=block_until)
        minutes = remaining_time // 60
        seconds = remaining_time % 60

        await update.message.reply_text(
            f"Witaj, {user_name}!\n\n"
            f"Dostęp zablokowany z powodu zbyt wielu nieudanych prób. "
            f"Spróbuj ponownie za {minutes} min {seconds} s."
        )
        return

    # Check if user is already authorized
    if user_id in authorized_users:
        await update.message.reply_text(
            f"Witaj, {user_name}!\n\n"
            "Jesteś już zalogowany. Wyślij link (YouTube, Vimeo, TikTok, Instagram, LinkedIn, Castbox) "
            "aby pobrać film lub audio."
        )
        return

    # If user is not authorized, ask for PIN
    await update.message.reply_text(
        f"Witaj, {user_name}!\n\n"
        "To jest bot chroniony PIN-em.\n"
        "Aby korzystać z bota, podaj 8-cyfrowy kod PIN."
    )

    context.user_data["awaiting_pin"] = True


async def notify_admin_pin_failure(bot, user, attempt_count: int, blocked: bool):
    """
    Sends a Telegram notification to ADMIN_CHAT_ID about a failed PIN attempt.

    Non-blocking: any error is silently logged so the auth flow is never interrupted.
    """
    if not ADMIN_CHAT_ID:
        return

    try:
        admin_id = int(ADMIN_CHAT_ID)
    except (ValueError, TypeError):
        logging.warning("ADMIN_CHAT_ID is not a valid integer: %s", ADMIN_CHAT_ID)
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

    # Check if user is blocked
    if is_user_blocked(user_id, block_map=block_until):
        remaining_time = get_block_remaining_seconds(user_id, block_map=block_until)
        minutes = remaining_time // 60
        seconds = remaining_time % 60

        await update.message.reply_text(
            f"Dostęp zablokowany z powodu zbyt wielu nieudanych prób. "
            f"Spróbuj ponownie za {minutes} min {seconds} s."
        )

        try:
            await update.message.delete()
        except Exception:
            pass

        return True

    # Check if waiting for PIN from this user
    if context.user_data.get("awaiting_pin", False) or not (user_id in authorized_users):
        # Check if message looks like a PIN attempt (digits only)
        if message_text and message_text.isdigit():
            if message_text == PIN_CODE:
                # Reset failed attempts counter
                clear_failed_attempts(user_id, attempts=failed_attempts)

                # Add user to authorized list
                manage_authorized_user(user_id, 'add')

                # Remove awaiting PIN state
                context.user_data.pop("awaiting_pin", None)

                await update.message.reply_text(
                    "PIN poprawny! Możesz teraz korzystać z bota.\n\n"
                    "Wyślij link (YouTube, Vimeo, TikTok, Instagram, LinkedIn, Castbox) "
                    "aby pobrać film lub audio."
                )

                # Check for pending URL
                pending_url = context.user_data.get("pending_url")
                if pending_url:
                    context.user_data.pop("pending_url", None)
                    await process_youtube_link(update, context, pending_url)

                # Check for pending audio upload
                pending_audio = context.user_data.get("pending_audio")
                if pending_audio:
                    context.user_data.pop("pending_audio", None)
                    await process_audio_file(update, context, pending_audio)

                # Check for pending video upload
                pending_video = context.user_data.get("pending_video")
                if pending_video:
                    context.user_data.pop("pending_video", None)
                    await process_video_file(update, context, pending_video)
            else:
                # Increment failed attempts counter
                remaining_attempts, attempt_count = register_pin_failure(
                    user_id,
                    attempts=failed_attempts,
                    block_map=block_until,
                    max_attempts=MAX_ATTEMPTS,
                    block_time=BLOCK_TIME,
                )

                blocked = remaining_attempts == 0

                # Notify admin (non-blocking)
                await notify_admin_pin_failure(
                    context.bot, update.effective_user,
                    attempt_count, blocked,
                )

                if blocked:
                    await update.message.reply_text(
                        "Niepoprawny PIN!\n\n"
                        f"Przekroczono maksymalną liczbę prób ({MAX_ATTEMPTS}).\n"
                        f"Dostęp zablokowany na {BLOCK_TIME // 60} minut."
                    )
                else:
                    await update.message.reply_text(
                        "Niepoprawny PIN!\n\n"
                        f"Pozostało prób: {remaining_attempts}"
                    )

            # Delete message containing PIN for security
            try:
                await update.message.delete()
            except Exception:
                pass

            return True

    return False


async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /logout command - removes user from authorized list."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if user_id not in authorized_users:
        await update.message.reply_text("Nie jesteś zalogowany.")
        return

    manage_authorized_user(user_id, 'remove')

    # Clear session state
    user_urls.pop(chat_id, None)
    user_time_ranges.pop(chat_id, None)
    context.user_data.clear()

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
        "- Castbox (castbox.fm)\n\n"
        "Komendy administracyjne:\n"
        "- /status - sprawdź przestrzeń dyskową\n"
        "- /cleanup - usuń stare pliki (>24h)",
        parse_mode='Markdown'
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /status command - shows disk space status."""
    user_id = update.effective_user.id

    if user_id not in authorized_users:
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

    await update.message.reply_text(status_msg, parse_mode='Markdown')


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /history command - shows download history and statistics."""
    user_id = update.effective_user.id

    if user_id not in authorized_users:
        await update.message.reply_text("Brak autoryzacji. Użyj /start aby się zalogować.")
        return

    # Get stats for this user
    stats = get_download_stats(user_id)

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

    if user_id not in authorized_users:
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

    if user_id not in authorized_users:
        await update.message.reply_text("Brak autoryzacji. Użyj /start aby się zalogować.")
        return

    if not _is_admin(user_id):
        await update.message.reply_text("Ta komenda jest dostępna tylko dla administratora.")
        return

    user_count = len(authorized_users)
    user_list = ', '.join(str(uid) for uid in sorted(authorized_users))

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
    if user_id not in authorized_users:
        context.user_data["pending_url"] = message_text

        await update.message.reply_text(
            "Wymagane uwierzytelnienie!\n\n"
            "Proszę podaj 8-cyfrowy kod PIN, aby uzyskać dostęp."
        )

        context.user_data["awaiting_pin"] = True
        return

    # Check if user has an active URL session and message looks like a time range
    current_url = user_urls.get(chat_id)
    if current_url:
        time_range = parse_time_range(message_text)
        if time_range:
            # Get video info to validate time range against duration
            info = get_video_info(current_url)
            if info:
                duration = info.get('duration', 0)
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
                user_time_ranges[chat_id] = time_range

                # Send confirmation and show main menu with updated time range
                cur_platform = context.user_data.get('platform', 'youtube')
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
            "- Castbox (castbox.fm)"
        )
        return

    await process_youtube_link(update, context, message_text)


def _build_playlist_message(playlist_info: dict) -> tuple[str, InlineKeyboardMarkup]:
    """Builds playlist listing message and keyboard.

    Returns:
        Tuple of (message_text, reply_markup).
    """
    entries = playlist_info['entries']
    total = playlist_info.get('playlist_count', len(entries))

    msg = f"*{escape_md(playlist_info['title'])}*\n"
    msg += f"Filmów: {len(entries)}"
    if total > len(entries):
        msg += f" (z {total})"
    msg += "\n\n"

    for i, entry in enumerate(entries, 1):
        title = entry.get('title', 'Nieznany')[:50]
        duration = entry.get('duration')
        if duration:
            dur_str = f"{duration // 60}:{duration % 60:02d}"
        else:
            dur_str = "?"
        msg += f"{i}. {escape_md(title)} ({dur_str})\n"

    keyboard = [
        [InlineKeyboardButton("Pobierz wszystkie — Audio MP3", callback_data="pl_dl_audio_mp3")],
        [InlineKeyboardButton("Pobierz wszystkie — Audio M4A", callback_data="pl_dl_audio_m4a")],
        [InlineKeyboardButton("Pobierz wszystkie — Video (najlepsza)", callback_data="pl_dl_video_best")],
        [InlineKeyboardButton("Pobierz wszystkie — Video 720p", callback_data="pl_dl_video_720p")],
    ]

    # Show "load more" button when playlist has more items than currently displayed
    if total > len(entries) and len(entries) < MAX_PLAYLIST_ITEMS_EXPANDED:
        more_count = min(total, MAX_PLAYLIST_ITEMS_EXPANDED)
        keyboard.append([InlineKeyboardButton(
            f"Pokaż więcej (do {more_count})", callback_data="pl_more"
        )])

    keyboard.append([InlineKeyboardButton("Anuluj", callback_data="pl_cancel")])

    return msg, InlineKeyboardMarkup(keyboard)


async def process_playlist_link(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    """Handles playlist URL — fetches info and shows playlist menu."""
    chat_id = update.effective_chat.id

    progress_message = await update.message.reply_text(
        "Wykryto playlistę! Pobieranie informacji..."
    )

    playlist_info = get_playlist_info(url, max_items=MAX_PLAYLIST_ITEMS)

    if not playlist_info:
        await progress_message.edit_text(
            "Nie udało się pobrać informacji o playliście."
        )
        return

    if not playlist_info['entries']:
        await progress_message.edit_text("Playlista jest pusta.")
        return

    # Store playlist data in session
    user_playlist_data[chat_id] = playlist_info
    user_urls[chat_id] = url

    msg, reply_markup = _build_playlist_message(playlist_info)
    await progress_message.edit_text(msg, reply_markup=reply_markup, parse_mode='Markdown')


async def process_youtube_link(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    """Processes a media link after PIN authorization."""
    chat_id = update.effective_chat.id
    user_urls[chat_id] = url
    # Clear any previous time range
    user_time_ranges.pop(chat_id, None)

    # Detect and store platform for conditional UI
    platform = detect_platform(url) or 'youtube'
    context.user_data['platform'] = platform

    # Castbox: channel URLs are not supported, only episode URLs
    if platform == 'castbox' and '/channel/' in url:
        await update.message.reply_text(
            "Castbox: link do kanału nie jest obsługiwany.\n\n"
            "Wyślij link do konkretnego odcinka podcastu\n"
            "(np. castbox.fm/episode/...)."
        )
        return

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

    progress_message = await update.message.reply_text("Pobieranie informacji o filmie...")

    info = get_video_info(url)
    if not info:
        await progress_message.edit_text("Wystąpił błąd podczas pobierania informacji o filmie.")
        return

    title = info.get('title', 'Nieznany tytuł')
    duration = info.get('duration', 0)
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
    time_range = user_time_ranges.get(chat_id)
    time_range_info = ""
    if time_range:
        time_range_info = f"\n✂️ Zakres: {time_range['start']} - {time_range['end']}"

    await progress_message.edit_text(
        f"*{escape_md(title)}*\nCzas trwania: {duration_str}{size_warning}{time_range_info}\n\nWybierz format do pobrania:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


# Telegram Bot API download limit (bots can download files up to 20MB)
TELEGRAM_DOWNLOAD_LIMIT_MB = 20


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

    if user_id not in authorized_users:
        context.user_data["pending_audio"] = audio_info
        await message.reply_text(
            "Wymagane uwierzytelnienie!\n\n"
            "Proszę podaj 8-cyfrowy kod PIN, aby uzyskać dostęp."
        )
        context.user_data["awaiting_pin"] = True
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

    if file_size_mb > TELEGRAM_DOWNLOAD_LIMIT_MB:
        await message.reply_text(
            f"Plik jest za duży do pobrania przez Telegram Bot API.\n\n"
            f"Rozmiar: {file_size_mb:.1f} MB\n"
            f"Limit: {TELEGRAM_DOWNLOAD_LIMIT_MB} MB"
        )
        return

    progress_msg = await message.reply_text("Pobieranie pliku audio...")

    chat_download_path = os.path.join(DOWNLOAD_PATH, str(chat_id))
    os.makedirs(chat_download_path, exist_ok=True)

    try:
        tg_file = await context.bot.get_file(audio_info['file_id'])

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
        context.user_data['audio_file_path'] = mp3_path
        context.user_data['audio_file_title'] = title

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

    if user_id not in authorized_users:
        context.user_data["pending_video"] = video_info
        await message.reply_text(
            "Wymagane uwierzytelnienie!\n\n"
            "Proszę podaj 8-cyfrowy kod PIN, aby uzyskać dostęp."
        )
        context.user_data["awaiting_pin"] = True
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

    if file_size_mb > TELEGRAM_DOWNLOAD_LIMIT_MB:
        await message.reply_text(
            f"Plik jest za duży do pobrania przez Telegram Bot API.\n\n"
            f"Rozmiar: {file_size_mb:.1f} MB\n"
            f"Limit: {TELEGRAM_DOWNLOAD_LIMIT_MB} MB"
        )
        return

    progress_msg = await message.reply_text("Pobieranie pliku video...")

    chat_download_path = os.path.join(DOWNLOAD_PATH, str(chat_id))
    os.makedirs(chat_download_path, exist_ok=True)

    try:
        tg_file = await context.bot.get_file(video_info['file_id'])

        title = video_info['title']
        ext = video_info['ext']

        # Sanitize title for filename
        safe_title = "".join(c if c.isalnum() or c in ' -_' else '_' for c in title)[:80]
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        video_path = os.path.join(chat_download_path, f"{timestamp}_{safe_title}{ext}")

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
        context.user_data['audio_file_path'] = mp3_path
        context.user_data['audio_file_title'] = title

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
