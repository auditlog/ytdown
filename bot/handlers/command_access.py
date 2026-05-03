"""Access, auth, and admin/status command handlers."""

from __future__ import annotations

import logging
import os
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from bot.cleanup import cleanup_old_files, get_disk_usage
from bot.config import DOWNLOAD_PATH, get_download_stats, get_runtime_value
from bot.runtime import (
    add_authorized_user_for,
    get_app_runtime,
    get_authorized_user_ids_for,
    remove_authorized_user_for,
)
from bot.security_limits import BLOCK_TIME, MAX_ATTEMPTS
from bot.session_store import block_until, failed_attempts, user_playlist_data, user_time_ranges, user_urls
from bot.services.auth_service import (
    clear_auth_security_state,
    handle_pin_input,
    handle_start,
    logout_user,
)
from bot.session_context import (
    clear_transient_flow_state as _clear_transient_flow_state,
    get_auth_state as _get_auth_state,
)
from bot.platforms import PLATFORMS


def _format_supported_platforms_block() -> str:
    """Return a newline-joined bullet list of platforms for help messages."""

    return "\n".join(f"- {p.display_name} ({p.domains[0]})" for p in PLATFORMS)


def _format_cookies_required_names() -> str:
    """Return a comma-separated list of platforms that typically need cookies.txt."""

    names = [p.display_name for p in PLATFORMS if p.requires_cookies]
    return ", ".join(names)


