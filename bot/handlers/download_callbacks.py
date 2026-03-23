"""Download-oriented Telegram callback flows."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import yt_dlp
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.ext import ContextTypes

from bot.config import DOWNLOAD_PATH, get_runtime_value
from bot.handlers.common_ui import (
    build_main_keyboard,
    escape_md,
    format_bytes,
    format_eta,
    safe_edit_message,
    send_long_message,
)
from bot.security import (
    MAX_FILE_SIZE_MB,
    MAX_PLAYLIST_ITEMS,
    MAX_PLAYLIST_ITEMS_EXPANDED,
    get_media_label,
    normalize_url,
    user_playlist_data,
    user_time_ranges,
    user_urls,
)
from bot.session_context import (
    clear_session_context_value as _clear_session_context_value,
    clear_session_value as _clear_session_value,
    get_session_context_value as _get_session_context_value,
    get_session_value as _get_session_value,
    set_session_value as _set_session_value,
)
from bot.session_store import download_progress as _download_progress
from bot.transcription_limits import CORRECTION_DURATION_LIMIT_MIN, SUMMARY_DURATION_LIMIT_MIN
from bot.downloader_media import COOKIES_FILE, download_photo, download_thumbnail
from bot.downloader_metadata import get_video_info
from bot.downloader_subtitles import get_available_subtitles
from bot.downloader_validation import sanitize_filename
from bot.services.download_service import (
    ensure_size_within_limit,
    estimate_download_size,
    execute_download,
    prepare_download_plan,
)
from bot.services.playlist_service import (
    build_playlist_message,
    build_single_video_url,
    download_playlist_item,
    load_playlist,
    parse_playlist_download_choice,
)
from bot.services.spotify_service import download_resolved_audio
from bot.services.transcription_service import (
    cleanup_transcription_artifacts,
    generate_summary_artifact,
    load_transcript_result,
    run_transcription_with_progress,
    transcript_too_long_for_summary,
)
from bot.runtime import record_download_for


_executor = ThreadPoolExecutor(max_workers=2)

def create_progress_hook(chat_id):
    """Creates a progress hook for yt-dlp that updates shared progress state."""

    def hook(d):
        if d["status"] == "downloading":
            _download_progress[chat_id] = {
                "status": "downloading",
                "percent": d.get("_percent_str", "?%").strip(),
                "downloaded": d.get("downloaded_bytes", 0),
                "total": d.get("total_bytes") or d.get("total_bytes_estimate", 0),
                "speed": d.get("speed", 0),
                "eta": d.get("eta", None),
                "filename": d.get("filename", ""),
                "updated": time.time(),
            }
        elif d["status"] == "finished":
            _download_progress[chat_id] = {
                "status": "finished",
                "percent": "100%",
                "downloaded": d.get("downloaded_bytes", 0),
                "total": d.get("total_bytes", 0),
                "filename": d.get("filename", ""),
                "updated": time.time(),
            }
        elif d["status"] == "error":
            _download_progress[chat_id] = {
                "status": "error",
                "updated": time.time(),
            }

    return hook


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
        }
        if os.path.exists(COOKIES_FILE):
            ydl_opts["cookiefile"] = COOKIES_FILE

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                _executor,
                lambda opts=ydl_opts, target_url=video_url: yt_dlp.YoutubeDL(opts).download([target_url]),
            )

            downloaded = None
            for filename in os.listdir(download_path):
                full_path = os.path.join(download_path, filename)
                if filename.startswith(f"{current_date} {safe_title}") and os.path.isfile(full_path):
                    downloaded = full_path
                    break

            if downloaded:
                file_size = os.path.getsize(downloaded) / (1024 * 1024)
                if file_size > 50:
                    await safe_edit_message(query, f"Film {i + 1} za duży ({file_size:.0f} MB, limit: 50 MB).")
                    os.remove(downloaded)
                    continue

                with open(downloaded, "rb") as file_obj:
                    await context.bot.send_video(
                        chat_id=chat_id,
                        video=file_obj,
                        caption=f"{title} ({i + 1}/{len(video_entries)})"[:200],
                        read_timeout=120,
                        write_timeout=120,
                    )
                os.remove(downloaded)
                sent_count += 1
        except Exception as exc:
            logging.error("Error downloading Instagram video %d: %s", i + 1, exc)

    total = len(video_entries)
    if sent_count == total:
        await safe_edit_message(query, f"Wysłano {total} filmów!")
    elif sent_count > 0:
        await safe_edit_message(query, f"Wysłano {sent_count} z {total} filmów.")
    else:
        await safe_edit_message(query, "Nie udało się wysłać żadnego filmu.")
    _clear_session_context_value(context, chat_id, "instagram_carousel", legacy_key="ig_carousel")


async def download_file(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    type,
    format,
    url,
    transcribe=False,
    summary=False,
    summary_type=None,
    use_format_id=False,
    audio_quality="192",
):
    media_type = type
    query = update.callback_query
    chat_id = update.effective_chat.id
    title = "Unknown"
    success_recorded = False

    async def update_status(text):
        await safe_edit_message(query, text)

    media_name = get_media_label(_get_session_context_value(context, chat_id, "platform", legacy_key="platform"))
    await update_status(f"Pobieranie informacji o {media_name}...")

    chat_download_path = os.path.join(DOWNLOAD_PATH, str(chat_id))
    os.makedirs(chat_download_path, exist_ok=True)

    time_range = _get_session_value(context, chat_id, "time_range", user_time_ranges)
    try:
        plan = prepare_download_plan(
            url=url,
            media_type=media_type,
            format_choice=format,
            chat_download_path=chat_download_path,
            time_range=time_range,
            transcribe=transcribe,
            use_format_id=use_format_id,
            audio_quality=audio_quality,
        )
    except ValueError:
        await update_status("Nieobsługiwana jakość audio. Spróbuj zmienić opcję.")
        return

    if not plan:
        await update_status(f"Wystąpił błąd podczas pobierania informacji o {media_name}.")
        return

    info = plan.info
    title = plan.title
    duration_str = plan.duration_str
    sanitized_title = plan.sanitized_title

    try:
        await update_status(f"Sprawdzanie rozmiaru pliku...\n({duration_str})")
        size_mb = await asyncio.get_event_loop().run_in_executor(_executor, lambda: estimate_download_size(plan))
        if not ensure_size_within_limit(size_mb, max_size_mb=MAX_FILE_SIZE_MB):
            await update_status(
                f"Wybrany format jest zbyt duży!\n\n"
                f"Rozmiar: {size_mb:.1f} MB\n"
                f"Maksymalny dozwolony rozmiar: {MAX_FILE_SIZE_MB} MB\n\n"
                f"Spróbuj wybrać niższą jakość lub pobierz tylko audio."
            )
            return

        time_range_info = ""
        if time_range:
            time_range_info = f"\n✂️ Zakres: {time_range['start']} - {time_range['end']}"
        await update_status(f"Rozpoczynam pobieranie...\nCzas trwania: {duration_str}{time_range_info}")
        download_result = await execute_download(
            plan,
            chat_id=chat_id,
            executor=_executor,
            progress_hook_factory=create_progress_hook,
            progress_state=_download_progress,
            status_callback=update_status,
            format_bytes=format_bytes,
            format_eta=format_eta,
        )
        downloaded_file_path = download_result.file_path
        file_size_mb = download_result.file_size_mb

        if transcribe:
            await update_status(
                f"Pobieranie zakończone ({file_size_mb:.1f} MB).\n\n"
                f"Rozpoczynanie transkrypcji audio...\nTo może potrwać kilka minut."
            )

            if not get_runtime_value("GROQ_API_KEY", ""):
                await update_status(
                    "Funkcja niedostępna — brak klucza API do transkrypcji.\n"
                    "Skontaktuj się z administratorem."
                )
                return

            transcript_path = await run_transcription_with_progress(
                source_path=downloaded_file_path,
                output_dir=chat_download_path,
                executor=_executor,
                status_callback=lambda status: update_status(f"Transkrypcja w toku...\n\n{status}"),
            )

            if not transcript_path or not os.path.exists(transcript_path):
                await update_status("Wystąpił błąd podczas transkrypcji.")
                return

            transcript_result = load_transcript_result(transcript_path)

            if summary:
                if not get_runtime_value("CLAUDE_API_KEY", ""):
                    await update_status(
                        "Funkcja niedostępna — brak klucza API do podsumowań.\n"
                        "Skontaktuj się z administratorem."
                    )
                    return

                transcript_text = transcript_result.display_text
                if transcript_too_long_for_summary(transcript_text):
                    await update_status(
                        "Transkrypcja zakończona, ale tekst jest zbyt długi na podsumowanie AI.\n\n"
                        "Wysyłam samą transkrypcję."
                    )
                    with open(transcript_path, "rb") as file_obj:
                        await context.bot.send_document(
                            chat_id=chat_id,
                            document=file_obj,
                            filename=os.path.basename(transcript_path),
                            caption=f"Transkrypcja: {title} (podsumowanie pominięte — tekst zbyt długi)",
                            read_timeout=60,
                            write_timeout=60,
                        )
                    record_download_for(context, chat_id, title, url, "transcription", file_size_mb, time_range, selected_format=format)
                    success_recorded = True
                    return

                await update_status("Transkrypcja zakończona.\n\nGeneruję podsumowanie AI...\nTo może potrwać około minuty.")
                summary_result = await generate_summary_artifact(
                    transcript_text=transcript_text,
                    summary_type=summary_type,
                    title=title,
                    sanitized_title=sanitized_title,
                    output_dir=chat_download_path,
                    executor=_executor,
                )
                if not summary_result:
                    await update_status("Wystąpił błąd podczas generowania podsumowania.")
                    return

                await update_status("Podsumowanie wygenerowane.\n\nWysyłanie wyników...")
                await send_long_message(
                    context.bot,
                    chat_id,
                    summary_result.summary_text,
                    header=f"*{escape_md(title)} - {summary_result.summary_type_name}*\n\n",
                )
                await update_status("Wysyłanie pliku z pełną transkrypcją...")
                with open(transcript_path, "rb") as file_obj:
                    await context.bot.send_document(
                        chat_id=chat_id,
                        document=file_obj,
                        filename=os.path.basename(transcript_path),
                        caption=f"Pełna transkrypcja: {title}",
                        read_timeout=60,
                        write_timeout=60,
                    )
                record_download_for(
                    context,
                    chat_id,
                    title,
                    url,
                    f"transcription_summary_{summary_type}",
                    file_size_mb,
                    time_range,
                    selected_format=format,
                )
                success_recorded = True
                await update_status("Transkrypcja i podsumowanie zostały wysłane!")
            else:
                await update_status("Transkrypcja zakończona.\n\nWysyłanie transkrypcji...")
                display_text = transcript_result.display_text
                if len(display_text) <= 30000:
                    await send_long_message(
                        context.bot,
                        chat_id,
                        display_text,
                        header=f"*Transkrypcja: {escape_md(title)}*\n\n",
                    )
                with open(transcript_path, "rb") as file_obj:
                    await context.bot.send_document(
                        chat_id=chat_id,
                        document=file_obj,
                        filename=os.path.basename(transcript_path),
                        caption=(
                            f"Transkrypcja: {title}"
                            if len(display_text) <= 30000
                            else f"Transkrypcja: {title} ({len(display_text):,} znaków — tylko plik)"
                        ),
                        read_timeout=60,
                        write_timeout=60,
                    )
                try:
                    cleanup_transcription_artifacts(
                        source_media_path=downloaded_file_path,
                        output_dir=chat_download_path,
                        transcript_prefix=sanitized_title,
                    )
                except Exception as exc:
                    logging.error("Error deleting files: %s", exc)
                record_download_for(context, chat_id, title, url, "transcription", file_size_mb, time_range, selected_format=format)
                success_recorded = True
                await update_status("Transkrypcja została wysłana!")
        else:
            await update_status(f"Pobieranie zakończone ({file_size_mb:.1f} MB).\n\nWysyłanie pliku do Telegram...")
            thumb_path = await asyncio.get_event_loop().run_in_executor(_executor, download_thumbnail, info, chat_download_path, True)
            try:
                with open(downloaded_file_path, "rb") as file_obj:
                    thumb_file = open(thumb_path, "rb") if thumb_path else None
                    try:
                        if media_type == "audio":
                            await context.bot.send_audio(
                                chat_id=chat_id,
                                audio=file_obj,
                                title=title,
                                caption=title,
                                thumbnail=thumb_file,
                                read_timeout=60,
                                write_timeout=60,
                            )
                        else:
                            await context.bot.send_video(
                                chat_id=chat_id,
                                video=file_obj,
                                caption=title,
                                thumbnail=thumb_file,
                                read_timeout=60,
                                write_timeout=60,
                            )
                    finally:
                        if thumb_file:
                            thumb_file.close()
            finally:
                if thumb_path and os.path.exists(thumb_path):
                    os.remove(thumb_path)

            os.remove(downloaded_file_path)
            record_download_for(context, chat_id, title, url, f"{media_type}_{format}", file_size_mb, time_range, selected_format=format)
            success_recorded = True
            await update_status("Plik został wysłany!")
    except Exception as exc:
        if not success_recorded:
            record_download_for(
                context,
                chat_id,
                title,
                url,
                f"{media_type}_{format}",
                status="failure",
                selected_format=format,
                error_message=str(exc),
            )
        logging.error("Error in download_file: %s", exc)

        error_str = str(exc).lower()
        if any(keyword in error_str for keyword in ("login", "sign in", "cookie", "authentication")):
            await update_status(
                "Ta platforma wymaga zalogowania.\n\n"
                "Aby pobrać treści z ograniczonym dostępem:\n"
                "1. Zaloguj się na platformę w przeglądarce\n"
                "2. Wyeksportuj cookies (rozszerzenie 'Get cookies.txt LOCALLY')\n"
                "3. Umieść plik cookies.txt w katalogu bota\n"
                "4. Spróbuj ponownie"
            )
        else:
            await update_status("Wystąpił błąd podczas pobierania. Spróbuj ponownie.")


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
            msg, reply_markup = build_playlist_message(playlist_info)
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
            msg, reply_markup = build_playlist_message(playlist_info)
            await query.edit_message_text(msg, reply_markup=reply_markup, parse_mode="Markdown")
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

    if file_size_mb > 50:
        raise RuntimeError(f"Plik za duży do wysłania ({file_size_mb:.0f} MB, limit Telegram: 50 MB)")

    loop = asyncio.get_event_loop()
    item_info = await loop.run_in_executor(_executor, get_video_info, url)
    thumb_path = None
    if item_info:
        thumb_path = await loop.run_in_executor(_executor, download_thumbnail, item_info, chat_download_path, True)

    try:
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


async def download_spotify_resolved(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    resolved: dict,
    audio_format: str = "mp3",
    transcribe: bool = False,
    summary: bool = False,
    summary_type: int | None = None,
):
    query = update.callback_query
    chat_id = update.effective_chat.id
    title = resolved.get("title", "Podcast episode")

    async def update_status(text):
        await safe_edit_message(query, text)

    await update_status("Pobieranie odcinka podcastu...")
    chat_download_path = os.path.join(DOWNLOAD_PATH, str(chat_id))
    os.makedirs(chat_download_path, exist_ok=True)

    source = resolved["source"]
    downloaded_file_path = None

    try:
        await update_status("Pobieranie audio z iTunes..." if source == "itunes" else "Pobieranie audio z YouTube...")
        downloaded_file_path = await download_resolved_audio(
            resolved=resolved,
            audio_format=audio_format,
            output_dir=chat_download_path,
            executor=_executor,
        )
        if not downloaded_file_path:
            await update_status("Nie udało się pobrać pliku audio.")
            return

        file_size_mb = os.path.getsize(downloaded_file_path) / (1024 * 1024)

        if transcribe:
            await update_status(
                f"Pobieranie zakończone ({file_size_mb:.1f} MB).\n\nRozpoczynanie transkrypcji audio...\nTo może potrwać kilka minut."
            )
            if not get_runtime_value("GROQ_API_KEY", ""):
                await update_status(
                    "Funkcja niedostępna — brak klucza API do transkrypcji.\nSkontaktuj się z administratorem."
                )
                return

            transcript_path = await run_transcription_with_progress(
                source_path=downloaded_file_path,
                output_dir=chat_download_path,
                executor=_executor,
                status_callback=update_status,
            )
            if not transcript_path or not os.path.exists(transcript_path):
                await update_status("Wystąpił błąd podczas transkrypcji.")
                return

            transcript_result = load_transcript_result(transcript_path)
            transcript_text = transcript_result.display_text
            sanitized_title = os.path.splitext(os.path.basename(downloaded_file_path))[0]

            if summary and summary_type:
                if not get_runtime_value("CLAUDE_API_KEY", ""):
                    await update_status(
                        "Transkrypcja zakończona.\n\nPodsumowanie niedostępne — brak klucza CLAUDE_API_KEY.\nWysyłam samą transkrypcję."
                    )
                elif transcript_too_long_for_summary(transcript_text):
                    await update_status(
                        "Transkrypcja zakończona, ale tekst jest zbyt długi na podsumowanie AI.\n\nWysyłam samą transkrypcję."
                    )
                else:
                    await update_status("Transkrypcja zakończona.\n\nGeneruję podsumowanie AI...\nTo może potrwać około minuty.")
                    summary_result = await generate_summary_artifact(
                        transcript_text=transcript_text,
                        summary_type=summary_type,
                        title=title,
                        sanitized_title=sanitized_title,
                        output_dir=chat_download_path,
                        executor=_executor,
                    )
                    if summary_result:
                        await send_long_message(
                            context.bot,
                            chat_id,
                            summary_result.summary_text,
                            header=f"*Podsumowanie: {escape_md(title)}*\n\n",
                        )

            await update_status("Wysyłanie pliku z transkrypcją...")
            with open(transcript_path, "rb") as file_obj:
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=file_obj,
                    filename=os.path.basename(transcript_path),
                    caption=f"Transkrypcja: {title}"[:200],
                    read_timeout=60,
                    write_timeout=60,
                )

            record_download_for(
                context,
                chat_id,
                title,
                _get_session_value(context, chat_id, "current_url", user_urls) or "",
                "spotify_transcribe",
                file_size_mb,
            )
            _clear_session_context_value(context, chat_id, "spotify_resolved", legacy_key="spotify_resolved")
            cleanup_transcription_artifacts(
                source_media_path=downloaded_file_path,
                output_dir=chat_download_path,
                transcript_prefix=sanitized_title,
            )
            downloaded_file_path = None
        else:
            await update_status(f"Wysyłanie pliku ({file_size_mb:.1f} MB)...")
            with open(downloaded_file_path, "rb") as file_obj:
                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=file_obj,
                    title=title,
                    caption=title[:200],
                    read_timeout=120,
                    write_timeout=120,
                )
            record_download_for(
                context,
                chat_id,
                title,
                _get_session_value(context, chat_id, "current_url", user_urls) or "",
                f"spotify_audio_{audio_format}",
                file_size_mb,
            )
            _clear_session_context_value(context, chat_id, "spotify_resolved", legacy_key="spotify_resolved")

        await update_status(f"Gotowe: {title}")
    except Exception as exc:
        logging.error("Error downloading Spotify episode: %s", exc)
        await update_status(f"Błąd pobierania: {str(exc)[:200]}")
    finally:
        if downloaded_file_path and os.path.exists(downloaded_file_path):
            try:
                os.remove(downloaded_file_path)
            except OSError:
                pass


async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
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
