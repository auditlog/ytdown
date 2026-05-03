"""Download-oriented Telegram callback flows."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yt_dlp
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.ext import ContextTypes

from bot.config import DOWNLOAD_PATH, YTDLP_REMOTE_COMPONENTS, get_runtime_value
from bot.handlers.common_ui import (
    build_main_keyboard,
    escape_md,
    format_bytes,
    format_eta,
    safe_edit_message,
    send_long_message,
)
from bot.security_limits import MAX_FILE_SIZE_MB, MAX_PLAYLIST_ITEMS, MAX_PLAYLIST_ITEMS_EXPANDED, TELEGRAM_UPLOAD_LIMIT_MB
from bot.security_policy import get_media_label, normalize_url
from bot.session_context import (
    clear_session_context_value as _clear_session_context_value,
    clear_session_value as _clear_session_value,
    get_session_context_value as _get_session_context_value,
    get_session_value as _get_session_value,
    set_session_value as _set_session_value,
)
from bot.session_store import (
    ArchiveJobState,
    archived_deliveries,
    download_progress as _download_progress,
    pending_archive_jobs,
    user_playlist_data,
    user_time_ranges,
    user_urls,
)
from bot.archive import is_7z_available, volume_size_for
from bot.mtproto import mtproto_unavailability_reason as _mtproto_unavailability_reason
from bot.services.archive_service import (
    execute_single_file_archive_flow,
    register_pending_archive_job,
    send_volumes,
)
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
from bot.handlers.media_extras_callbacks import (
    _handle_instagram_download as _extracted_handle_instagram_download,
    _show_spotify_summary_options as _extracted_show_spotify_summary_options,
    handle_formats_list as _extracted_handle_formats_list,
)
from bot.handlers.playlist_callbacks import (
    download_playlist as _extracted_download_playlist,
    handle_playlist_callback as _extracted_handle_playlist_callback,
)
from bot.handlers.spotify_callbacks import (
    download_spotify_resolved,
)
from bot.handlers.time_range_callbacks import (
    apply_time_range_preset,
    back_to_main_menu,
    show_time_range_options,
)
from bot.platforms import get_platform

GENERIC_COOKIES_HINT = (
    "Ta platforma wymaga zalogowania.\n\n"
    "Aby pobrać treści z ograniczonym dostępem:\n"
    "1. Zaloguj się na platformę w przeglądarce\n"
    "2. Wyeksportuj cookies (rozszerzenie 'Get cookies.txt LOCALLY')\n"
    "3. Umieść plik cookies.txt w katalogu bota\n"
    "4. Spróbuj ponownie"
)

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
    return await _extracted_handle_instagram_download(update, context, url, callback_data)


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
                    try:
                        os.remove(downloaded)
                    except OSError:
                        pass
                    continue

                with open(downloaded, "rb") as file_obj:
                    await context.bot.send_video(
                        chat_id=chat_id,
                        video=file_obj,
                        caption=f"{title} ({i + 1}/{len(video_entries)})"[:200],
                        read_timeout=120,
                        write_timeout=120,
                    )
                try:
                    os.remove(downloaded)
                except OSError:
                    pass
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
            use_mtproto = file_size_mb > TELEGRAM_UPLOAD_LIMIT_MB

            # Detect files that exceed the active Telegram transport limit.
            # When too large, offer the 7z archive split instead of failing.
            transport_limit_mb = volume_size_for(
                use_mtproto=_mtproto_unavailability_reason() is None
            )
            if file_size_mb > transport_limit_mb and is_7z_available():
                await _offer_archive_or_cancel(
                    update,
                    context,
                    chat_id=chat_id,
                    file_path=downloaded_file_path,
                    title=title,
                    media_type=media_type,
                    format_choice=format,
                    file_size_mb=file_size_mb,
                )
                # The archive flow now owns the file; skip cleanup.
                success_recorded = True
                return

            method_label = " (MTProto)" if use_mtproto else ""
            await update_status(f"Pobieranie zakończone ({file_size_mb:.1f} MB).\n\nWysyłanie pliku do Telegram...{method_label}")
            thumb_path = await asyncio.get_event_loop().run_in_executor(_executor, download_thumbnail, info, chat_download_path, True)
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
                        ok = await send_audio_mtproto(chat_id, downloaded_file_path, title=title, caption=title, thumb_path=thumb_path)
                    else:
                        ok = await send_video_mtproto(chat_id, downloaded_file_path, caption=title, thumb_path=thumb_path)
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
                    try:
                        os.remove(thumb_path)
                    except OSError:
                        pass

            try:
                os.remove(downloaded_file_path)
            except OSError:
                pass
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
            platform_name = _get_session_context_value(
                context, chat_id, "platform", legacy_key="platform"
            )
            config = get_platform(platform_name)
            hint = (
                config.cookies_hint
                if config and config.cookies_hint
                else GENERIC_COOKIES_HINT
            )
            await update_status(hint)
        else:
            await update_status("Wystąpił błąd podczas pobierania. Spróbuj ponownie.")


async def handle_formats_list(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    return await _extracted_handle_formats_list(update, context, url)


async def handle_playlist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    return await _extracted_handle_playlist_callback(update, context, data)


async def download_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE, callback_data: str):
    return await _extracted_download_playlist(update, context, callback_data)


async def _show_spotify_summary_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _extracted_show_spotify_summary_options(update, context)


async def _offer_archive_or_cancel(
    update,
    context,
    *,
    chat_id: int,
    file_path: str,
    title: str,
    media_type: str,
    format_choice: str,
    file_size_mb: float,
) -> None:
    """Register a pending archive job and present [Wyślij jako 7z]/[Anuluj]."""

    use_mtproto = _mtproto_unavailability_reason() is None
    volume_size_mb = volume_size_for(use_mtproto)
    state = ArchiveJobState(
        file_path=Path(file_path),
        title=title,
        media_type=media_type,
        format_choice=format_choice,
        file_size_mb=file_size_mb,
        use_mtproto=use_mtproto,
        created_at=datetime.now(),
    )
    token = register_pending_archive_job(chat_id, state)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Wyślij jako 7z", callback_data=f"arc_split_{token}")],
        [InlineKeyboardButton("Anuluj", callback_data=f"arc_cancel_{token}")],
    ])
    text = (
        f"Plik za duży dla Telegrama: {file_size_mb:.0f} MB > limit {volume_size_mb} MB.\n"
        f"Mogę spakować go w wolumeny 7z (po {volume_size_mb} MB) i wysłać paczki."
    )
    try:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    except Exception as exc:
        logging.debug("offer-archive edit failed: %s", exc)


async def handle_archive_callback(update, context, data: str) -> None:
    """Dispatch arc_split_/arc_cancel_/arc_pack_partial_/arc_purge_partial_/arc_resend_/arc_purge_ callbacks."""

    chat_id = update.effective_chat.id

    if data.startswith("arc_split_"):
        token = data[len("arc_split_"):]
        await execute_single_file_archive_flow(
            update, context, chat_id=chat_id, token=token,
        )
        return

    if data.startswith("arc_cancel_"):
        token = data[len("arc_cancel_"):]
        await _handle_arc_cancel(update, chat_id, token)
        return

    # arc_pack_partial_ and arc_purge_partial_ must be matched before arc_purge_
    # to avoid the shorter prefix swallowing the longer one.
    if data.startswith("arc_pack_partial_"):
        token = data[len("arc_pack_partial_"):]
        from bot.services.archive_service import execute_partial_archive_flow
        await execute_partial_archive_flow(
            update, context, chat_id=chat_id, token=token,
        )
        return

    if data.startswith("arc_purge_partial_"):
        token = data[len("arc_purge_partial_"):]
        await _handle_arc_purge_partial(update, chat_id, token)
        return

    if data.startswith("arc_resend_"):
        await _handle_arc_resend(update, context, chat_id, data)
        return

    if data.startswith("arc_purge_"):
        token = data[len("arc_purge_"):]
        await _handle_arc_purge(update, chat_id, token)
        return


async def _handle_arc_cancel(update, chat_id: int, token: str) -> None:
    bucket = pending_archive_jobs.get(chat_id) or {}
    state = bucket.pop(token, None)
    if not bucket:
        pending_archive_jobs.pop(chat_id, None)
    else:
        pending_archive_jobs[chat_id] = bucket
    if state is None:
        try:
            await update.callback_query.edit_message_text("Sesja wygasła.")
        except Exception as exc:
            logging.debug("arc_cancel edit failed: %s", exc)
        return
    try:
        os.remove(str(state.file_path))
    except OSError:
        pass
    try:
        await update.callback_query.edit_message_text("Anulowano. Plik usunięty.")
    except Exception as exc:
        logging.debug("arc_cancel edit failed: %s", exc)


async def _handle_arc_resend(update, context, chat_id: int, data: str) -> None:
    rest = data[len("arc_resend_"):]
    if "_" not in rest:
        return
    token, idx_str = rest.rsplit("_", 1)
    try:
        start_index = int(idx_str)
    except ValueError:
        return

    bucket = archived_deliveries.get(chat_id) or {}
    state = bucket.get(token)
    if state is None:
        try:
            await update.callback_query.edit_message_text("Sesja wygasła.")
        except Exception:
            pass
        return

    async def status(text: str) -> None:
        try:
            await update.callback_query.edit_message_text(text)
        except Exception:
            pass

    try:
        await send_volumes(
            context.bot,
            chat_id=chat_id,
            volumes=state.volumes,
            caption_prefix=state.caption_prefix,
            use_mtproto=state.use_mtproto,
            start_index=start_index,
            status_cb=status,
        )
        await status(f"Wysłano paczki od [{start_index + 1}/{len(state.volumes)}].")
    except Exception as exc:
        await status(f"Wysyłka nadal nie powiodła się: {exc}")


async def _handle_arc_purge(update, chat_id: int, token: str) -> None:
    bucket = archived_deliveries.get(chat_id) or {}
    state = bucket.pop(token, None)
    if not bucket:
        archived_deliveries.pop(chat_id, None)
    else:
        archived_deliveries[chat_id] = bucket
    if state is None:
        try:
            await update.callback_query.edit_message_text("Sesja wygasła.")
        except Exception as exc:
            logging.debug("arc_purge edit failed: %s", exc)
        return
    if state.workspace.exists():
        shutil.rmtree(state.workspace, ignore_errors=True)
    try:
        await update.callback_query.edit_message_text("Folder usunięty.")
    except Exception as exc:
        logging.debug("arc_purge edit failed: %s", exc)


async def _handle_arc_purge_partial(update, chat_id: int, token: str) -> None:
    from bot.session_store import partial_archive_workspaces

    bucket = partial_archive_workspaces.get(chat_id) or {}
    state = bucket.pop(token, None)
    if not bucket:
        partial_archive_workspaces.pop(chat_id, None)
    else:
        partial_archive_workspaces[chat_id] = bucket
    if state is None:
        try:
            await update.callback_query.edit_message_text("Sesja wygasła.")
        except Exception:
            pass
        return
    if state.workspace.exists():
        shutil.rmtree(state.workspace, ignore_errors=True)
    try:
        await update.callback_query.edit_message_text("Folder usunięty.")
    except Exception as exc:
        logging.debug("arc_purge_partial edit failed: %s", exc)
