"""
Telegram commands module for YouTube Downloader Telegram Bot.

Contains command handlers (/start, /help, /status, /cleanup, /users)
and PIN authentication logic.
"""

import os
import time
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot.config import (
    DOWNLOAD_PATH,
    PIN_CODE,
    authorized_users,
)
from bot.security import (
    MAX_ATTEMPTS,
    BLOCK_TIME,
    RATE_LIMIT_REQUESTS,
    RATE_LIMIT_WINDOW,
    MAX_FILE_SIZE_MB,
    failed_attempts,
    block_until,
    user_urls,
    check_rate_limit,
    validate_youtube_url,
    manage_authorized_user,
    estimate_file_size,
)
from bot.cleanup import (
    cleanup_old_files,
    get_disk_usage,
)
from bot.downloader import get_video_info


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /start command."""
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name

    # Check if user is blocked
    if time.time() < block_until[user_id]:
        remaining_time = int(block_until[user_id] - time.time())
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
            "Jesteś już zalogowany. Możesz wysłać link do YouTube, aby pobrać film lub audio."
        )
        return

    # If user is not authorized, ask for PIN
    await update.message.reply_text(
        f"Witaj, {user_name}!\n\n"
        "To jest bot chroniony PIN-em.\n"
        "Aby korzystać z bota, podaj 8-cyfrowy kod PIN."
    )

    context.user_data["awaiting_pin"] = True


async def handle_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles PIN input from user."""
    user_id = update.effective_user.id
    message_text = update.message.text

    # Check if user is blocked
    if time.time() < block_until[user_id]:
        remaining_time = int(block_until[user_id] - time.time())
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
        # Check if message looks like PIN (8 digits)
        if message_text.isdigit() and len(message_text) == 8:
            if message_text == PIN_CODE:
                # Reset failed attempts counter
                failed_attempts[user_id] = 0

                # Add user to authorized list
                manage_authorized_user(user_id, 'add')

                # Remove awaiting PIN state
                context.user_data.pop("awaiting_pin", None)

                await update.message.reply_text(
                    "PIN poprawny! Możesz teraz korzystać z bota.\n\n"
                    "Wyślij link do YouTube, aby pobrać film lub audio."
                )

                # Check for pending URL
                pending_url = context.user_data.get("pending_url")
                if pending_url:
                    context.user_data.pop("pending_url", None)
                    await process_youtube_link(update, context, pending_url)
            else:
                # Increment failed attempts counter
                failed_attempts[user_id] += 1

                if failed_attempts[user_id] >= MAX_ATTEMPTS:
                    block_until[user_id] = time.time() + BLOCK_TIME

                    await update.message.reply_text(
                        "Niepoprawny PIN!\n\n"
                        f"Przekroczono maksymalną liczbę prób ({MAX_ATTEMPTS}).\n"
                        f"Dostęp zablokowany na {BLOCK_TIME // 60} minut."
                    )
                else:
                    remaining_attempts = MAX_ATTEMPTS - failed_attempts[user_id]
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


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /help command."""
    await update.message.reply_text(
        "Jak korzystać z bota:\n\n"
        "1. Wyślij link do filmu z YouTube\n"
        "2. Wybierz format (video lub audio) i jakość\n"
        "3. Poczekaj na pobranie pliku\n\n"
        "Bot obsługuje linki z YouTube w formatach:\n"
        "- https://www.youtube.com/watch?v=...\n"
        "- https://youtu.be/...\n\n"
        "Komendy administracyjne:\n"
        "- /status - sprawdź przestrzeń dyskową\n"
        "- /cleanup - usuń stare pliki (>24h)"
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

    user_count = len(authorized_users)
    user_list = ', '.join(str(uid) for uid in sorted(authorized_users))

    await update.message.reply_text(
        f"Autoryzowani użytkownicy\n\n"
        f"- Liczba: {user_count}\n"
        f"- Lista ID: {user_list if user_count <= 10 else str(user_count) + ' użytkowników'}\n"
        f"- Twoje ID: {user_id}"
    )


async def handle_youtube_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles YouTube links."""
    user_id = update.effective_user.id
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
    if not validate_youtube_url(message_text):
        await update.message.reply_text(
            "Nieprawidłowy URL!\n\n"
            "Podaj prawidłowy link do YouTube.\n"
            "Obsługiwane formaty:\n"
            "- https://www.youtube.com/watch?v=...\n"
            "- https://youtu.be/...\n"
            "- https://music.youtube.com/..."
        )
        return

    # Check if user is blocked
    if time.time() < block_until[user_id]:
        remaining_time = int(block_until[user_id] - time.time())
        minutes = remaining_time // 60
        seconds = remaining_time % 60

        await update.message.reply_text(
            f"Dostęp zablokowany z powodu zbyt wielu nieudanych prób. "
            f"Spróbuj ponownie za {minutes} min {seconds} s."
        )
        return

    await process_youtube_link(update, context, message_text)


async def process_youtube_link(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    """Processes YouTube link after PIN authorization."""
    chat_id = update.effective_chat.id
    user_urls[chat_id] = url

    progress_message = await update.message.reply_text("Pobieranie informacji o filmie...")

    info = get_video_info(url)
    if not info:
        await progress_message.edit_text("Wystąpił błąd podczas pobierania informacji o filmie.")
        return

    title = info.get('title', 'Nieznany tytuł')

    estimated_size = estimate_file_size(info)
    size_warning = ""

    if estimated_size and estimated_size > MAX_FILE_SIZE_MB:
        size_warning = f"\n*Uwaga:* Szacowany rozmiar najlepszej jakości: {estimated_size:.1f} MB (limit: {MAX_FILE_SIZE_MB} MB)\n"

        keyboard = [
            [InlineKeyboardButton("Video 1080p (Full HD)", callback_data="dl_video_1080p")],
            [InlineKeyboardButton("Video 720p (HD)", callback_data="dl_video_720p")],
            [InlineKeyboardButton("Video 480p (SD)", callback_data="dl_video_480p")],
            [InlineKeyboardButton("Video 360p (Niska jakość)", callback_data="dl_video_360p")],
            [InlineKeyboardButton("Audio (MP3)", callback_data="dl_audio_mp3")],
            [InlineKeyboardButton("Audio (M4A)", callback_data="dl_audio_m4a")],
            [InlineKeyboardButton("Transkrypcja audio", callback_data="transcribe")],
            [InlineKeyboardButton("Transkrypcja + Podsumowanie", callback_data="transcribe_summary")],
            [InlineKeyboardButton("Lista formatów", callback_data="formats")]
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("Najlepsza jakość video", callback_data="dl_video_best")],
            [InlineKeyboardButton("Audio (MP3)", callback_data="dl_audio_mp3")],
            [InlineKeyboardButton("Audio (M4A)", callback_data="dl_audio_m4a")],
            [InlineKeyboardButton("Audio (FLAC)", callback_data="dl_audio_flac")],
            [InlineKeyboardButton("Transkrypcja audio", callback_data="transcribe")],
            [InlineKeyboardButton("Transkrypcja + Podsumowanie", callback_data="transcribe_summary")],
            [InlineKeyboardButton("Lista formatów", callback_data="formats")]
        ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await progress_message.edit_text(
        f"*{title}*\n{size_warning}\nWybierz format do pobrania:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
