"""Transcription- and subtitle-oriented Telegram callback flows."""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from bot.config import DOWNLOAD_PATH, get_runtime_value
from bot.handlers.common_ui import escape_md, safe_edit_message, send_long_message
from bot.security_policy import get_media_label
from bot.session_context import (
    clear_uploaded_audio_state as _clear_uploaded_audio_state,
    clear_session_context_value as _clear_session_context_value,
    get_session_context_value as _get_session_context_value,
    set_session_context_value as _set_session_context_value,
)
from bot.transcription_limits import CORRECTION_DURATION_LIMIT_MIN, SUMMARY_DURATION_LIMIT_MIN
from bot.downloader_metadata import get_video_info
from bot.downloader_subtitles import (
    download_subtitles,
    get_available_subtitles,
    parse_subtitle_file,
)
from bot.downloader_validation import sanitize_filename
from bot.services.transcription_service import (
    cleanup_transcription_artifacts,
    generate_summary_artifact,
    load_transcript_result,
    run_transcription_with_progress,
    save_transcript_markdown,
    transcript_too_long_for_summary,
)
from bot.runtime import record_download_for


_executor = ThreadPoolExecutor(max_workers=2)



async def show_summary_options(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    """Dependency placeholder overridden by router module during extraction."""
    raise NotImplementedError


async def download_file(update: Update, context: ContextTypes.DEFAULT_TYPE, type, format, url, **kwargs):
    """Dependency placeholder overridden by router module during extraction."""
    raise NotImplementedError


async def transcribe_audio_file(update: Update, context: ContextTypes.DEFAULT_TYPE, summary=False, summary_type=None):
    query = update.callback_query
    chat_id = update.effective_chat.id

    mp3_path = _get_session_context_value(context, chat_id, "audio_file_path", legacy_key="audio_file_path")
    title = _get_session_context_value(
        context,
        chat_id,
        "audio_file_title",
        legacy_key="audio_file_title",
        default="Plik audio",
    )

    if not mp3_path or not os.path.exists(mp3_path):
        _clear_uploaded_audio_state(context, chat_id)
        await query.edit_message_text("Plik audio nie został znaleziony. Wyślij go ponownie.")
        return

    async def update_status(text):
        await safe_edit_message(query, text)

    chat_download_path = os.path.join(DOWNLOAD_PATH, str(chat_id))
    file_size_mb = os.path.getsize(mp3_path) / (1024 * 1024)

    await update_status("Rozpoczynanie transkrypcji audio...\nTo może potrwać kilka minut.")

    if not get_runtime_value("GROQ_API_KEY", ""):
        await update_status(
            "Funkcja niedostępna — brak klucza API do transkrypcji.\n"
            "Skontaktuj się z administratorem."
        )
        return

    transcript_path = await run_transcription_with_progress(
        source_path=mp3_path,
        output_dir=chat_download_path,
        executor=_executor,
        status_callback=lambda text: update_status(f"Transkrypcja w toku...\n\n{text}"),
    )

    if not transcript_path or not os.path.exists(transcript_path):
        await update_status("Wystąpił błąd podczas transkrypcji.")
        return

    if summary:
        if not get_runtime_value("CLAUDE_API_KEY", ""):
            await update_status(
                "Funkcja niedostępna — brak klucza API do podsumowań.\n"
                "Skontaktuj się z administratorem."
            )
            return

        transcript_result = load_transcript_result(transcript_path)
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
            record_download_for(context, chat_id, title, "audio_upload", "audio_upload_transcription", file_size_mb, None)
            _clear_uploaded_audio_state(context, chat_id)
            return

        await update_status("Transkrypcja zakończona.\n\nGeneruję podsumowanie AI...\nTo może potrwać około minuty.")
        safe_title = sanitize_filename(title)
        summary_result = await generate_summary_artifact(
            transcript_text=transcript_text,
            summary_type=summary_type,
            title=title,
            sanitized_title=safe_title,
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
            "audio_upload",
            f"audio_upload_transcription_summary_{summary_type}",
            file_size_mb,
            None,
        )
        _clear_uploaded_audio_state(context, chat_id)
        await update_status("Transkrypcja i podsumowanie zostały wysłane!")
        return

    await update_status("Transkrypcja zakończona.\n\nWysyłanie transkrypcji...")
    transcript_result = load_transcript_result(transcript_path)
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
            source_media_path=mp3_path,
            output_dir=chat_download_path,
            transcript_prefix=os.path.splitext(os.path.basename(mp3_path))[0],
        )
    except Exception as exc:
        logging.error("Error deleting audio files: %s", exc)

    record_download_for(context, chat_id, title, "audio_upload", "audio_upload_transcription", file_size_mb, None)
    _clear_uploaded_audio_state(context, chat_id)
    await update_status("Transkrypcja została wysłana!")


