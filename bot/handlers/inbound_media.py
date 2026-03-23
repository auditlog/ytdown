"""Inbound media and link entry handlers."""

from __future__ import annotations

import logging
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

from bot.config import DOWNLOAD_PATH, get_runtime_value
from bot.downloader_media import get_instagram_post_info, is_photo_entry
from bot.downloader_metadata import get_video_info
from bot.downloader_playlist import is_playlist_url, is_pure_playlist_url
from bot.security_limits import FFMPEG_TIMEOUT, MAX_FILE_SIZE_MB, MAX_PLAYLIST_ITEMS, RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW
from bot.security_pin import get_block_remaining_seconds, is_user_blocked
from bot.security_policy import detect_platform, estimate_file_size, get_media_label, normalize_url, validate_url
from bot.security_throttling import check_rate_limit
from bot.services.auth_service import store_pending_action
from bot.services.playlist_service import build_playlist_message, load_playlist
from bot.session_store import block_until, user_playlist_data, user_time_ranges, user_urls
from bot.services.spotify_service import (
    build_episode_caption_data,
    get_resolution_error_message,
    resolve_episode,
)
from bot.session_context import (
    get_auth_state as _get_auth_state,
    get_session_context_value as _get_session_context_value,
    get_session_value as _get_session_value,
    set_session_context_value as _set_session_context_value,
    set_session_value as _set_session_value,
    clear_session_context_value as _clear_session_context_value,
    clear_session_value as _clear_session_value,
)
from bot.spotify import parse_spotify_episode_url


async def handle_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raise NotImplementedError