async def process_youtube_link(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    from bot.handlers.inbound_media import process_youtube_link as _process_youtube_link

    return await _process_youtube_link(update, context, url)


async def process_audio_file(update: Update, context: ContextTypes.DEFAULT_TYPE, audio_info):
    from bot.handlers.inbound_media import process_audio_file as _process_audio_file

    return await _process_audio_file(update, context, audio_info)


async def process_video_file(update: Update, context: ContextTypes.DEFAULT_TYPE, video_info):
    from bot.handlers.inbound_media import process_video_file as _process_video_file

    return await _process_video_file(update, context, video_info)


def _is_admin(user_id: int) -> bool:
    """Returns True if user_id matches ADMIN_CHAT_ID."""
    admin_chat_id = get_runtime_value("ADMIN_CHAT_ID", "")
    if not admin_chat_id:
        return True
    try:
        return user_id == int(admin_chat_id)
    except (ValueError, TypeError):
        return False


def _get_authorized_user_ids(context: ContextTypes.DEFAULT_TYPE) -> set[int]:
    """Return authorized users from runtime when available."""
    return get_authorized_user_ids_for(context)


def _is_authorized(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    return user_id in _get_authorized_user_ids(context)


def _get_history_stats(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> dict:
    runtime = get_app_runtime(context)
    if runtime is not None:
        return runtime.download_history_repository.stats(user_id=user_id)
    return get_download_stats(user_id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    admin_chat_id = get_runtime_value("ADMIN_CHAT_ID", "")
    if not admin_chat_id:
        return

    try:
        admin_id = int(admin_chat_id)
    except (ValueError, TypeError):
        logging.warning("ADMIN_CHAT_ID is not a valid integer: %s", admin_chat_id)
        return

    try:
        emoji = "\U0001f6ab" if blocked else "\u26a0\ufe0f"
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
        f"{_format_supported_platforms_block()}\n\n"
        "🔒 *Platformy wymagające logowania:*\n"
        f"{_format_cookies_required_names()} mogą wymagać pliku cookies.txt\n"
        "do pobierania treści z ograniczonym dostępem.\n\n"
        "Komendy administracyjne:\n"
        "- /status - sprawdź przestrzeń dyskową\n"
        "- /cleanup - usuń stare pliki (>24h)",
        parse_mode="Markdown",
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not _is_authorized(context, user_id):
        await update.message.reply_text("Brak autoryzacji. Użyj /start aby się zalogować.")
        return

    used_gb, free_gb, total_gb, usage_percent = get_disk_usage()
    file_count = 0
    total_size_mb = 0

    try:
        for root, _dirs, files in os.walk(DOWNLOAD_PATH):
            for file_name in files:
                file_count += 1
                file_path = os.path.join(root, file_name)
                total_size_mb += os.path.getsize(file_path) / (1024 * 1024)
    except Exception:
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

    from bot.downloader_media import COOKIES_FILE
    if os.path.exists(COOKIES_FILE):
        cookie_size = os.path.getsize(COOKIES_FILE)
        status_msg += f"\n**cookies.txt:** ✅ ({cookie_size} B)\n"
    else:
        status_msg += f"\n**cookies.txt:** ❌ brak ({_format_cookies_required_names()} mogą wymagać)\n"

    await update.message.reply_text(status_msg, parse_mode="Markdown")


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not _is_authorized(context, user_id):
        await update.message.reply_text("Brak autoryzacji. Użyj /start aby się zalogować.")
        return

    stats = _get_history_stats(context, user_id)
    if stats["total_downloads"] == 0:
        await update.message.reply_text("Brak historii pobrań.")
        return

    msg = "📊 **Historia pobrań**\n\n"
    msg += f"**Twoje statystyki:**\n"
    msg += f"- Łączna liczba pobrań: {stats['total_downloads']}\n"
    msg += f"- Udane: {stats['success_count']} ✅  Nieudane: {stats['failure_count']} ❌\n"
    msg += f"- Łączny rozmiar: {stats['total_size_mb']:.1f} MB\n\n"

    if stats["format_counts"]:
        msg += "**Formaty:**\n"
        for fmt, count in sorted(stats["format_counts"].items(), key=lambda item: -item[1]):
            msg += f"- {fmt}: {count}\n"
        msg += "\n"

    if stats["recent"]:
        msg += "**Ostatnie pobrania:**\n"
        for record in stats["recent"][:5]:
            title = record.get("title", "Nieznany")[:40]
            if len(record.get("title", "")) > 40:
                title += "..."
            timestamp = record.get("timestamp", "")[:10]
            fmt = record.get("format", "?")
            size = record.get("file_size_mb", 0)
            status_icon = "✅" if record.get("status", "success") == "success" else "❌"
            time_range_str = f" ✂️{record['time_range']}" if record.get("time_range") else ""
            msg += f"- {status_icon} `{timestamp}` {title} ({fmt}, {size:.1f}MB){time_range_str}\n"

    await update.message.reply_text(msg, parse_mode="Markdown")


async def cleanup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not _is_authorized(context, user_id):
        await update.message.reply_text("Brak autoryzacji. Użyj /start aby się zalogować.")
        return

    await update.message.reply_text("Rozpoczynam czyszczenie starych plików...")
    deleted_count = cleanup_old_files(DOWNLOAD_PATH, max_age_hours=24)
    _used_gb, free_gb, _total_gb, _usage_percent = get_disk_usage()

    if deleted_count > 0:
        await update.message.reply_text(
            f"Czyszczenie zakończone!\n\n"
            f"- Usunięto plików: {deleted_count}\n"
            f"- Wolna przestrzeń: {free_gb:.1f} GB"
        )
        return

    await update.message.reply_text(
        "Brak plików do usunięcia.\n"
        "Wszystkie pliki są młodsze niż 24 godziny."
    )


async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not _is_authorized(context, user_id):
        await update.message.reply_text("Brak autoryzacji. Użyj /start aby się zalogować.")
        return
    if not _is_admin(user_id):
        await update.message.reply_text("Ta komenda jest dostępna tylko dla administratora.")
        return

    authorized_user_ids = _get_authorized_user_ids(context)
    user_count = len(authorized_user_ids)
    user_list = ", ".join(str(uid) for uid in sorted(authorized_user_ids))

    await update.message.reply_text(
        f"Autoryzowani użytkownicy\n\n"
        f"- Liczba: {user_count}\n"
        f"- Lista ID: {user_list if user_count <= 10 else str(user_count) + ' użytkowników'}\n"
        f"- Twoje ID: {user_id}"
    )