async def show_audio_summary_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = update.effective_chat.id
    title = _get_session_context_value(
        context,
        chat_id,
        "audio_file_title",
        legacy_key="audio_file_title",
        default="Plik audio",
    )

    keyboard = [
        [InlineKeyboardButton("1. Krótkie podsumowanie", callback_data="audio_summary_option_1")],
        [InlineKeyboardButton("2. Szczegółowe podsumowanie", callback_data="audio_summary_option_2")],
        [InlineKeyboardButton("3. Podsumowanie w punktach", callback_data="audio_summary_option_3")],
        [InlineKeyboardButton("4. Podział zadań na osoby", callback_data="audio_summary_option_4")],
    ]

    await safe_edit_message(
        query,
        f"*{escape_md(title)}*\n\nWybierz rodzaj podsumowania:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def show_subtitle_source_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, url, with_summary=False):
    query = update.callback_query
    chat_id = update.effective_chat.id

    await safe_edit_message(query, "Sprawdzanie dostępnych napisów...")

    info = get_video_info(url)
    if not info:
        media_name = get_media_label(_get_session_context_value(context, chat_id, "platform", legacy_key="platform"))
        await query.edit_message_text(f"Wystąpił błąd podczas pobierania informacji o {media_name}.")
        return

    title = info.get("title", "Nieznany tytuł")
    duration = info.get("duration", 0)
    duration_min = duration / 60 if duration else 0
    subs = get_available_subtitles(info)

    if not subs["has_any"]:
        if with_summary:
            if duration_min > SUMMARY_DURATION_LIMIT_MIN:
                await safe_edit_message(
                    query,
                    f"Film trwa {duration_min:.0f} min — to zbyt długo na podsumowanie AI "
                    f"(limit ~{SUMMARY_DURATION_LIMIT_MIN} min).\n"
                    f"Transkrypcja jest dostępna, ale bez podsumowania."
                )
                return
            await show_summary_options(update, context, url)
        else:
            await download_file(update, context, "audio", "mp3", url, transcribe=True)
        return

    summary_suffix = "_sum" if with_summary else ""
    original_lang = subs.get("original_lang")
    keyboard = []

    def _lang_label(lang_code, suffix=""):
        label = f"  {lang_code.upper()}"
        if original_lang and lang_code == original_lang:
            label += " (oryginal)"
        if suffix:
            label += f" {suffix}"
        return label

    if subs["manual"]:
        keyboard.append([InlineKeyboardButton("--- Napisy YouTube (manualne) ---", callback_data="noop")])
        for lang in subs["manual"]:
            keyboard.append([InlineKeyboardButton(_lang_label(lang), callback_data=f"sub_lang_{lang}{summary_suffix}")])

    if subs["auto"]:
        keyboard.append([InlineKeyboardButton("--- Napisy automatyczne ---", callback_data="noop")])
        for lang in subs["auto"]:
            keyboard.append([InlineKeyboardButton(_lang_label(lang, "(auto)"), callback_data=f"sub_auto_{lang}{summary_suffix}")])

    keyboard.append([InlineKeyboardButton("Transkrypcja AI (Whisper)", callback_data=f"sub_src_ai{summary_suffix}")])
    keyboard.append([InlineKeyboardButton("Powrót", callback_data="back")])

    duration_warning = ""
    if duration_min > SUMMARY_DURATION_LIMIT_MIN:
        duration_warning = (
            f"\n\nUwaga: film trwa {duration_min:.0f} min — podsumowanie "
            f"i korekta AI niedostępne (limit ~{SUMMARY_DURATION_LIMIT_MIN} min)."
        )
    elif duration_min > CORRECTION_DURATION_LIMIT_MIN:
        duration_warning = (
            f"\n\nUwaga: film trwa {duration_min:.0f} min — korekta AI "
            f"transkrypcji niedostępna (limit ~{CORRECTION_DURATION_LIMIT_MIN} min). "
            f"Podsumowanie działa normalnie."
        )

    await safe_edit_message(
        query,
        f"*{escape_md(title)}*\n\n"
        f"Film ma dostępne napisy! Wybierz źródło transkrypcji:\n\n"
        f"Napisy YouTube — natychmiastowo, 0 tokenów\n"
        f"AI Whisper — kilka minut, zużywa tokeny"
        f"{duration_warning}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


def _parse_subtitle_callback(data: str):
    with_summary = data.endswith("_sum")

    if data.startswith("sub_lang_"):
        rest = data[len("sub_lang_"):]
        if with_summary:
            rest = rest[:-4]
        if not rest:
            return None
        return (rest, False, with_summary)

    if data.startswith("sub_auto_"):
        rest = data[len("sub_auto_"):]
        if with_summary:
            rest = rest[:-4]
        if not rest:
            return None
        return (rest, True, with_summary)

    return None


async def _handle_subtitle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, url, data):
    parsed = _parse_subtitle_callback(data)
    if not parsed:
        await update.callback_query.edit_message_text("Nieobsługiwana opcja napisów.")
        return

    lang, auto, with_summary = parsed
    if with_summary:
        _set_session_context_value(
            context,
            update.effective_chat.id,
            "subtitle_pending",
            {"url": url, "lang": lang, "auto": auto},
            legacy_key="subtitle_pending",
        )
        await show_subtitle_summary_options(update, context)
        return

    await handle_subtitle_download(update, context, url, lang, auto, summary=False)


async def _handle_subtitle_summary_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, url, data):
    try:
        summary_type = int(data.replace("sub_sum_", ""))
    except ValueError:
        await update.callback_query.edit_message_text("Nieobsługiwana opcja podsumowania.")
        return

    if summary_type < 1 or summary_type > 4:
        await update.callback_query.edit_message_text("Nieobsługiwana opcja podsumowania.")
        return

    pending = _get_session_context_value(
        context,
        update.effective_chat.id,
        "subtitle_pending",
        legacy_key="subtitle_pending",
    )
    if not pending:
        await update.callback_query.edit_message_text("Sesja wygasła. Wyślij link ponownie.")
        return

    _clear_session_context_value(
        context,
        update.effective_chat.id,
        "subtitle_pending",
        legacy_key="subtitle_pending",
    )
    await handle_subtitle_download(
        update,
        context,
        pending["url"],
        pending["lang"],
        pending["auto"],
        summary=True,
        summary_type=summary_type,
    )


