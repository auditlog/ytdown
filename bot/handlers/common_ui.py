"""Shared Telegram UI helpers for callback/command flows."""

from __future__ import annotations

import logging
import math

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


def build_spotify_episode_keyboard(
    *,
    quality_options: list,
    has_native_audio: bool,
    has_fallback_audio: bool,
) -> list:
    """Build the keyboard for a Spotify episode from actually available sources.

    Buttons that would fail under the current configuration are omitted rather
    than shown and then erroring — the native video path needs a cookie jar,
    the legacy iTunes/YouTube path needs Web API credentials, and an episode
    may have either, both, or neither.
    """

    keyboard = []

    for option in quality_options:
        height = option["height"]
        size_mb = option["size_mb"]
        keyboard.append([
            InlineKeyboardButton(
                f"Video {height}p (~{size_mb:.0f} MB)",
                callback_data=f"spv_video_{height}p",
            )
        ])

    if has_native_audio:
        keyboard.append([
            InlineKeyboardButton("Audio (M4A) — Spotify", callback_data="spv_audio_m4a")
        ])

    if has_fallback_audio:
        keyboard.append([InlineKeyboardButton("Audio (MP3)", callback_data="dl_audio_mp3")])
        keyboard.append([InlineKeyboardButton("Audio (M4A)", callback_data="dl_audio_m4a")])

    keyboard.append([InlineKeyboardButton("Transkrypcja audio", callback_data="transcribe")])
    keyboard.append([
        InlineKeyboardButton("Transkrypcja + Podsumowanie", callback_data="transcribe_summary")
    ])

    return keyboard


def build_spotify_track_keyboard() -> list:
    """Build audio choices for a Spotify music track resolved via YT Music."""

    return [
        [InlineKeyboardButton("Audio (MP3)", callback_data="dl_audio_mp3")],
        [InlineKeyboardButton("Audio (M4A)", callback_data="dl_audio_m4a")],
    ]


def build_spotify_collection_view(
    collection: dict,
    *,
    page_size: int = 8,
) -> tuple[str, list]:
    """Render a paginated multi-select album/playlist view."""

    tracks = collection.get("tracks") or []
    selected = {int(index) for index in collection.get("selected") or []}
    page_count = max(1, math.ceil(len(tracks) / page_size))
    page = min(max(int(collection.get("page") or 0), 0), page_count - 1)
    collection["page"] = page

    kind_label = "Album" if collection.get("kind") == "album" else "Playlista"
    owner = collection.get("owner", "")
    owner_line = f"\nAutor: {escape_md(owner)}" if owner else ""
    loaded = len(tracks)
    total = int(collection.get("total") or loaded)
    truncated_line = (
        f"\nPokazano pierwsze {loaded} z {total} pozycji."
        if collection.get("truncated")
        else ""
    )
    text = (
        f"*{kind_label}: {escape_md(collection.get('title', 'Spotify'))}*"
        f"{owner_line}\nUtwory: {loaded}/{total}"
        f"\nZaznaczono: {len(selected)}{truncated_line}\n\n"
        "Wybierz utwory do pobrania z YT Music:"
    )

    keyboard = []
    start = page * page_size
    for index in range(start, min(start + page_size, len(tracks))):
        track = tracks[index]
        marker = "✅" if index in selected else "▫️"
        artist = track.get("artist", "")
        label = f"{index + 1}. {artist} — {track.get('title', '?')}" if artist else (
            f"{index + 1}. {track.get('title', '?')}"
        )
        if len(label) > 52:
            label = f"{label[:49]}..."
        keyboard.append([
            InlineKeyboardButton(f"{marker} {label}", callback_data=f"spc_t_{index}")
        ])

    if page_count > 1:
        navigation = []
        if page > 0:
            navigation.append(InlineKeyboardButton("⬅️", callback_data=f"spc_p_{page - 1}"))
        navigation.append(
            InlineKeyboardButton(f"{page + 1}/{page_count}", callback_data=f"spc_p_{page}")
        )
        if page < page_count - 1:
            navigation.append(InlineKeyboardButton("➡️", callback_data=f"spc_p_{page + 1}"))
        keyboard.append(navigation)

    keyboard.append([
        InlineKeyboardButton("Zaznacz wszystkie", callback_data="spc_all"),
        InlineKeyboardButton("Wyczyść", callback_data="spc_clear"),
    ])
    keyboard.append([
        InlineKeyboardButton(
            f"Pobierz MP3 ({len(selected)})",
            callback_data="spc_dl_mp3",
        ),
        InlineKeyboardButton(
            f"Pobierz M4A ({len(selected)})",
            callback_data="spc_dl_m4a",
        ),
    ])
    if collection.get("archive_available"):
        keyboard.append([
            InlineKeyboardButton(
                f"MP3 → paczki 7z ({len(selected)})",
                callback_data="spc_pack_mp3",
            ),
            InlineKeyboardButton(
                f"M4A → paczki 7z ({len(selected)})",
                callback_data="spc_pack_m4a",
            ),
        ])
    return text, keyboard


def build_spotify_archive_batch_view(
    collection: dict,
    *,
    audio_format: str,
) -> tuple[str, list]:
    """Ask how many selected Spotify tracks should go into each archive."""

    selected_count = len(collection.get("selected") or [])
    format_label = audio_format.upper()
    text = (
        f"*Spotify → {format_label} → 7z*\n"
        f"Zaznaczono: {selected_count}\n\n"
        "Ile utworów ma zawierać jedno archiwum?\n"
        "Archiwa większe niż około 1 GB zostaną dodatkowo podzielone na wolumeny."
    )
    prefix = f"spc_pack_{audio_format}"
    keyboard = [
        [
            InlineKeyboardButton("50", callback_data=f"{prefix}_50"),
            InlineKeyboardButton("100", callback_data=f"{prefix}_100"),
            InlineKeyboardButton(
                f"Całość ({selected_count})",
                callback_data=f"{prefix}_all",
            ),
        ],
        [InlineKeyboardButton("Wróć", callback_data="spc_pack_back")],
    ]
    return text, keyboard


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