def _is_authorized(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    raise NotImplementedError


def parse_time_range(text: str) -> dict | None:
    raise NotImplementedError


def _build_main_keyboard(platform: str, large_file: bool = False) -> list:
    raise NotImplementedError


def _build_instagram_photo_keyboard(photos: list, videos: list) -> list:
    raise NotImplementedError


async def process_youtube_link(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    raise NotImplementedError


async def process_playlist_link(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    raise NotImplementedError


async def _process_spotify_episode(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    raise NotImplementedError


async def process_audio_file(update: Update, context: ContextTypes.DEFAULT_TYPE, audio_info: dict | None = None):
    raise NotImplementedError


async def process_video_file(update: Update, context: ContextTypes.DEFAULT_TYPE, video_info: dict | None = None):
    raise NotImplementedError


def escape_md(text: str) -> str:
    """Escapes Markdown v1 special characters in text."""
    return escape_markdown(text, version=1)


async def handle_youtube_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles YouTube links and custom time range input."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    message_text = update.message.text

    pin_handled = await handle_pin(update, context)
    if pin_handled:
        return

    if not _is_authorized(context, user_id):
        store_pending_action(_get_auth_state(context, chat_id), kind="url", payload=message_text)
        await update.message.reply_text(
            "Wymagane uwierzytelnienie!\n\n"
            "Proszę podaj 8-cyfrowy kod PIN, aby uzyskać dostęp."
        )
        return

    current_url = _get_session_value(context, chat_id, "current_url", user_urls)
    if current_url:
        time_range = parse_time_range(message_text)
        if time_range:
            info = get_video_info(current_url)
            if info:
                duration = int(info.get("duration") or 0)
                title = info.get("title", "Nieznany tytuł")
                duration_str = f"{duration // 60}:{duration % 60:02d}" if duration else "?"

                if duration and time_range["end_sec"] > duration:
                    await update.message.reply_text(
                        f"❌ Nieprawidłowy zakres!\n\n"
                        f"Czas końcowy ({time_range['end']}) przekracza czas trwania filmu ({duration_str})."
                    )
                    return

                _set_session_value(context, chat_id, "time_range", time_range, user_time_ranges)
                cur_platform = _get_session_context_value(
                    context,
                    chat_id,
                    "platform",
                    legacy_key="platform",
                    default="youtube",
                )
                reply_markup = InlineKeyboardMarkup(_build_main_keyboard(cur_platform))
                await update.message.reply_text(
                    f"✅ Ustawiono zakres: {time_range['start']} - {time_range['end']}\n\n"
                    f"*{escape_md(title)}*\nCzas trwania: {duration_str}\n"
                    f"✂️ Zakres: {time_range['start']} - {time_range['end']}\n\n"
                    f"Wybierz format do pobrania:",
                    reply_markup=reply_markup,
                    parse_mode="Markdown",
                )
                return

    if is_user_blocked(user_id, block_map=block_until):
        remaining_time = get_block_remaining_seconds(user_id, block_map=block_until)
        minutes = remaining_time // 60
        seconds = remaining_time % 60
        await update.message.reply_text(
            f"Dostęp zablokowany z powodu zbyt wielu nieudanych prób. "
            f"Spróbuj ponownie za {minutes} min {seconds} s."
        )
        return

    if not check_rate_limit(user_id):
        await update.message.reply_text(
            "Przekroczono limit requestów!\n\n"
            f"Możesz wysłać maksymalnie {RATE_LIMIT_REQUESTS} requestów "
            f"w ciągu {RATE_LIMIT_WINDOW} sekund.\n"
            "Spróbuj ponownie za chwilę."
        )
        return

    if "castbox.fm" in message_text:
        import asyncio

        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            message_text = await loop.run_in_executor(executor, normalize_url, message_text)

    if not validate_url(message_text):
        await update.message.reply_text(
            "Nieprawidłowy URL!\n\n"
            "Obsługiwane platformy:\n"
            "- YouTube (youtube.com, youtu.be)\n"
            "- Vimeo (vimeo.com)\n"
            "- TikTok (tiktok.com)\n"
            "- Instagram (instagram.com)\n"
            "- LinkedIn (linkedin.com)\n"
            "- Castbox (castbox.fm)\n"
            "- Spotify podcasty (open.spotify.com/episode)"
        )
        return

    await process_youtube_link(update, context, message_text)


def _build_playlist_message(playlist_info: dict) -> tuple[str, InlineKeyboardMarkup]:
    """Compatibility wrapper around the playlist service message builder."""
    return build_playlist_message(playlist_info)


async def extracted_process_playlist_link(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    """Handles playlist URL and shows playlist menu."""
    chat_id = update.effective_chat.id
    progress_message = await update.message.reply_text("Wykryto playlistę! Pobieranie informacji...")
    playlist_info = load_playlist(url, max_items=MAX_PLAYLIST_ITEMS)

    if not playlist_info:
        await progress_message.edit_text("Nie udało się pobrać informacji o playliście.")
        return

    if not playlist_info["entries"]:
        await progress_message.edit_text("Playlista jest pusta.")
        return

    _set_session_value(context, chat_id, "playlist_data", playlist_info, user_playlist_data)
    _set_session_value(context, chat_id, "current_url", url, user_urls)
    msg, reply_markup = _build_playlist_message(playlist_info)
    await progress_message.edit_text(msg, reply_markup=reply_markup, parse_mode="Markdown")


async def extracted_process_spotify_episode(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    """Resolves a Spotify episode URL and shows download options."""
    chat_id = update.effective_chat.id
    progress_message = await update.message.reply_text("Spotify: wyszukiwanie odcinka podcastu...")

    resolved = await resolve_episode(url)
    error_message = get_resolution_error_message(resolved)
    if error_message:
        await progress_message.edit_text(error_message)
        return

    _set_session_context_value(
        context,
        chat_id,
        "spotify_resolved",
        resolved,
        legacy_key="spotify_resolved",
    )
    _set_session_value(context, chat_id, "current_url", url, user_urls)

    caption_data = build_episode_caption_data(resolved)
    title = caption_data["title"]
    show_name = caption_data["show_name"]
    duration_str = caption_data["duration_str"]
    source_label = caption_data["source_label"]
    show_info = f"\nPodcast: {escape_md(show_name)}" if show_name else ""

    reply_markup = InlineKeyboardMarkup(_build_main_keyboard("spotify"))
    await progress_message.edit_text(
        f"*{escape_md(title)}*{show_info}\n"
        f"Czas trwania: {duration_str}\n"
        f"Źródło audio: {source_label}\n\n"
        f"Wybierz opcję:",
        reply_markup=reply_markup,
        parse_mode="Markdown",
    )


async def extracted_process_youtube_link(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    """Processes a media link after PIN authorization."""
    chat_id = update.effective_chat.id

    if "castbox.fm" in url:
        import asyncio

        with ThreadPoolExecutor(max_workers=1) as executor:
            url = await asyncio.get_event_loop().run_in_executor(executor, normalize_url, url)

    _set_session_value(context, chat_id, "current_url", url, user_urls)
    _clear_session_value(context, chat_id, "time_range", user_time_ranges)

    platform = detect_platform(url) or "youtube"
    _set_session_context_value(context, chat_id, "platform", platform, legacy_key="platform")
    _clear_session_context_value(context, chat_id, "spotify_resolved", legacy_key="spotify_resolved")
    _clear_session_context_value(context, chat_id, "instagram_carousel", legacy_key="ig_carousel")
    _clear_session_context_value(context, chat_id, "subtitle_pending", legacy_key="subtitle_pending")

    if platform == "castbox" and "/channel/" in url:
        await update.message.reply_text(
            "Castbox: link do kanału nie jest obsługiwany.\n\n"
            "Wyślij link do konkretnego odcinka podcastu\n"
            "(np. castbox.fm/episode/...)."
        )
        return

    if platform == "spotify":
        if not parse_spotify_episode_url(url):
            await update.message.reply_text(
                "Spotify: obsługiwane są tylko linki do odcinków podcastów.\n\n"
                "Wyślij link w formacie:\n"
                "open.spotify.com/episode/..."
            )
            return
        await _process_spotify_episode(update, context, url)
        return

    if platform == "instagram":
        progress_message = await update.message.reply_text("Pobieranie informacji o poście...")
        import asyncio

        ig_info = await asyncio.get_event_loop().run_in_executor(None, get_instagram_post_info, url)
        if ig_info:
            if ig_info.get("_type") == "playlist" and ig_info.get("entries"):
                entries = [entry for entry in ig_info.get("entries", []) if entry]
                photos = [entry for entry in entries if is_photo_entry(entry)]
                videos = [entry for entry in entries if not is_photo_entry(entry)]

                if photos:
                    carousel_state = {
                        "photos": photos,
                        "videos": videos,
                        "title": ig_info.get("title", "Instagram post"),
                    }
                    _set_session_context_value(
                        context,
                        chat_id,
                        "instagram_carousel",
                        carousel_state,
                        legacy_key="ig_carousel",
                    )
                    reply_markup = InlineKeyboardMarkup(
                        _build_instagram_photo_keyboard(photos, videos)
                    )
                    title = escape_md(ig_info.get("title", "Instagram post"))
                    parts = []
                    if photos:
                        parts.append(f"{len(photos)} zdjęć" if len(photos) > 1 else "1 zdjęcie")
                    if videos:
                        parts.append(f"{len(videos)} filmów" if len(videos) > 1 else "1 film")
                    await progress_message.edit_text(
                        f"*{title}*\nKaruzela: {', '.join(parts)}\n\nWybierz co pobrać:",
                        reply_markup=reply_markup,
                        parse_mode="Markdown",
                    )
                    return

            elif is_photo_entry(ig_info):
                carousel_state = {
                    "photos": [ig_info],
                    "videos": [],
                    "title": ig_info.get("title", "Instagram photo"),
                }
                _set_session_context_value(
                    context,
                    chat_id,
                    "instagram_carousel",
                    carousel_state,
                    legacy_key="ig_carousel",
                )
                reply_markup = InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Pobierz zdjęcie", callback_data="dl_ig_photos")]]
                )
                title = escape_md(ig_info.get("title", "Instagram photo"))
                await progress_message.edit_text(
                    f"*{title}*\nTyp: zdjęcie\n\nWybierz opcję:",
                    reply_markup=reply_markup,
                    parse_mode="Markdown",
                )
                return

        await progress_message.delete()

    if is_playlist_url(url):
        if is_pure_playlist_url(url):
            await process_playlist_link(update, context, url)
            return

        reply_markup = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Pojedynczy film", callback_data="pl_single")],
                [InlineKeyboardButton("Cała playlista", callback_data="pl_full")],
            ]
        )
        await update.message.reply_text(
            "Ten link zawiera zarówno film jak i playlistę.\n\n"
            "Co chcesz pobrać?",
            reply_markup=reply_markup,
        )
        return

    media_name = get_media_label(platform)
    progress_message = await update.message.reply_text(f"Pobieranie informacji o {media_name}...")
    info = get_video_info(url)
    if not info:
        await progress_message.edit_text(f"Wystąpił błąd podczas pobierania informacji o {media_name}.")
        return

    title = info.get("title", "Nieznany tytuł")
    duration = int(info.get("duration") or 0)
    duration_str = f"{duration // 60}:{duration % 60:02d}" if duration else "?"
    estimated_size = estimate_file_size(info)
    size_warning = ""

    if estimated_size and estimated_size > MAX_FILE_SIZE_MB:
        size_warning = (
            f"\n*Uwaga:* Szacowany rozmiar najlepszej jakości: {estimated_size:.1f} MB "
            f"(limit: {MAX_FILE_SIZE_MB} MB)\n"
        )
        keyboard = _build_main_keyboard(platform, large_file=True)
    else:
        keyboard = _build_main_keyboard(platform)

    time_range = _get_session_value(context, chat_id, "time_range", user_time_ranges)
    time_range_info = f"\n✂️ Zakres: {time_range['start']} - {time_range['end']}" if time_range else ""

    await progress_message.edit_text(
        f"*{escape_md(title)}*\nCzas trwania: {duration_str}{size_warning}{time_range_info}\n\n"
        f"Wybierz format do pobrania:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


TELEGRAM_DOWNLOAD_LIMIT_MB = 20
MTPROTO_MAX_FILE_SIZE_MB = 200


def _extract_audio_info(message) -> dict | None:
    """Extract audio file metadata from a Telegram message."""
    if message.voice:
        voice = message.voice
        return {
            "file_id": voice.file_id,
            "file_size": voice.file_size,
            "duration": voice.duration,
            "mime_type": voice.mime_type or "audio/ogg",
            "title": "Wiadomość głosowa",
        }

    if message.audio:
        audio = message.audio
        return {
            "file_id": audio.file_id,
            "file_size": audio.file_size,
            "duration": audio.duration,
            "mime_type": audio.mime_type or "audio/mpeg",
            "title": audio.title or audio.file_name or "Plik audio",
        }

    if message.document:
        doc = message.document
        mime = doc.mime_type or ""
        if mime.startswith("audio/"):
            return {
                "file_id": doc.file_id,
                "file_size": doc.file_size,
                "duration": None,
                "mime_type": mime,
                "title": doc.file_name or "Dokument audio",
            }

    return None


async def handle_audio_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles voice messages, audio files, and audio documents."""
    user_id = update.effective_user.id
    message = update.message
    audio_info = _extract_audio_info(message)
    if not audio_info:
        return

    pin_handled = await handle_pin(update, context)
    if pin_handled:
        return

    if not _is_authorized(context, user_id):
        store_pending_action(
            _get_auth_state(context, update.effective_chat.id),
            kind="audio",
            payload=audio_info,
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

    await process_audio_file(update, context, audio_info)


async def extracted_process_audio_file(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    audio_info: dict | None = None,
):
    """Download uploaded audio, convert if needed, and show transcription options."""
    chat_id = update.effective_chat.id
    message = update.message

    if not audio_info:
        audio_info = _extract_audio_info(message)
    if not audio_info:
        await message.reply_text("Nie rozpoznano pliku audio.")
        return

    file_size = audio_info.get("file_size") or 0
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
        f"Pobieranie pliku audio ({file_size_mb:.1f} MB)..."
        + (" (MTProto)" if use_mtproto else "")
    )
    chat_download_path = os.path.join(DOWNLOAD_PATH, str(chat_id))
    os.makedirs(chat_download_path, exist_ok=True)

    try:
        mime_to_ext = {
            "audio/ogg": ".ogg",
            "audio/opus": ".opus",
            "audio/mpeg": ".mp3",
            "audio/mp4": ".m4a",
            "audio/x-m4a": ".m4a",
            "audio/wav": ".wav",
            "audio/x-wav": ".wav",
            "audio/flac": ".flac",
            "audio/webm": ".webm",
            "audio/aac": ".aac",
            "audio/amr": ".amr",
            "audio/x-caf": ".caf",
        }
        ext = mime_to_ext.get(audio_info["mime_type"], ".ogg")
        title = audio_info["title"]
        safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)[:80]
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        raw_path = os.path.join(chat_download_path, f"{timestamp}_{safe_title}{ext}")

        if use_mtproto:
            from bot.mtproto import download_file_mtproto

            success = await download_file_mtproto(
                bot_token=get_runtime_value("TELEGRAM_BOT_TOKEN", ""),
                chat_id=chat_id,
                message_id=message.message_id,
                dest_path=raw_path,
            )
            if not success:
                await progress_msg.edit_text("Błąd pobierania pliku przez MTProto.")
                return
        else:
            tg_file = await context.bot.get_file(audio_info["file_id"])
            await tg_file.download_to_drive(raw_path)

        if ext == ".mp3":
            mp3_path = raw_path
        else:
            mp3_path = os.path.splitext(raw_path)[0] + ".mp3"
            await progress_msg.edit_text("Konwersja do MP3...")
            result = subprocess.run(
                ["ffmpeg", "-i", raw_path, "-vn", "-acodec", "libmp3lame", "-q:a", "2", mp3_path],
                capture_output=True,
                timeout=FFMPEG_TIMEOUT,
            )
            if result.returncode != 0:
                logging.error("ffmpeg conversion failed: %s", result.stderr.decode())
                await progress_msg.edit_text("Błąd konwersji pliku audio.")
                return
            os.remove(raw_path)

        mp3_size_mb = os.path.getsize(mp3_path) / (1024 * 1024)
        _set_session_context_value(
            context,
            chat_id,
            "audio_file_path",
            mp3_path,
            legacy_key="audio_file_path",
        )
        _set_session_context_value(
            context,
            chat_id,
            "audio_file_title",
            title,
            legacy_key="audio_file_title",
        )

        duration_info = ""
        if audio_info.get("duration"):
            mins = audio_info["duration"] // 60
            secs = audio_info["duration"] % 60
            duration_info = f"\nCzas trwania: {mins}:{secs:02d}"

        reply_markup = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Transkrypcja", callback_data="audio_transcribe")],
                [InlineKeyboardButton("Transkrypcja + Podsumowanie", callback_data="audio_transcribe_summary")],
            ]
        )
        await progress_msg.edit_text(
            f"*{escape_md(title)}*{duration_info}\n"
            f"Rozmiar: {mp3_size_mb:.1f} MB\n\n"
            f"Wybierz opcję:",
            reply_markup=reply_markup,
            parse_mode="Markdown",
        )
    except Exception as exc:
        logging.error("Error processing audio upload: %s", exc)
        await progress_msg.edit_text("Błąd przetwarzania pliku audio. Spróbuj ponownie.")


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
    """Handles video file uploads and offers transcription."""
    user_id = update.effective_user.id
    message = update.message
    video_info = _extract_video_info(message)
    if not video_info:
        return

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

        os.remove(video_path)
        mp3_size_mb = os.path.getsize(mp3_path) / (1024 * 1024)
        _set_session_context_value(
            context,
            chat_id,
            "audio_file_path",
            mp3_path,
            legacy_key="audio_file_path",
        )
        _set_session_context_value(
            context,
            chat_id,
            "audio_file_title",
            title,
            legacy_key="audio_file_title",
        )

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
