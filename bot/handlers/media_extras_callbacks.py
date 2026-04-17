"""Media-extra Telegram callback flows outside the core download pipeline."""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import yt_dlp
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.ext import ContextTypes

from bot.config import DOWNLOAD_PATH, YTDLP_REMOTE_COMPONENTS
from bot.downloader_media import COOKIES_FILE, download_photo
from bot.downloader_metadata import get_video_info
from bot.downloader_validation import sanitize_filename
from bot.handlers.common_ui import escape_md, safe_edit_message
from bot.runtime import record_download_for
from bot.security_policy import get_media_label
from bot.session_context import (
    clear_session_context_value as _clear_session_context_value,
    get_session_context_value as _get_session_context_value,
    get_session_value as _get_session_value,
)
from bot.session_store import user_urls


_executor = ThreadPoolExecutor(max_workers=2)


async def _handle_instagram_download(update: Update, context: ContextTypes.DEFAULT_TYPE, url, callback_data: str):
    query = update.callback_query
    chat_id = update.effective_chat.id

    carousel = _get_session_context_value(context, chat_id, "instagram_carousel", legacy_key="ig_carousel")
    if not carousel:
        await safe_edit_message(query, "Sesja wygasła. Wyślij link ponownie.")
        return

    chat_download_path = os.path.join(DOWNLOAD_PATH, str(chat_id))
    os.makedirs(chat_download_path, exist_ok=True)

    title = carousel.get("title", "Instagram post")
    photos = carousel.get("photos", [])
    videos = carousel.get("videos", [])

    if callback_data == "dl_ig_photos":
        await _download_and_send_ig_photos(update, context, photos, title, chat_download_path)
    elif callback_data == "dl_ig_videos":
        await _download_and_send_ig_videos(update, context, videos, title, url, chat_download_path)
    elif callback_data == "dl_ig_all":
        if photos:
            await _download_and_send_ig_photos(update, context, photos, title, chat_download_path)
        if videos:
            await _download_and_send_ig_videos(update, context, videos, title, url, chat_download_path)


async def _download_and_send_ig_photos(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    photo_entries: list,
    title: str,
    download_path: str,
):
    query = update.callback_query
    chat_id = update.effective_chat.id
    count = len(photo_entries)

    await safe_edit_message(query, f"Pobieranie {'zdjęcia' if count == 1 else f'{count} zdjęć'}...")

    downloaded_paths = []
    loop = asyncio.get_event_loop()
    current_date = datetime.now().strftime("%Y-%m-%d")

    for i, entry in enumerate(photo_entries):
        photo_url = entry.get("url", "")
        if not photo_url:
            thumbs = entry.get("thumbnails", [])
            if thumbs:
                photo_url = thumbs[-1].get("url", "")
        if not photo_url:
            logging.warning("No URL for Instagram photo entry %d", i)
            continue

        safe_title = sanitize_filename(f"{title}_{i + 1}")
        output = os.path.join(download_path, f"{current_date} {safe_title}")
        path = await loop.run_in_executor(_executor, download_photo, photo_url, output)
        if path:
            downloaded_paths.append(path)

    if not downloaded_paths:
        await safe_edit_message(query, "Nie udało się pobrać zdjęć.")
        return

    try:
        if len(downloaded_paths) == 1:
            with open(downloaded_paths[0], "rb") as file_obj:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=file_obj,
                    caption=title[:200],
                    read_timeout=60,
                    write_timeout=60,
                )
        else:
            for batch_start in range(0, len(downloaded_paths), 10):
                batch = downloaded_paths[batch_start:batch_start + 10]
                file_handles = []
                media_group = []

                for j, path in enumerate(batch):
                    file_handle = open(path, "rb")
                    file_handles.append(file_handle)
                    caption = title[:200] if (batch_start + j) == 0 else None
                    media_group.append(InputMediaPhoto(media=file_handle, caption=caption))

                try:
                    await context.bot.send_media_group(
                        chat_id=chat_id,
                        media=media_group,
                        read_timeout=120,
                        write_timeout=120,
                    )
                finally:
                    for file_handle in file_handles:
                        file_handle.close()

                if batch_start + 10 < len(downloaded_paths):
                    await asyncio.sleep(1)

        total_size = sum(os.path.getsize(path) for path in downloaded_paths) / (1024 * 1024)
        record_download_for(
            context,
            chat_id,
            title,
            _get_session_value(context, chat_id, "current_url", user_urls) or "",
            "photo",
            total_size,
        )
        await safe_edit_message(query, f"Wysłano {len(downloaded_paths)} zdjęć!")
    except Exception as exc:
        logging.error("Error sending Instagram photos: %s", exc)
        await safe_edit_message(query, "Błąd podczas wysyłania zdjęć.")
    finally:
        _clear_session_context_value(context, chat_id, "instagram_carousel", legacy_key="ig_carousel")
        for path in downloaded_paths:
            try:
                os.remove(path)
            except OSError:
                pass


