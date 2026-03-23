"""Time-range selection and back-to-menu callback flows."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.downloader_metadata import get_video_info
from bot.handlers.common_ui import build_main_keyboard, escape_md, safe_edit_message
from bot.security_policy import get_media_label
from bot.session_context import (
    get_session_context_value as _get_session_context_value,
    get_session_value as _get_session_value,
    set_session_value as _set_session_value,
)
from bot.session_store import user_time_ranges


async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    """Re-display the main download menu for a given URL."""

    query = update.callback_query
    chat_id = update.effective_chat.id
    platform = _get_session_context_value(context, chat_id, "platform", legacy_key="platform", default="youtube")

    info = get_video_info(url)
    if not info:
        media_name = get_media_label(platform)
        await query.edit_message_text(f"Wystąpił błąd podczas pobierania informacji o {media_name}.")
        return

    title = info.get("title", "Nieznany tytuł")
    duration = int(info.get("duration") or 0)
    duration_str = f"{duration // 60}:{duration % 60:02d}" if duration else "?"
    keyboard = build_main_keyboard(platform)

    time_range = _get_session_value(context, chat_id, "time_range", user_time_ranges)
    time_range_info = f"\n✂️ Zakres: {time_range['start']} - {time_range['end']}" if time_range else ""

    await safe_edit_message(
        query,
        f"*{escape_md(title)}*\nCzas trwania: {duration_str}{time_range_info}\n\nWybierz format do pobrania:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def show_time_range_options(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    """Display time-range preset menu for a video."""

    query = update.callback_query
    chat_id = update.effective_chat.id

    info = get_video_info(url)
    if not info:
        media_name = get_media_label(_get_session_context_value(context, chat_id, "platform", legacy_key="platform"))
        await query.edit_message_text(f"Wystąpił błąd podczas pobierania informacji o {media_name}.")
        return

    title = info.get("title", "Nieznany tytuł")
    duration = int(info.get("duration") or 0)
    duration_str = f"{duration // 60}:{duration % 60:02d}" if duration else "?"

    time_range = _get_session_value(context, chat_id, "time_range", user_time_ranges)
    current_range = f"\n\n✂️ Aktualny zakres: {time_range['start']} - {time_range['end']}" if time_range else ""

    keyboard = [
        [InlineKeyboardButton("Pierwsze 5 minut", callback_data="time_range_preset_first_5")],
        [InlineKeyboardButton("Pierwsze 10 minut", callback_data="time_range_preset_first_10")],
        [InlineKeyboardButton("Pierwsze 30 minut", callback_data="time_range_preset_first_30")],
        [InlineKeyboardButton("Ostatnie 5 minut", callback_data="time_range_preset_last_5")],
        [InlineKeyboardButton("Ostatnie 10 minut", callback_data="time_range_preset_last_10")],
    ]
    if time_range:
        keyboard.append([InlineKeyboardButton("❌ Usuń zakres (cały film)", callback_data="time_range_clear")])
    keyboard.append([InlineKeyboardButton("Powrót", callback_data="back")])

    await safe_edit_message(
        query,
        f"*{escape_md(title)}*\nCzas trwania: {duration_str}{current_range}\n\n"
        f"Wybierz zakres czasowy do pobrania:\n\n"
        f"💡 Możesz też wpisać własny zakres w formacie:\n"
        f"`0:30-5:45` lub `1:00:00-1:30:00`",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def apply_time_range_preset(update: Update, context: ContextTypes.DEFAULT_TYPE, url, preset):
    """Apply a time-range preset and return to the main menu."""

    query = update.callback_query
    chat_id = update.effective_chat.id

    info = get_video_info(url)
    if not info:
        media_name = get_media_label(_get_session_context_value(context, chat_id, "platform", legacy_key="platform"))
        await query.edit_message_text(f"Wystąpił błąd podczas pobierania informacji o {media_name}.")
        return

    duration = info.get("duration", 0)
    if not duration:
        await query.edit_message_text("Nie można określić czasu trwania filmu.")
        return

    start_sec = 0
    end_sec = duration
    if preset == "first_5":
        end_sec = min(5 * 60, duration)
    elif preset == "first_10":
        end_sec = min(10 * 60, duration)
    elif preset == "first_30":
        end_sec = min(30 * 60, duration)
    elif preset == "last_5":
        start_sec = max(0, duration - 5 * 60)
    elif preset == "last_10":
        start_sec = max(0, duration - 10 * 60)

    def format_time(seconds):
        if seconds >= 3600:
            return f"{int(seconds // 3600)}:{int((seconds % 3600) // 60):02d}:{int(seconds % 60):02d}"
        return f"{int(seconds // 60)}:{int(seconds % 60):02d}"

    _set_session_value(
        context,
        chat_id,
        "time_range",
        {
            "start": format_time(start_sec),
            "end": format_time(end_sec),
            "start_sec": start_sec,
            "end_sec": end_sec,
        },
        user_time_ranges,
    )
    await back_to_main_menu(update, context, url)