async def show_subtitle_summary_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    keyboard = [
        [InlineKeyboardButton("1. Krótkie podsumowanie", callback_data="sub_sum_1")],
        [InlineKeyboardButton("2. Szczegółowe podsumowanie", callback_data="sub_sum_2")],
        [InlineKeyboardButton("3. Podsumowanie w punktach", callback_data="sub_sum_3")],
        [InlineKeyboardButton("4. Podział zadań na osoby", callback_data="sub_sum_4")],
        [InlineKeyboardButton("Powrót", callback_data="back")],
    ]

    await safe_edit_message(
        query,
        "Wybierz rodzaj podsumowania dla napisów:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_subtitle_download(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url,
    lang,
    auto,
    summary=False,
    summary_type=None,
):
    query = update.callback_query
    chat_id = update.effective_chat.id

    async def update_status(text):
        await safe_edit_message(query, text)

    sub_type = "automatycznych" if auto else "manualnych"
    await update_status(f"Pobieranie napisów YouTube ({lang.upper()}, {sub_type})...")

    chat_download_path = os.path.join(DOWNLOAD_PATH, str(chat_id))
    os.makedirs(chat_download_path, exist_ok=True)

    info = get_video_info(url)
    title = info.get("title", "Nieznany tytuł") if info else "Nieznany tytuł"

    loop = asyncio.get_event_loop()
    sub_path = await loop.run_in_executor(
        _executor,
        lambda: download_subtitles(url, lang, chat_download_path, auto=auto, title=title),
    )

    if not sub_path or not os.path.exists(sub_path):
        await update_status("Nie udało się pobrać napisów. Spróbuj transkrypcji AI.")
        return

    transcript_text = parse_subtitle_file(sub_path)
    if not transcript_text.strip():
        await update_status("Napisy są puste. Spróbuj transkrypcji AI.")
        return

    sanitized_title = sanitize_filename(title)
    transcript_path = save_transcript_markdown(
        title=title,
        transcript_text=transcript_text,
        sanitized_title=sanitized_title,
        output_dir=chat_download_path,
        dated=True,
    )

    if summary and transcript_too_long_for_summary(transcript_text):
        await update_status(
            "Napisy pobrane, ale tekst jest zbyt długi na podsumowanie AI.\n\n"
            "Wysyłam samą transkrypcję z napisów."
        )
        summary = False

    if summary:
        if not get_runtime_value("CLAUDE_API_KEY", ""):
            await update_status(
                "Funkcja niedostępna — brak klucza API do podsumowań.\n"
                "Skontaktuj się z administratorem."
            )
            return

        await update_status("Napisy pobrane.\n\nGeneruję podsumowanie AI...\nTo może potrwać około minuty.")
        summary_result = await generate_summary_artifact(
            transcript_text=transcript_text,
            summary_type=summary_type,
            title=title,
            sanitized_title=f"{datetime.now().strftime('%Y-%m-%d')} {sanitized_title}",
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

        await update_status("Wysyłanie pliku z transkrypcją napisów...")
        with open(transcript_path, "rb") as file_obj:
            await context.bot.send_document(
                chat_id=chat_id,
                document=file_obj,
                filename=os.path.basename(transcript_path),
                caption=f"Napisy YouTube ({lang.upper()}): {title}",
                read_timeout=60,
                write_timeout=60,
            )

        try:
            os.remove(sub_path)
        except Exception as exc:
            logging.error("Error deleting subtitle file: %s", exc)

        record_download_for(
            context,
            chat_id,
            title,
            url,
            f"yt_subtitles_{lang}_summary_{summary_type}",
            0,
            None,
            selected_format=f"sub_{lang}",
        )
        await update_status("Napisy i podsumowanie zostały wysłane!")
        return

    await update_status("Napisy pobrane.\n\nWysyłanie transkrypcji...")
    display_text = transcript_text
    if len(display_text) <= 30000:
        await send_long_message(
            context.bot,
            chat_id,
            display_text,
            header=f"*Napisy YouTube ({lang.upper()}): {escape_md(title)}*\n\n",
        )

    with open(transcript_path, "rb") as file_obj:
        await context.bot.send_document(
            chat_id=chat_id,
            document=file_obj,
            filename=os.path.basename(transcript_path),
            caption=(
                f"Napisy YouTube ({lang.upper()}): {title}"
                if len(display_text) <= 30000
                else f"Napisy ({lang.upper()}): {title} ({len(display_text):,} znaków — tylko plik)"
            ),
            read_timeout=60,
            write_timeout=60,
        )

    try:
        os.remove(sub_path)
    except Exception as exc:
        logging.error("Error deleting subtitle file: %s", exc)

    record_download_for(
        context,
        chat_id,
        title,
        url,
        f"yt_subtitles_{lang}",
        0,
        None,
        selected_format=f"sub_{lang}",
    )
    await update_status("Napisy zostały wysłane!")
