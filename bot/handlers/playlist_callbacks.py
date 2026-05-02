"""Playlist-oriented Telegram callback flows."""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.downloader_media import download_thumbnail
from bot.downloader_metadata import get_video_info
from bot.handlers.common_ui import build_main_keyboard, escape_md
from bot.security_limits import MAX_PLAYLIST_ITEMS, MAX_PLAYLIST_ITEMS_EXPANDED, TELEGRAM_UPLOAD_LIMIT_MB
from bot.security_policy import get_media_label
from bot.services.archive_service import execute_playlist_archive_flow
from bot.services.playlist_service import (
    build_playlist_message,
    build_single_video_url,
    download_playlist_item,
    load_playlist,
    parse_playlist_download_choice,
)
from bot.runtime import get_app_runtime, record_download_for
from bot.session_context import (
    clear_session_value as _clear_session_value,
    get_session_context_value as _get_session_context_value,
    get_session_value as _get_session_value,
    set_session_value as _set_session_value,
)
from bot.session_store import user_playlist_data, user_urls


_executor = ThreadPoolExecutor(max_workers=2)


async def handle_playlist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    query = update.callback_query
    chat_id = update.effective_chat.id

    if data == "pl_cancel":
        _clear_session_value(context, chat_id, "playlist_data", user_playlist_data)
        await query.edit_message_text("Pobieranie playlisty anulowane.")
        return

    if data == "pl_single":
        url = _get_session_value(context, chat_id, "current_url", user_urls)
        if url:
            clean_url = build_single_video_url(url)
            _clear_session_value(context, chat_id, "playlist_data", user_playlist_data)
            _set_session_value(context, chat_id, "current_url", clean_url, user_urls)

            media_name = get_media_label(_get_session_context_value(context, chat_id, "platform", legacy_key="platform"))
            await query.edit_message_text(f"Pobieranie informacji o {media_name}...")

            info = get_video_info(clean_url)
            if not info:
                await query.edit_message_text(f"Wystąpił błąd podczas pobierania informacji o {media_name}.")
                return

            title = info.get("title", "Nieznany tytuł")
            duration = info.get("duration", 0)
            duration_str = f"{duration // 60}:{duration % 60:02d}" if duration else "?"
            platform = _get_session_context_value(context, chat_id, "platform", legacy_key="platform", default="youtube")
            keyboard = build_main_keyboard(platform)

            await query.edit_message_text(
                f"*{escape_md(title)}*\nCzas trwania: {duration_str}\n\nWybierz format do pobrania:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )
        return

    if data == "pl_full":
        url = _get_session_value(context, chat_id, "current_url", user_urls)
        if url:
            await query.edit_message_text("Pobieranie informacji o playliście...")
            playlist_info = load_playlist(url, max_items=MAX_PLAYLIST_ITEMS)
            if not playlist_info or not playlist_info["entries"]:
                await query.edit_message_text("Nie udało się pobrać informacji o playliście.")
                return
            _set_session_value(context, chat_id, "playlist_data", playlist_info, user_playlist_data)
            runtime = get_app_runtime(context)
            archive_available = runtime.archive_available if runtime is not None else False
            msg, reply_markup = build_playlist_message(
                playlist_info, archive_available=archive_available,
            )
            await query.edit_message_text(msg, reply_markup=reply_markup, parse_mode="Markdown")
        return

    if data == "pl_more":
        url = _get_session_value(context, chat_id, "current_url", user_urls)
        if url:
            await query.edit_message_text("Pobieranie rozszerzonej listy...")
            playlist_info = load_playlist(url, max_items=MAX_PLAYLIST_ITEMS_EXPANDED)
            if not playlist_info or not playlist_info["entries"]:
                await query.edit_message_text("Nie udało się pobrać rozszerzonej listy.")
                return
            _set_session_value(context, chat_id, "playlist_data", playlist_info, user_playlist_data)
            runtime = get_app_runtime(context)
            archive_available = runtime.archive_available if runtime is not None else False
            msg, reply_markup = build_playlist_message(
                playlist_info, archive_available=archive_available,
            )
            await query.edit_message_text(msg, reply_markup=reply_markup, parse_mode="Markdown")
        return

    if data.startswith("pl_zip_dl_"):
        await _dispatch_archive_playlist(update, context, data)
        return

    if data.startswith("pl_dl_"):
        await download_playlist(update, context, data)


async def download_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE, callback_data: str):
    query = update.callback_query
    chat_id = update.effective_chat.id

    playlist = _get_session_value(context, chat_id, "playlist_data", user_playlist_data)
    if not playlist:
        await query.edit_message_text("Sesja playlisty wygasła. Wyślij link ponownie.")
        return

    entries = playlist["entries"]
    choice = parse_playlist_download_choice(callback_data)
    media_type = choice.media_type
    format_choice = choice.format_choice

    total = len(entries)
    succeeded = 0
    failed_titles = []

    await query.edit_message_text(
        f"Rozpoczynam pobieranie playlisty ({total} filmów)...\nFormat: {media_type} {format_choice}"
    )

    for i, entry in enumerate(entries, 1):
        entry_url = entry["url"]
        entry_title = entry.get("title", f"Film {i}")

        try:
            status_msg = await context.bot.send_message(chat_id=chat_id, text=f"[{i}/{total}] Pobieranie: {entry_title}...")
            await _download_single_playlist_item(context, chat_id, entry_url, entry_title, media_type, format_choice, status_msg)
            succeeded += 1
        except Exception as exc:
            failed_titles.append(entry_title)
            logging.error("Playlist item %d/%d failed: %s", i, total, exc)
            try:
                await status_msg.edit_text(f"[{i}/{total}] Błąd: {entry_title}\n{str(exc)[:100]}")
            except Exception:
                pass

        if i < total:
            await asyncio.sleep(1)

    failed = len(failed_titles)
    summary = f"Playlista zakończona!\n\nPobrano: {succeeded}/{total}\n"
    if failed:
        summary += f"Błędy: {failed}\n"
        for title in failed_titles[:5]:
            summary += f"  - {title[:40]}\n"

    await context.bot.send_message(chat_id=chat_id, text=summary)
    _clear_session_value(context, chat_id, "playlist_data", user_playlist_data)


