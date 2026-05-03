"""Shared Telegram UI helpers for callback/command flows."""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton
from telegram.error import BadRequest, NetworkError, TimedOut
from telegram.helpers import escape_markdown


def escape_md(text: str) -> str:
    """Escape Markdown v1 special characters in text."""

    return escape_markdown(text, version=1)


def build_main_keyboard(platform: str, large_file: bool = False) -> list:
    """Build the main format selection keyboard for a detected platform."""

    from bot.platforms import get_platform

    config = get_platform(platform)
    if config is None:
        raise ValueError(f"Unknown platform in session: {platform!r}")
    is_podcast = config.is_podcast
    hide_flac = config.hide_flac
    hide_time_range = config.hide_time_range

    if is_podcast:
        return [
            [InlineKeyboardButton("Audio (MP3)", callback_data="dl_audio_mp3")],
            [InlineKeyboardButton("Audio (M4A)", callback_data="dl_audio_m4a")],
            [InlineKeyboardButton("Transkrypcja audio", callback_data="transcribe")],
            [InlineKeyboardButton("Transkrypcja + Podsumowanie", callback_data="transcribe_summary")],
        ]

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
            [InlineKeyboardButton("Video — najwyższa (do 4K/2160p)", callback_data="dl_video_best")],
            [InlineKeyboardButton("Video — średnia (720p HD)", callback_data="dl_video_medium")],
            [InlineKeyboardButton("Audio (MP3)", callback_data="dl_audio_mp3")],
            [InlineKeyboardButton("Audio (M4A)", callback_data="dl_audio_m4a")],
        ]
        if not hide_flac:
            keyboard.append([InlineKeyboardButton("Audio (FLAC)", callback_data="dl_audio_flac")])
        keyboard.extend(
            [
                [InlineKeyboardButton("Transkrypcja audio", callback_data="transcribe")],
                [InlineKeyboardButton("Transkrypcja + Podsumowanie", callback_data="transcribe_summary")],
            ]
        )

    if not hide_time_range:
        keyboard.append([InlineKeyboardButton("✂️ Zakres czasowy", callback_data="time_range")])
    keyboard.append(
        [
            InlineKeyboardButton("Lista formatów", callback_data="formats"),
            InlineKeyboardButton("Miniaturka", callback_data="thumbnail"),
        ]
    )
    return keyboard


def build_instagram_photo_keyboard(photos: list, videos: list) -> list:
    """Build keyboard for Instagram photo and carousel download choices."""

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


def format_bytes(bytes_value):
    """Formats bytes to human readable string."""
    if bytes_value is None:
        return "?"
    for unit in ["B", "KB", "MB", "GB"]:
        if bytes_value < 1024:
            return f"{bytes_value:.1f} {unit}"
        bytes_value /= 1024
    return f"{bytes_value:.1f} TB"


def format_eta(seconds):
    """Formats seconds to human readable time string."""
    if seconds is None or seconds < 0:
        return "?"
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"


async def safe_edit_message(query, text, reply_markup=None, parse_mode=None):
    """Safely edit a Telegram message and ignore common transient failures."""

    try:
        await query.edit_message_text(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
    except BadRequest as exc:
        if "Message is not modified" not in str(exc):
            raise
    except (NetworkError, TimedOut) as exc:
        logging.warning("Network error updating status message: %s", exc)


async def send_long_message(bot, chat_id, text, header="", parse_mode="Markdown"):
    """Split and send a long Telegram message in multiple chunks."""

    max_length = 4000
    parts = []
    current = header

    for line in text.split("\n"):
        while len(line) > max_length:
            split_at = max_length
            for sep in [". ", "! ", "? ", ", ", " "]:
                idx = line.rfind(sep, 0, max_length)
                if idx > max_length // 2:
                    split_at = idx + len(sep)
                    break
            if current.strip():
                parts.append(current)
                current = ""
            parts.append(line[:split_at])
            line = line[split_at:]

        if len(current) + len(line) + 2 > max_length:
            parts.append(current)
            current = line + "\n"
        else:
            current += line + "\n"

    if current.strip():
        parts.append(current)

    for part in parts:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=part,
                parse_mode=parse_mode,
                read_timeout=60,
                write_timeout=60,
            )
        except BadRequest:
            await bot.send_message(
                chat_id=chat_id,
                text=part,
                read_timeout=60,
                write_timeout=60,
            )