async def _download_and_send_ig_videos(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    video_entries: list,
    title: str,
    url: str,
    download_path: str,
):
    query = update.callback_query
    chat_id = update.effective_chat.id
    sent_count = 0

    for i, entry in enumerate(video_entries):
        video_url = entry.get("url") or entry.get("webpage_url", "")
        if not video_url:
            continue

        await safe_edit_message(query, f"Pobieranie filmu {i + 1}/{len(video_entries)}...")

        safe_title = sanitize_filename(f"{title}_video_{i + 1}")
        current_date = datetime.now().strftime("%Y-%m-%d")
        output_path = os.path.join(download_path, f"{current_date} {safe_title}")

        ydl_opts = {
            "outtmpl": f"{output_path}.%(ext)s",
            "format": "best",
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "remote_components": YTDLP_REMOTE_COMPONENTS,
        }
        if os.path.exists(COOKIES_FILE):
            ydl_opts["cookiefile"] = COOKIES_FILE

        try:
            loop = asyncio.get_event_loop()
            downloaded_file = await loop.run_in_executor(
                _executor,
                _download_instagram_video_file,
                ydl_opts,
                video_url,
                output_path,
            )
            if not downloaded_file:
                continue

            file_size_mb = os.path.getsize(downloaded_file) / (1024 * 1024)
            if file_size_mb > 50:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"Film {i + 1} jest za duży do wysłania ({file_size_mb:.1f} MB).",
                )
                continue

            with open(downloaded_file, "rb") as file_obj:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=file_obj,
                    caption=f"{title[:180]} ({i + 1}/{len(video_entries)})",
                    read_timeout=120,
                    write_timeout=120,
                )
            sent_count += 1
            record_download_for(context, chat_id, f"{title} #{i + 1}", url, "instagram_video", file_size_mb)
        except Exception as exc:
            logging.error("Error sending Instagram video %d: %s", i + 1, exc)
        finally:
            for path in _instagram_video_candidates(output_path):
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass

    _clear_session_context_value(context, chat_id, "instagram_carousel", legacy_key="ig_carousel")
    if sent_count:
        await safe_edit_message(query, f"Wysłano {sent_count} filmów!")
    else:
        await safe_edit_message(query, "Nie udało się wysłać filmów.")


def _download_instagram_video_file(ydl_opts: dict, video_url: str, output_path: str) -> str | None:
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([video_url])
    for path in _instagram_video_candidates(output_path):
        if os.path.exists(path):
            return path
    return None


def _instagram_video_candidates(output_path: str) -> list[str]:
    return [
        f"{output_path}.mp4",
        f"{output_path}.mkv",
        f"{output_path}.webm",
        f"{output_path}.mov",
    ]


async def handle_formats_list(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    query = update.callback_query
    info = get_video_info(url)
    if not info:
        chat_id = update.effective_chat.id
        media_name = get_media_label(_get_session_context_value(context, chat_id, "platform", legacy_key="platform"))
        await query.edit_message_text(f"Wystąpił błąd podczas pobierania informacji o {media_name}.")
        return

    title = info.get("title", "Nieznany tytuł")
    video_formats = []
    audio_formats = []

    for format_item in info.get("formats", []):
        format_id = format_item.get("format_id", "N/A")
        ext = format_item.get("ext", "N/A")
        resolution = format_item.get("resolution", "N/A")

        if format_item.get("vcodec") == "none":
            if len(audio_formats) < 5:
                audio_formats.append({"id": format_id, "desc": f"{format_id}: {ext}, {resolution}"})
        else:
            if len(video_formats) < 5:
                video_formats.append({"id": format_id, "desc": f"{format_id}: {ext}, {resolution}"})

    keyboard = []
    for format_item in video_formats:
        keyboard.append([InlineKeyboardButton(f"Video {format_item['desc']}", callback_data=f"dl_video_{format_item['id']}")])
    for format_item in audio_formats:
        keyboard.append([InlineKeyboardButton(f"Audio {format_item['desc']}", callback_data=f"dl_audio_format_{format_item['id']}")])
    keyboard.append([InlineKeyboardButton("Powrót", callback_data="back")])

    await safe_edit_message(
        query,
        f"Formaty dla: {title}\n\nWybierz format:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def _show_spotify_summary_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = update.effective_chat.id
    resolved = _get_session_context_value(context, chat_id, "spotify_resolved", legacy_key="spotify_resolved", default={})
    title = resolved.get("title", "Odcinek podcastu")
    keyboard = [
        [InlineKeyboardButton("1. Krótkie podsumowanie", callback_data="summary_option_1")],
        [InlineKeyboardButton("2. Szczegółowe podsumowanie", callback_data="summary_option_2")],
        [InlineKeyboardButton("3. Podsumowanie w punktach", callback_data="summary_option_3")],
        [InlineKeyboardButton("4. Podział na zadania", callback_data="summary_option_4")],
    ]
    await safe_edit_message(
        query,
        f"*{escape_md(title)}*\n\nWybierz rodzaj podsumowania:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