async def _download_single_playlist_item(context, chat_id, url, title, media_type, format_choice, status_msg):
    try:
        result = await download_playlist_item(
            chat_id=chat_id,
            url=url,
            title=title,
            media_type=media_type,
            format_choice=format_choice,
            executor=_executor,
        )
    except RuntimeError:
        raise

    downloaded_file_path = result.file_path
    file_size_mb = result.file_size_mb
    chat_download_path = os.path.dirname(downloaded_file_path)

    use_mtproto = file_size_mb > TELEGRAM_UPLOAD_LIMIT_MB

    loop = asyncio.get_event_loop()
    item_info = await loop.run_in_executor(_executor, get_video_info, url)
    thumb_path = None
    if item_info:
        thumb_path = await loop.run_in_executor(_executor, download_thumbnail, item_info, chat_download_path, True)

    try:
        if use_mtproto:
            from bot.mtproto import mtproto_unavailability_reason, send_audio_mtproto, send_video_mtproto

            reason = mtproto_unavailability_reason()
            if reason is not None:
                raise RuntimeError(
                    f"Plik za duży dla Bot API ({file_size_mb:.0f} MB, limit: {TELEGRAM_UPLOAD_LIMIT_MB} MB).\n"
                    f"{reason}"
                )
            if media_type == "audio":
                ok = await send_audio_mtproto(chat_id, downloaded_file_path, title=title, caption=title[:200], thumb_path=thumb_path)
            else:
                ok = await send_video_mtproto(chat_id, downloaded_file_path, caption=title[:200], thumb_path=thumb_path)
            if not ok:
                raise RuntimeError("Wysyłanie pliku przez MTProto nie powiodło się.")
        else:
            with open(downloaded_file_path, "rb") as file_obj:
                thumb_file = open(thumb_path, "rb") if thumb_path else None
                try:
                    if media_type == "audio":
                        await context.bot.send_audio(
                            chat_id=chat_id,
                            audio=file_obj,
                            title=title,
                            caption=title[:200],
                            thumbnail=thumb_file,
                            read_timeout=120,
                            write_timeout=120,
                        )
                    else:
                        await context.bot.send_video(
                            chat_id=chat_id,
                            video=file_obj,
                            caption=title[:200],
                            thumbnail=thumb_file,
                            read_timeout=120,
                            write_timeout=120,
                        )
                finally:
                    if thumb_file:
                        thumb_file.close()
    finally:
        if thumb_path and os.path.exists(thumb_path):
            try:
                os.remove(thumb_path)
            except OSError:
                pass
        try:
            os.remove(downloaded_file_path)
        except OSError:
            pass

    record_download_for(context, chat_id, title, url, f"{media_type}_{format_choice}", file_size_mb)
    await status_msg.edit_text(f"[✅] {title}")


async def _dispatch_archive_playlist(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    callback_data: str,
) -> None:
    """Dispatch a `pl_zip_dl_*` callback to the archive (7z) flow."""

    chat_id = update.effective_chat.id
    playlist = _get_session_value(context, chat_id, "playlist_data", user_playlist_data)
    if not playlist:
        await update.callback_query.edit_message_text(
            "Sesja playlisty wygasła. Wyślij link ponownie."
        )
        return

    choice = parse_playlist_download_choice(callback_data)

    await execute_playlist_archive_flow(
        update,
        context,
        chat_id=chat_id,
        playlist=playlist,
        media_type=choice.media_type,
        format_choice=choice.format_choice,
        executor=_executor,
    )
    _clear_session_value(context, chat_id, "playlist_data", user_playlist_data)
