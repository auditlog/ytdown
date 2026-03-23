"""Video upload handlers — extract audio from uploaded video files."""

from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.config import DOWNLOAD_PATH, get_runtime_value
from bot.handlers.common_ui import escape_md
from bot.security_limits import FFMPEG_TIMEOUT, RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW
from bot.security_throttling import check_rate_limit
from bot.services.auth_service import store_pending_action
from bot.session_context import (
    get_auth_state as _get_auth_state,
    set_session_context_value as _set_session_context_value,
)


TELEGRAM_DOWNLOAD_LIMIT_MB = 20
MTPROTO_MAX_FILE_SIZE_MB = 200


def _extract_video_info(message) -> dict | None:
    """Extract video file metadata from a Telegram message."""

    video_mime_to_ext = {
        "video/mp4": ".mp4",
        "video/quicktime": ".mov",
        "video/x-matroska": ".mkv",
        "video/x-msvideo": ".avi",
        "video/webm": ".webm",
    }

    if message.video:
        vid = message.video
        mime = vid.mime_type or "video/mp4"
        return {
            "file_id": vid.file_id,
            "file_size": vid.file_size,
            "duration": vid.duration,
            "mime_type": mime,
            "title": vid.file_name or "Video",
            "ext": video_mime_to_ext.get(mime, ".mp4"),
        }

    if message.document:
        doc = message.document
        mime = doc.mime_type or ""
        if mime in video_mime_to_ext:
            return {
                "file_id": doc.file_id,
                "file_size": doc.file_size,
                "duration": None,
                "mime_type": mime,
                "title": doc.file_name or "Video",
                "ext": video_mime_to_ext[mime],
            }

    return None


async def handle_video_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle video file uploads and offer transcription."""

    user_id = update.effective_user.id
    message = update.message
    video_info = _extract_video_info(message)
    if not video_info:
        return

    from bot.handlers.inbound_media import handle_pin, _is_authorized, process_video_file

    pin_handled = await handle_pin(update, context)
    if pin_handled:
        return

    if not _is_authorized(context, user_id):
        store_pending_action(
            _get_auth_state(context, update.effective_chat.id),
            kind="video",
            payload=video_info,
        )
        await message.reply_text(
            "Wymagane uwierzytelnienie!\n\n"
            "Proszę podaj 8-cyfrowy kod PIN, aby uzyskać dostęp."
        )
        return

    if not check_rate_limit(user_id):
        await message.reply_text(
            "Przekroczono limit requestów!\n\n"
            f"Możesz wysłać maksymalnie {RATE_LIMIT_REQUESTS} requestów "
            f"w ciągu {RATE_LIMIT_WINDOW} sekund.\n"
            "Spróbuj ponownie za chwilę."
        )
        return

    await process_video_file(update, context, video_info)


async def extracted_process_video_file(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    video_info: dict | None = None,
):
    """Download a video file, extract audio, and show transcription options."""

    chat_id = update.effective_chat.id
    message = update.message

    if not video_info:
        video_info = _extract_video_info(message)
    if not video_info:
        await message.reply_text("Nie rozpoznano pliku video.")
        return

    file_size = video_info.get("file_size") or 0
    file_size_mb = file_size / (1024 * 1024) if file_size else 0
    use_mtproto = file_size_mb > TELEGRAM_DOWNLOAD_LIMIT_MB

    if use_mtproto:
        from bot.mtproto import is_mtproto_available

        if not is_mtproto_available():
            await message.reply_text(
                f"Plik jest za duży do pobrania przez Telegram Bot API.\n\n"
                f"Rozmiar: {file_size_mb:.1f} MB\n"
                f"Limit: {TELEGRAM_DOWNLOAD_LIMIT_MB} MB\n\n"
                f"Aby pobierać większe pliki, skonfiguruj TELEGRAM_API_ID "
                f"i TELEGRAM_API_HASH (z my.telegram.org) oraz zainstaluj pyrogram."
            )
            return
        if file_size_mb > MTPROTO_MAX_FILE_SIZE_MB:
            await message.reply_text(
                f"Plik jest zbyt duży.\n\n"
                f"Rozmiar: {file_size_mb:.1f} MB\n"
                f"Limit: {MTPROTO_MAX_FILE_SIZE_MB} MB"
            )
            return

    progress_msg = await message.reply_text(
        f"Pobieranie pliku video ({file_size_mb:.1f} MB)..."
        + (" (MTProto)" if use_mtproto else "")
    )
    chat_download_path = os.path.join(DOWNLOAD_PATH, str(chat_id))
    os.makedirs(chat_download_path, exist_ok=True)

    try:
        title = video_info["title"]
        ext = video_info["ext"]
        safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)[:80]
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        video_path = os.path.join(chat_download_path, f"{timestamp}_{safe_title}{ext}")

        if use_mtproto:
            from bot.mtproto import download_file_mtproto

            success = await download_file_mtproto(
                bot_token=get_runtime_value("TELEGRAM_BOT_TOKEN", ""),
                chat_id=chat_id,
                message_id=message.message_id,
                dest_path=video_path,
            )
            if not success:
                await progress_msg.edit_text("Błąd pobierania pliku przez MTProto.")
                return
        else:
            tg_file = await context.bot.get_file(video_info["file_id"])
            await tg_file.download_to_drive(video_path)

        await progress_msg.edit_text("Ekstrakcja audio z video...")
        mp3_path = os.path.splitext(video_path)[0] + ".mp3"
        result = subprocess.run(
            ["ffmpeg", "-i", video_path, "-vn", "-acodec", "libmp3lame", "-q:a", "2", mp3_path],
            capture_output=True,
            timeout=FFMPEG_TIMEOUT,
        )
        if result.returncode != 0:
            logging.error("ffmpeg video audio extraction failed: %s", result.stderr.decode())
            await progress_msg.edit_text("Błąd ekstrakcji audio z pliku video.")
            return

        try:
            os.remove(video_path)
        except OSError:
            pass
        mp3_size_mb = os.path.getsize(mp3_path) / (1024 * 1024)
        _set_session_context_value(context, chat_id, "audio_file_path", mp3_path, legacy_key="audio_file_path")
        _set_session_context_value(context, chat_id, "audio_file_title", title, legacy_key="audio_file_title")

        duration_info = ""
        if video_info.get("duration"):
            mins = video_info["duration"] // 60
            secs = video_info["duration"] % 60
            duration_info = f"\nCzas trwania: {mins}:{secs:02d}"

        reply_markup = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Transkrypcja", callback_data="audio_transcribe")],
                [InlineKeyboardButton("Transkrypcja + Podsumowanie", callback_data="audio_transcribe_summary")],
            ]
        )
        await progress_msg.edit_text(
            f"*{escape_md(title)}*{duration_info}\n"
            f"Rozmiar audio: {mp3_size_mb:.1f} MB\n\n"
            f"Wybierz opcję:",
            reply_markup=reply_markup,
            parse_mode="Markdown",
        )
    except Exception as exc:
        logging.error("Error processing video upload: %s", exc)
        await progress_msg.edit_text("Błąd przetwarzania pliku video. Spróbuj ponownie.")
