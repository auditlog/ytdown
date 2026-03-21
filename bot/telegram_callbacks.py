"""
Telegram callbacks module for YouTube Downloader Telegram Bot.

Contains callback query handlers and file download logic.
"""

import os
import asyncio
import logging
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import ContextTypes
from telegram.error import BadRequest, NetworkError, TimedOut
from telegram.helpers import escape_markdown

# Thread pool for running sync functions
_executor = ThreadPoolExecutor(max_workers=2)

from bot.config import (
    DOWNLOAD_PATH,
    get_runtime_value,
)
from bot.security import (
    MAX_FILE_SIZE_MB,
    MAX_PLAYLIST_ITEMS,
    MAX_PLAYLIST_ITEMS_EXPANDED,
    check_rate_limit,
    normalize_url,
    user_urls,
    user_time_ranges,
    user_playlist_data,
    get_media_label,
)
from bot.telegram_commands import _build_main_keyboard, _build_playlist_message, process_playlist_link
from bot.transcription import (
    transcribe_mp3_file,
    CORRECTION_DURATION_LIMIT_MIN,
    SUMMARY_DURATION_LIMIT_MIN,
)
from bot.downloader import (
    get_video_info,
    sanitize_filename,
    is_valid_audio_format,
    is_valid_ytdlp_format_id,
    is_valid_audio_quality,
    get_available_subtitles,
    download_subtitles,
    parse_subtitle_file,
    download_thumbnail,
    download_photo,
)
from bot.services.download_service import (
    ensure_size_within_limit,
    estimate_download_size,
    execute_download,
    prepare_download_plan,
)
from bot.services.transcription_service import (
    cleanup_transcription_artifacts,
    generate_summary_artifact,
    load_transcript_result,
    run_transcription_with_progress,
    save_transcript_markdown,
    transcript_too_long_for_summary,
)
from bot.services.spotify_service import download_resolved_audio
from bot.services.playlist_service import (
    build_single_video_url,
    download_playlist_item,
    load_playlist,
    parse_playlist_download_choice,
)
from bot.runtime import get_app_runtime, record_download_for
from bot.session_store import download_progress as _download_progress


def escape_md(text: str) -> str:
    """Escapes Markdown v1 special characters in text."""
    return escape_markdown(text, version=1)


def format_bytes(bytes_value):
    """Formats bytes to human readable string."""
    if bytes_value is None:
        return "?"
    for unit in ['B', 'KB', 'MB', 'GB']:
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
    elif seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    else:
        return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"


def create_progress_hook(chat_id):
    """Creates a progress hook for yt-dlp that updates global progress state."""
    def hook(d):
        if d['status'] == 'downloading':
            _download_progress[chat_id] = {
                'status': 'downloading',
                'percent': d.get('_percent_str', '?%').strip(),
                'downloaded': d.get('downloaded_bytes', 0),
                'total': d.get('total_bytes') or d.get('total_bytes_estimate', 0),
                'speed': d.get('speed', 0),
                'eta': d.get('eta', None),
                'filename': d.get('filename', ''),
                'updated': time.time()
            }
        elif d['status'] == 'finished':
            _download_progress[chat_id] = {
                'status': 'finished',
                'percent': '100%',
                'downloaded': d.get('downloaded_bytes', 0),
                'total': d.get('total_bytes', 0),
                'filename': d.get('filename', ''),
                'updated': time.time()
            }
        elif d['status'] == 'error':
            _download_progress[chat_id] = {
                'status': 'error',
                'updated': time.time()
            }
    return hook


async def safe_edit_message(query, text, reply_markup=None, parse_mode=None):
    """
    Safely edits message, ignoring 'message not modified' error
    and transient network errors (so status updates don't crash the handler).
    """
    try:
        await query.edit_message_text(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            raise
    except (NetworkError, TimedOut) as e:
        logging.warning(f"Network error updating status message: {e}")


async def send_long_message(bot, chat_id, text, header="", parse_mode='Markdown'):
    """
    Splits long text into multiple Telegram messages (max 4000 chars each)
    and sends them sequentially. Optionally prepends a header to the first chunk.

    Handles lines longer than max_length (e.g. Whisper output without newlines)
    by splitting at sentence boundaries, commas, or spaces.
    """
    max_length = 4000
    parts = []
    current = header

    for line in text.split('\n'):
        # Split oversized lines at natural break points
        while len(line) > max_length:
            split_at = max_length
            for sep in ['. ', '! ', '? ', ', ', ' ']:
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
            current = line + '\n'
        else:
            current += line + '\n'

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


def parse_download_callback(data):
    """Parses download-related callback data.

    Expected formats:
      - dl_video_<format>
      - dl_audio_<codec>
      - dl_audio_format_<format_id>
    """
    if not isinstance(data, str):
        return None

    if not data.startswith("dl_"):
        return None

    parts = data.split("_")
    if len(parts) < 3:
        return None

    media_type = parts[1]
    if media_type not in {"audio", "video"}:
        return None

    if media_type == "audio":
        if len(parts) == 4 and parts[2] == "format":
            return {"media_type": "audio", "mode": "format_id", "format": parts[3]}
        if len(parts) == 3 and parts[2] != "format":
            return {"media_type": "audio", "mode": "codec", "format": parts[2]}
        return None

    if media_type == "video":
        if len(parts) == 3:
            return {"media_type": "video", "mode": "format_id", "format": parts[2]}
        return None

    return None


def parse_summary_option(option_data):
    """Parses summary option payloads.

    Expected format:
      - summary_option_<index>
      - audio_summary_option_<index>
    """
    if not isinstance(option_data, str):
        return None

    if (
        not option_data.startswith("summary_option_")
        and not option_data.startswith("audio_summary_option_")
    ):
        return None

    _, _, raw_value = option_data.rpartition("_")

    if not raw_value:
        return None

    try:
        summary_option = int(raw_value)
    except ValueError:
        return None

    if summary_option < 1 or summary_option > 4:
        return None

    return summary_option


def _get_session_value(context: ContextTypes.DEFAULT_TYPE, chat_id: int, field_name: str, legacy_map):
    """Read one chat-scoped value from runtime session store when available."""

    runtime = get_app_runtime(context)
    if runtime is not None:
        return runtime.session_store.get_field(chat_id, field_name)
    return legacy_map.get(chat_id)


def _set_session_value(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    field_name: str,
    value,
    legacy_map,
) -> None:
    """Write one chat-scoped value through runtime session store when available."""

    runtime = get_app_runtime(context)
    if runtime is not None:
        runtime.session_store.set_field(chat_id, field_name, value)
        return
    legacy_map[chat_id] = value


def _clear_session_value(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    field_name: str,
    legacy_map,
) -> None:
    """Clear one chat-scoped value through runtime session store when available."""

    runtime = get_app_runtime(context)
    if runtime is not None:
        runtime.session_store.pop_field(chat_id, field_name, None)
        return
    legacy_map.pop(chat_id, None)


def _get_session_context_value(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    field_name: str,
    *,
    legacy_key: str,
    default=None,
):
    """Read one session-scoped context value from runtime or legacy user_data."""

    runtime = get_app_runtime(context)
    if runtime is not None:
        value = runtime.session_store.get_field(chat_id, field_name)
        if value is not None:
            return value
    return context.user_data.get(legacy_key, default)


def _set_session_context_value(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    field_name: str,
    value,
    *,
    legacy_key: str,
) -> None:
    """Write one session-scoped context value to runtime and legacy user_data."""

    runtime = get_app_runtime(context)
    if runtime is not None:
        runtime.session_store.set_field(chat_id, field_name, value)
    context.user_data[legacy_key] = value


def _clear_session_context_value(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    field_name: str,
    *,
    legacy_key: str,
) -> None:
    """Clear one session-scoped context value from runtime and legacy user_data."""

    runtime = get_app_runtime(context)
    if runtime is not None:
        runtime.session_store.pop_field(chat_id, field_name, None)
    context.user_data.pop(legacy_key, None)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles all callback queries."""
    query = update.callback_query
    await query.answer()
    data = query.data

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    # Rate limit callbacks to prevent abuse
    if not check_rate_limit(user_id):
        await query.edit_message_text("Przekroczono limit requestów. Spróbuj ponownie za chwilę.")
        return

    # Playlist callbacks
    if data.startswith("pl_"):
        await handle_playlist_callback(update, context, data)
        return

    # Audio upload callbacks — no YouTube URL required
    if data == "audio_transcribe":
        await transcribe_audio_file(update, context)
        return
    elif data == "audio_transcribe_summary":
        await show_audio_summary_options(update, context)
        return
    elif data.startswith("audio_summary_option_"):
        option = parse_summary_option(data)
        if option is None:
            await query.edit_message_text("Nieobsługiwana opcja podsumowania.")
            return
        await transcribe_audio_file(update, context, summary=True, summary_type=option)
        return

    url = _get_session_value(context, chat_id, "current_url", user_urls)

    if not url:
        await query.edit_message_text("Sesja wygasła. Wyślij link ponownie.")
        return

    # Only Castbox links need redirect normalization that may hit the network.
    if "castbox.fm" in url:
        url = await asyncio.get_event_loop().run_in_executor(None, normalize_url, url)

    # Instagram photo/carousel callbacks
    if data.startswith("dl_ig_"):
        await _handle_instagram_download(update, context, url, data)
        return

    if data.startswith("dl_"):
        download_data = parse_download_callback(data)
        if not download_data:
            await query.edit_message_text("Nieobsługiwany format. Spróbuj wybrać format ponownie.")
            return

        media_type = download_data["media_type"]
        mode = download_data["mode"]
        selected_format = download_data["format"]

        # Spotify: use resolved audio source instead of yt-dlp
        if _get_session_context_value(context, chat_id, "platform", legacy_key="platform") == 'spotify':
            resolved = _get_session_context_value(context, chat_id, "spotify_resolved", legacy_key="spotify_resolved")
            if not resolved:
                await query.edit_message_text("Sesja Spotify wygasła. Wyślij link ponownie.")
                return
            await download_spotify_resolved(
                update, context, resolved, selected_format,
                transcribe=False
            )
            return

        if media_type == "audio" and mode == "format_id":
            if not is_valid_ytdlp_format_id(selected_format):
                await query.edit_message_text("Nieobsługiwany format. Spróbuj wybrać format ponownie.")
                return
            await download_file(update, context, "audio", selected_format, url, use_format_id=True)
        elif media_type == "audio":
            if not is_valid_audio_format(selected_format):
                await query.edit_message_text("Nieobsługiwany format audio. Spróbuj wybrać format ponownie.")
                return
            await download_file(update, context, "audio", selected_format, url)
        elif media_type == "video":
            if not is_valid_ytdlp_format_id(selected_format):
                await query.edit_message_text("Nieobsługiwany format. Spróbuj wybrać format ponownie.")
                return
            await download_file(update, context, "video", selected_format, url)
        else:
            await query.edit_message_text("Nieobsługiwany format. Spróbuj wybrać format ponownie.")
            return
    elif data == "transcribe_summary":
        if _get_session_context_value(context, chat_id, "platform", legacy_key="platform") == 'spotify':
            await _show_spotify_summary_options(update, context)
        else:
            await show_subtitle_source_menu(update, context, url, with_summary=True)
    elif data.startswith("summary_option_"):
        option = parse_summary_option(data)
        if option is None:
            await query.edit_message_text("Nieobsługiwana opcja podsumowania.")
            return
        if _get_session_context_value(context, chat_id, "platform", legacy_key="platform") == 'spotify':
            resolved = _get_session_context_value(context, chat_id, "spotify_resolved", legacy_key="spotify_resolved")
            if resolved:
                await download_spotify_resolved(update, context, resolved, "mp3", transcribe=True, summary=True, summary_type=option)
            else:
                await query.edit_message_text("Sesja Spotify wygasła. Wyślij link ponownie.")
        else:
            await download_file(update, context, "audio", "mp3", url, transcribe=True, summary=True, summary_type=option)
    elif data == "transcribe":
        if _get_session_context_value(context, chat_id, "platform", legacy_key="platform") == 'spotify':
            resolved = _get_session_context_value(context, chat_id, "spotify_resolved", legacy_key="spotify_resolved")
            if resolved:
                await download_spotify_resolved(update, context, resolved, "mp3", transcribe=True)
            else:
                await query.edit_message_text("Sesja Spotify wygasła. Wyślij link ponownie.")
        else:
            await show_subtitle_source_menu(update, context, url, with_summary=False)
    elif data == "sub_src_ai":
        await download_file(update, context, "audio", "mp3", url, transcribe=True)
    elif data == "sub_src_ai_sum":
        await show_summary_options(update, context, url)
    elif data.startswith("sub_lang_") or data.startswith("sub_auto_"):
        await _handle_subtitle_callback(update, context, url, data)
    elif data.startswith("sub_sum_"):
        await _handle_subtitle_summary_callback(update, context, url, data)
    elif data == "formats":
        await handle_formats_list(update, context, url)
    elif data == "thumbnail":
        await _handle_thumbnail_download(update, context, url)
    elif data == "time_range":
        await show_time_range_options(update, context, url)
    elif data == "time_range_clear":
        _clear_session_value(context, chat_id, "time_range", user_time_ranges)
        await back_to_main_menu(update, context, url)
    elif data.startswith("time_range_preset_"):
        # Handle preset time ranges like "first_5min", "last_10min"
        preset = data.replace("time_range_preset_", "")
        await apply_time_range_preset(update, context, url, preset)
    elif data == "back":
        await back_to_main_menu(update, context, url)


async def _handle_thumbnail_download(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    """Downloads and sends full-resolution thumbnail as a photo."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    await safe_edit_message(query, "Pobieranie miniaturki...")

    chat_download_path = os.path.join(DOWNLOAD_PATH, str(chat_id))
    os.makedirs(chat_download_path, exist_ok=True)

    loop = asyncio.get_event_loop()
    info = await loop.run_in_executor(_executor, get_video_info, url)
    if not info:
        await safe_edit_message(query, "Nie udało się pobrać informacji o wideo.")
        return

    title = info.get('title', 'Miniaturka')
    thumb_path = await loop.run_in_executor(
        _executor, download_thumbnail, info, chat_download_path, False
    )

    if not thumb_path:
        await safe_edit_message(query, "Brak dostępnej miniaturki dla tego wideo.")
        return

    try:
        with open(thumb_path, 'rb') as f:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=f,
                caption=title[:200],
            )
        await safe_edit_message(query, "Miniaturka została wysłana!")
    except Exception as e:
        logging.error("Error sending thumbnail: %s", e)
        await safe_edit_message(query, "Błąd podczas wysyłania miniaturki.")
    finally:
        if os.path.exists(thumb_path):
            os.remove(thumb_path)


async def _handle_instagram_download(
    update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, callback_data: str
):
    """Routes Instagram photo/carousel download callbacks."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    carousel = _get_session_context_value(context, chat_id, "instagram_carousel", legacy_key="ig_carousel")
    if not carousel:
        await safe_edit_message(query, "Sesja wygasła. Wyślij link ponownie.")
        return

    chat_download_path = os.path.join(DOWNLOAD_PATH, str(chat_id))
    os.makedirs(chat_download_path, exist_ok=True)

    title = carousel.get('title', 'Instagram post')
    photos = carousel.get('photos', [])
    videos = carousel.get('videos', [])

    if callback_data == "dl_ig_photos":
        await _download_and_send_ig_photos(
            update, context, photos, title, chat_download_path
        )
    elif callback_data == "dl_ig_videos":
        await _download_and_send_ig_videos(
            update, context, videos, title, url, chat_download_path
        )
    elif callback_data == "dl_ig_all":
        if photos:
            await _download_and_send_ig_photos(
                update, context, photos, title, chat_download_path
            )
        if videos:
            await _download_and_send_ig_videos(
                update, context, videos, title, url, chat_download_path
            )


async def _download_and_send_ig_photos(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
    photo_entries: list, title: str, download_path: str,
):
    """Downloads and sends Instagram photos, using media groups for multiple."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    count = len(photo_entries)

    await safe_edit_message(
        query,
        f"Pobieranie {'zdjęcia' if count == 1 else f'{count} zdjęć'}..."
    )

    downloaded_paths = []
    loop = asyncio.get_event_loop()
    current_date = datetime.now().strftime("%Y-%m-%d")

    for i, entry in enumerate(photo_entries):
        photo_url = entry.get('url', '')
        if not photo_url:
            # Fallback: use thumbnail as photo source
            thumbs = entry.get('thumbnails', [])
            if thumbs:
                photo_url = thumbs[-1].get('url', '')
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
            with open(downloaded_paths[0], 'rb') as f:
                await context.bot.send_photo(
                    chat_id=chat_id, photo=f,
                    caption=title[:200],
                    read_timeout=60, write_timeout=60,
                )
        else:
            # Telegram media group limit: 10 items per batch
            for batch_start in range(0, len(downloaded_paths), 10):
                batch = downloaded_paths[batch_start:batch_start + 10]
                file_handles = []
                media_group = []

                for j, path in enumerate(batch):
                    fh = open(path, 'rb')
                    file_handles.append(fh)
                    caption = title[:200] if (batch_start + j) == 0 else None
                    media_group.append(InputMediaPhoto(media=fh, caption=caption))

                try:
                    await context.bot.send_media_group(
                        chat_id=chat_id, media=media_group,
                        read_timeout=120, write_timeout=120,
                    )
                finally:
                    for fh in file_handles:
                        fh.close()

                if batch_start + 10 < len(downloaded_paths):
                    await asyncio.sleep(1)

        total_size = sum(os.path.getsize(p) for p in downloaded_paths) / (1024 * 1024)
        record_download_for(
            context, chat_id, title,
            _get_session_value(context, chat_id, "current_url", user_urls) or '',
            "photo", total_size,
        )
        await safe_edit_message(query, f"Wysłano {len(downloaded_paths)} zdjęć!")

    except Exception as e:
        logging.error("Error sending Instagram photos: %s", e)
        await safe_edit_message(query, "Błąd podczas wysyłania zdjęć.")
    finally:
        _clear_session_context_value(context, chat_id, "instagram_carousel", legacy_key="ig_carousel")
        for path in downloaded_paths:
            try:
                os.remove(path)
            except OSError:
                pass


async def _download_and_send_ig_videos(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
    video_entries: list, title: str, url: str, download_path: str,
):
    """Downloads and sends Instagram videos from carousel using yt-dlp."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    sent_count = 0

    for i, entry in enumerate(video_entries):
        video_url = entry.get('url') or entry.get('webpage_url', '')
        if not video_url:
            continue

        status = f"Pobieranie filmu {i + 1}/{len(video_entries)}..."
        await safe_edit_message(query, status)

        safe_title = sanitize_filename(f"{title}_video_{i + 1}")
        current_date = datetime.now().strftime("%Y-%m-%d")
        output_path = os.path.join(download_path, f"{current_date} {safe_title}")

        ydl_opts = {
            'outtmpl': f"{output_path}.%(ext)s",
            'format': 'best',
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
        }
        from bot.downloader import COOKIES_FILE as _cookies
        if os.path.exists(_cookies):
            ydl_opts['cookiefile'] = _cookies

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                _executor,
                lambda opts=ydl_opts, u=video_url: yt_dlp.YoutubeDL(opts).download([u])
            )

            # Find downloaded file
            downloaded = None
            for f in os.listdir(download_path):
                full = os.path.join(download_path, f)
                if f.startswith(f"{current_date} {safe_title}") and os.path.isfile(full):
                    downloaded = full
                    break

            if downloaded:
                file_size = os.path.getsize(downloaded) / (1024 * 1024)
                if file_size > 50:
                    await safe_edit_message(
                        query,
                        f"Film {i + 1} za duży ({file_size:.0f} MB, limit: 50 MB)."
                    )
                    os.remove(downloaded)
                    continue

                with open(downloaded, 'rb') as f:
                    await context.bot.send_video(
                        chat_id=chat_id, video=f,
                        caption=f"{title} ({i + 1}/{len(video_entries)})"[:200],
                        read_timeout=120, write_timeout=120,
                    )
                os.remove(downloaded)
                sent_count += 1

        except Exception as e:
            logging.error("Error downloading Instagram video %d: %s", i + 1, e)

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
    audio_quality='192',
):
    """Downloads file and sends it to user with progress updates."""
    media_type = type
    query = update.callback_query
    chat_id = update.effective_chat.id
    title = "Unknown"  # Default for error recording before info fetch
    success_recorded = False  # Guard against duplicate history records

    # Helper for status updates
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
        # Check file size first
        await update_status(f"Sprawdzanie rozmiaru pliku...\n({duration_str})")
        size_mb = await asyncio.get_event_loop().run_in_executor(
            _executor,
            lambda: estimate_download_size(plan)
        )
        if not ensure_size_within_limit(size_mb, max_size_mb=MAX_FILE_SIZE_MB):
            await update_status(
                f"Wybrany format jest zbyt duży!\n\n"
                f"Rozmiar: {size_mb:.1f} MB\n"
                f"Maksymalny dozwolony rozmiar: {MAX_FILE_SIZE_MB} MB\n\n"
                f"Spróbuj wybrać niższą jakość lub pobierz tylko audio."
            )
            return

        # Download file with progress tracking
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
            await update_status(f"Pobieranie zakończone ({file_size_mb:.1f} MB).\n\nRozpoczynanie transkrypcji audio...\nTo może potrwać kilka minut.")

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
                    # Send transcript file without summary
                    with open(transcript_path, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=chat_id,
                            document=f,
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
                    context.bot, chat_id, summary_result.summary_text,
                    header=f"*{escape_md(title)} - {summary_result.summary_type_name}*\n\n"
                )

                await update_status("Wysyłanie pliku z pełną transkrypcją...")

                with open(transcript_path, 'rb') as f:
                    await context.bot.send_document(
                        chat_id=chat_id,
                        document=f,
                        filename=os.path.basename(transcript_path),
                        caption=f"Pełna transkrypcja: {title}",
                        read_timeout=60,
                        write_timeout=60,
                    )

                # Record transcription+summary in history
                record_download_for(
                    context, chat_id, title, url,
                    f"transcription_summary_{summary_type}",
                    file_size_mb, time_range, selected_format=format,
                )
                success_recorded = True

                await update_status("Transkrypcja i podsumowanie zostały wysłane!")

            else:
                await update_status("Transkrypcja zakończona.\n\nWysyłanie transkrypcji...")
                display_text = transcript_result.display_text

                # Send transcript in chat if short enough, otherwise file only
                if len(display_text) <= 30000:
                    await send_long_message(
                        context.bot, chat_id, display_text,
                        header=f"*Transkrypcja: {escape_md(title)}*\n\n"
                    )

                # Send file as attachment
                with open(transcript_path, 'rb') as f:
                    await context.bot.send_document(
                        chat_id=chat_id,
                        document=f,
                        filename=os.path.basename(transcript_path),
                        caption=f"Transkrypcja: {title}" if len(display_text) <= 30000
                            else f"Transkrypcja: {title} ({len(display_text):,} znaków — tylko plik)",
                        read_timeout=60,
                        write_timeout=60,
                    )

                try:
                    cleanup_transcription_artifacts(
                        source_media_path=downloaded_file_path,
                        output_dir=chat_download_path,
                        transcript_prefix=sanitized_title,
                    )
                except Exception as e:
                    logging.error(f"Error deleting files: {e}")

                # Record transcription in history
                record_download_for(context, chat_id, title, url, "transcription", file_size_mb, time_range, selected_format=format)
                success_recorded = True

                await update_status("Transkrypcja została wysłana!")

        else:
            await update_status(f"Pobieranie zakończone ({file_size_mb:.1f} MB).\n\nWysyłanie pliku do Telegram...")

            # Download thumbnail for embed
            thumb_path = await asyncio.get_event_loop().run_in_executor(
                _executor, download_thumbnail, info, chat_download_path, True
            )

            try:
                with open(downloaded_file_path, 'rb') as f:
                    thumb_file = open(thumb_path, 'rb') if thumb_path else None
                    try:
                        if media_type == "audio":
                            await context.bot.send_audio(
                                chat_id=chat_id,
                                audio=f,
                                title=title,
                                caption=f"{title}",
                                thumbnail=thumb_file,
                                read_timeout=60,
                                write_timeout=60,
                            )
                        else:
                            await context.bot.send_video(
                                chat_id=chat_id,
                                video=f,
                                caption=f"{title}",
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

            # Record download in history
            format_type = f"{media_type}_{format}"
            record_download_for(context, chat_id, title, url, format_type, file_size_mb, time_range, selected_format=format)
            success_recorded = True

            await update_status("Plik został wysłany!")

    except Exception as e:
        # Only record failure if success wasn't already recorded
        if not success_recorded:
            record_download_for(
                context, chat_id, title, url, f"{media_type}_{format}",
                status="failure", selected_format=format,
                error_message=str(e),
            )
        logging.error(f"Error in download_file: {e}")

        # Detect login/cookie errors for platforms requiring authentication
        error_str = str(e).lower()
        if any(kw in error_str for kw in ('login', 'sign in', 'cookie', 'authentication')):
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
    """Displays list of available formats."""
    query = update.callback_query

    info = get_video_info(url)
    if not info:
        chat_id = update.effective_chat.id
        media_name = get_media_label(_get_session_context_value(context, chat_id, "platform", legacy_key="platform"))
        await query.edit_message_text(f"Wystąpił błąd podczas pobierania informacji o {media_name}.")
        return

    title = info.get('title', 'Nieznany tytuł')

    video_formats = []
    audio_formats = []

    for format in info.get('formats', []):
        format_id = format.get('format_id', 'N/A')
        ext = format.get('ext', 'N/A')
        resolution = format.get('resolution', 'N/A')

        if format.get('vcodec') == 'none':
            if len(audio_formats) < 5:
                audio_formats.append({
                    'id': format_id,
                    'desc': f"{format_id}: {ext}, {resolution}"
                })
        else:
            if len(video_formats) < 5:
                video_formats.append({
                    'id': format_id,
                    'desc': f"{format_id}: {ext}, {resolution}"
                })

    keyboard = []

    for format in video_formats:
        keyboard.append([InlineKeyboardButton(f"Video {format['desc']}", callback_data=f"dl_video_{format['id']}")])

    for format in audio_formats:
        keyboard.append([InlineKeyboardButton(f"Audio {format['desc']}", callback_data=f"dl_audio_format_{format['id']}")])

    keyboard.append([InlineKeyboardButton("Powrót", callback_data="back")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await safe_edit_message(
        query,
        f"Formaty dla: {title}\n\nWybierz format:",
        reply_markup=reply_markup
    )


async def show_summary_options(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    """Displays summary options."""
    query = update.callback_query

    info = get_video_info(url)
    if not info:
        chat_id = update.effective_chat.id
        media_name = get_media_label(_get_session_context_value(context, chat_id, "platform", legacy_key="platform"))
        await query.edit_message_text(f"Wystąpił błąd podczas pobierania informacji o {media_name}.")
        return

    title = info.get('title', 'Nieznany tytuł')

    keyboard = [
        [InlineKeyboardButton("1. Krótkie podsumowanie", callback_data="summary_option_1")],
        [InlineKeyboardButton("2. Szczegółowe podsumowanie", callback_data="summary_option_2")],
        [InlineKeyboardButton("3. Podsumowanie w punktach", callback_data="summary_option_3")],
        [InlineKeyboardButton("4. Podział zadań na osoby", callback_data="summary_option_4")],
        [InlineKeyboardButton("Powrót", callback_data="back")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await safe_edit_message(
        query,
        f"*{escape_md(title)}*\n\nWybierz rodzaj podsumowania:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def handle_playlist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    """Handles all playlist-related callbacks."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    if data == "pl_cancel":
        _clear_session_value(context, chat_id, "playlist_data", user_playlist_data)
        await query.edit_message_text("Pobieranie playlisty anulowane.")
        return

    if data == "pl_single":
        # Strip playlist params, download as single video
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

            from bot.telegram_commands import _build_main_keyboard, escape_md
            title = info.get('title', 'Nieznany tytuł')
            duration = info.get('duration', 0)
            duration_str = f"{duration // 60}:{duration % 60:02d}" if duration else "?"
            platform = _get_session_context_value(context, chat_id, "platform", legacy_key="platform", default="youtube")
            keyboard = _build_main_keyboard(platform)
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(
                f"*{escape_md(title)}*\nCzas trwania: {duration_str}\n\nWybierz format do pobrania:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        return

    if data == "pl_full":
        # Fetch and show full playlist
        url = _get_session_value(context, chat_id, "current_url", user_urls)
        if url:
            await query.edit_message_text("Pobieranie informacji o playliście...")
            playlist_info = load_playlist(url, max_items=MAX_PLAYLIST_ITEMS)
            if not playlist_info or not playlist_info['entries']:
                await query.edit_message_text(
                    "Nie udało się pobrać informacji o playliście."
                )
                return
            _set_session_value(context, chat_id, "playlist_data", playlist_info, user_playlist_data)
            msg, reply_markup = _build_playlist_message(playlist_info)
            await query.edit_message_text(msg, reply_markup=reply_markup, parse_mode='Markdown')
        return

    if data == "pl_more":
        # Re-fetch playlist with expanded limit
        url = _get_session_value(context, chat_id, "current_url", user_urls)
        if url:
            await query.edit_message_text("Pobieranie rozszerzonej listy...")
            playlist_info = load_playlist(url, max_items=MAX_PLAYLIST_ITEMS_EXPANDED)
            if not playlist_info or not playlist_info['entries']:
                await query.edit_message_text(
                    "Nie udało się pobrać rozszerzonej listy."
                )
                return
            _set_session_value(context, chat_id, "playlist_data", playlist_info, user_playlist_data)
            msg, reply_markup = _build_playlist_message(playlist_info)
            await query.edit_message_text(msg, reply_markup=reply_markup, parse_mode='Markdown')
        return

    if data.startswith("pl_dl_"):
        await download_playlist(update, context, data)
        return


async def download_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE, callback_data: str):
    """Downloads all items from playlist sequentially."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    playlist = _get_session_value(context, chat_id, "playlist_data", user_playlist_data)
    if not playlist:
        await query.edit_message_text("Sesja playlisty wygasła. Wyślij link ponownie.")
        return

    entries = playlist['entries']

    choice = parse_playlist_download_choice(callback_data)
    media_type = choice.media_type
    format_choice = choice.format_choice

    total = len(entries)
    succeeded = 0
    failed_titles = []

    await query.edit_message_text(
        f"Rozpoczynam pobieranie playlisty ({total} filmów)...\n"
        f"Format: {media_type} {format_choice}"
    )

    for i, entry in enumerate(entries, 1):
        entry_url = entry['url']
        entry_title = entry.get('title', f'Film {i}')

        try:
            status_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=f"[{i}/{total}] Pobieranie: {entry_title}..."
            )

            await _download_single_playlist_item(
                context, chat_id, entry_url, entry_title,
                media_type, format_choice, status_msg
            )
            succeeded += 1

        except Exception as e:
            failed_titles.append(entry_title)
            logging.error(f"Playlist item {i}/{total} failed: {e}")
            try:
                await status_msg.edit_text(
                    f"[{i}/{total}] Błąd: {entry_title}\n{str(e)[:100]}"
                )
            except Exception:
                pass

        # Small delay between items to avoid Telegram rate limits
        if i < total:
            await asyncio.sleep(1)

    # Summary message
    failed = len(failed_titles)
    summary = f"Playlista zakończona!\n\nPobrano: {succeeded}/{total}\n"
    if failed:
        summary += f"Błędy: {failed}\n"
        for title in failed_titles[:5]:
            summary += f"  - {title[:40]}\n"

    await context.bot.send_message(chat_id=chat_id, text=summary)

    # Cleanup session
    _clear_session_value(context, chat_id, "playlist_data", user_playlist_data)


async def _download_single_playlist_item(
    context, chat_id, url, title, media_type, format_choice, status_msg
):
    """Downloads and sends a single playlist item."""
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

    # Telegram Bot API limit: 50MB for file upload
    if file_size_mb > 50:
        raise RuntimeError(
            f"Plik za duży do wysłania ({file_size_mb:.0f} MB, limit Telegram: 50 MB)"
        )

    # Download thumbnail for embed
    loop = asyncio.get_event_loop()
    item_info = await loop.run_in_executor(
        _executor, get_video_info, url
    )
    thumb_path = None
    if item_info:
        thumb_path = await loop.run_in_executor(
            _executor, download_thumbnail, item_info, chat_download_path, True
        )

    try:
        with open(downloaded_file_path, 'rb') as f:
            thumb_file = open(thumb_path, 'rb') if thumb_path else None
            try:
                if media_type == "audio":
                    await context.bot.send_audio(
                        chat_id=chat_id, audio=f, title=title,
                        caption=title[:200],
                        thumbnail=thumb_file,
                        read_timeout=120, write_timeout=120,
                    )
                else:
                    await context.bot.send_video(
                        chat_id=chat_id, video=f, caption=title[:200],
                        thumbnail=thumb_file,
                        read_timeout=120, write_timeout=120,
                    )
            finally:
                if thumb_file:
                    thumb_file.close()
    finally:
        # Always clean up thumbnail and the file
        if thumb_path and os.path.exists(thumb_path):
            try:
                os.remove(thumb_path)
            except OSError:
                pass
        # Always clean up the file
        try:
            os.remove(downloaded_file_path)
        except OSError:
            pass

    record_download_for(context, chat_id, title, url, f"{media_type}_{format_choice}", file_size_mb)
    await status_msg.edit_text(f"[✅] {title}")


async def _show_spotify_summary_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows summary type options for Spotify episodes."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    resolved = _get_session_context_value(context, chat_id, "spotify_resolved", legacy_key="spotify_resolved", default={})
    title = resolved.get('title', 'Odcinek podcastu')

    keyboard = [
        [InlineKeyboardButton("1. Krótkie podsumowanie", callback_data="summary_option_1")],
        [InlineKeyboardButton("2. Szczegółowe podsumowanie", callback_data="summary_option_2")],
        [InlineKeyboardButton("3. Podsumowanie w punktach", callback_data="summary_option_3")],
        [InlineKeyboardButton("4. Podział na zadania", callback_data="summary_option_4")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await safe_edit_message(
        query,
        f"*{escape_md(title)}*\n\nWybierz rodzaj podsumowania:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
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
    """Downloads audio from resolved Spotify source (iTunes direct or YouTube)."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    title = resolved.get('title', 'Podcast episode')

    async def update_status(text):
        await safe_edit_message(query, text)

    await update_status("Pobieranie odcinka podcastu...")

    chat_download_path = os.path.join(DOWNLOAD_PATH, str(chat_id))
    os.makedirs(chat_download_path, exist_ok=True)

    source = resolved['source']
    downloaded_file_path = None

    try:
        await update_status(
            "Pobieranie audio z iTunes..." if source == 'itunes'
            else "Pobieranie audio z YouTube..."
        )
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
                f"Pobieranie zakończone ({file_size_mb:.1f} MB).\n\n"
                f"Rozpoczynanie transkrypcji audio...\n"
                f"To może potrwać kilka minut."
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
                        "Transkrypcja zakończona.\n\n"
                        "Podsumowanie niedostępne — brak klucza CLAUDE_API_KEY.\n"
                        "Wysyłam samą transkrypcję."
                    )
                elif transcript_too_long_for_summary(transcript_text):
                    await update_status(
                        "Transkrypcja zakończona, ale tekst jest zbyt długi na podsumowanie AI.\n\n"
                        "Wysyłam samą transkrypcję."
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
                        summary_header = f"*Podsumowanie: {escape_md(title)}*\n\n"
                        await send_long_message(
                            context.bot,
                            chat_id,
                            summary_result.summary_text,
                            header=summary_header,
                        )

            await update_status("Wysyłanie pliku z transkrypcją...")
            with open(transcript_path, 'rb') as tf:
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=tf,
                    filename=os.path.basename(transcript_path),
                    caption=f"Transkrypcja: {title}"[:200],
                    read_timeout=60,
                    write_timeout=60,
                )

            record_download_for(
                context, chat_id, title,
                _get_session_value(context, chat_id, "current_url", user_urls) or '',
                "spotify_transcribe", file_size_mb,
            )
            _clear_session_context_value(context, chat_id, "spotify_resolved", legacy_key="spotify_resolved")
            cleanup_transcription_artifacts(
                source_media_path=downloaded_file_path,
                output_dir=chat_download_path,
                transcript_prefix=sanitized_title,
            )
            downloaded_file_path = None
        else:
            # Send audio file
            await update_status(f"Wysyłanie pliku ({file_size_mb:.1f} MB)...")
            with open(downloaded_file_path, 'rb') as f:
                await context.bot.send_audio(
                    chat_id=chat_id, audio=f, title=title,
                    caption=title[:200],
                    read_timeout=120, write_timeout=120,
                )
            record_download_for(
                context, chat_id, title,
                _get_session_value(context, chat_id, "current_url", user_urls) or '',
                f"spotify_audio_{audio_format}", file_size_mb,
            )
            _clear_session_context_value(context, chat_id, "spotify_resolved", legacy_key="spotify_resolved")

        await update_status(f"Gotowe: {title}")

    except Exception as e:
        logging.error("Error downloading Spotify episode: %s", e)
        await update_status(f"Błąd pobierania: {str(e)[:200]}")
    finally:
        # Cleanup downloaded file
        if downloaded_file_path and os.path.exists(downloaded_file_path):
            try:
                os.remove(downloaded_file_path)
            except OSError:
                pass


async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    """Returns to main menu."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    platform = _get_session_context_value(context, chat_id, "platform", legacy_key="platform", default="youtube")

    info = get_video_info(url)
    if not info:
        media_name = get_media_label(platform)
        await query.edit_message_text(f"Wystąpił błąd podczas pobierania informacji o {media_name}.")
        return

    title = info.get('title', 'Nieznany tytuł')
    duration = int(info.get('duration') or 0)
    duration_str = f"{duration // 60}:{duration % 60:02d}" if duration else "?"

    keyboard = _build_main_keyboard(platform)

    reply_markup = InlineKeyboardMarkup(keyboard)

    # Show time range info if set
    time_range = _get_session_value(context, chat_id, "time_range", user_time_ranges)
    time_range_info = ""
    if time_range:
        time_range_info = f"\n✂️ Zakres: {time_range['start']} - {time_range['end']}"

    await safe_edit_message(
        query,
        f"*{escape_md(title)}*\nCzas trwania: {duration_str}{time_range_info}\n\nWybierz format do pobrania:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def show_time_range_options(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    """Shows time range selection options."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    info = get_video_info(url)
    if not info:
        media_name = get_media_label(_get_session_context_value(context, chat_id, "platform", legacy_key="platform"))
        await query.edit_message_text(f"Wystąpił błąd podczas pobierania informacji o {media_name}.")
        return

    title = info.get('title', 'Nieznany tytuł')
    duration = int(info.get('duration') or 0)
    duration_str = f"{duration // 60}:{duration % 60:02d}" if duration else "?"

    # Current time range
    time_range = _get_session_value(context, chat_id, "time_range", user_time_ranges)
    current_range = ""
    if time_range:
        current_range = f"\n\n✂️ Aktualny zakres: {time_range['start']} - {time_range['end']}"

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

    reply_markup = InlineKeyboardMarkup(keyboard)

    await safe_edit_message(
        query,
        f"*{escape_md(title)}*\nCzas trwania: {duration_str}{current_range}\n\n"
        f"Wybierz zakres czasowy do pobrania:\n\n"
        f"💡 Możesz też wpisać własny zakres w formacie:\n"
        f"`0:30-5:45` lub `1:00:00-1:30:00`",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def apply_time_range_preset(update: Update, context: ContextTypes.DEFAULT_TYPE, url, preset):
    """Applies a preset time range."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    info = get_video_info(url)
    if not info:
        media_name = get_media_label(_get_session_context_value(context, chat_id, "platform", legacy_key="platform"))
        await query.edit_message_text(f"Wystąpił błąd podczas pobierania informacji o {media_name}.")
        return

    duration = info.get('duration', 0)
    if not duration:
        await query.edit_message_text("Nie można określić czasu trwania filmu.")
        return

    # Parse preset
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

    # Format as MM:SS or HH:MM:SS
    def format_time(seconds):
        if seconds >= 3600:
            return f"{int(seconds // 3600)}:{int((seconds % 3600) // 60):02d}:{int(seconds % 60):02d}"
        return f"{int(seconds // 60)}:{int(seconds % 60):02d}"

    _set_session_value(context, chat_id, "time_range", {
        'start': format_time(start_sec),
        'end': format_time(end_sec),
        'start_sec': start_sec,
        'end_sec': end_sec
    }, user_time_ranges)

    await back_to_main_menu(update, context, url)


async def transcribe_audio_file(update: Update, context: ContextTypes.DEFAULT_TYPE, summary=False, summary_type=None):
    """
    Transcribes an uploaded audio file (MP3 path stored in user_data).

    Reuses the existing transcription pipeline from transcribe_mp3_file().
    """
    query = update.callback_query
    chat_id = update.effective_chat.id

    mp3_path = _get_session_context_value(context, chat_id, "audio_file_path", legacy_key="audio_file_path")
    title = _get_session_context_value(context, chat_id, "audio_file_title", legacy_key="audio_file_title", default='Plik audio')

    if not mp3_path or not os.path.exists(mp3_path):
        _clear_session_context_value(context, chat_id, "audio_file_path", legacy_key="audio_file_path")
        _clear_session_context_value(context, chat_id, "audio_file_title", legacy_key="audio_file_title")
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

    # Progress callback for transcription status updates
    current_status = {"text": ""}

    def progress_callback(status_text):
        current_status["text"] = status_text

    async def run_transcription_with_progress():
        loop = asyncio.get_event_loop()
        future = loop.run_in_executor(
            _executor,
            lambda: transcribe_mp3_file(mp3_path, chat_download_path, progress_callback, language=None)
        )

        last_status = ""
        while not future.done():
            if current_status["text"] and current_status["text"] != last_status:
                last_status = current_status["text"]
                await update_status(f"Transkrypcja w toku...\n\n{last_status}")
            await asyncio.sleep(2)

        return await future

    transcript_path = await run_transcription_with_progress()

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
            with open(transcript_path, 'rb') as f:
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=f,
                    filename=os.path.basename(transcript_path),
                    caption=f"Transkrypcja: {title} (podsumowanie pominięte — tekst zbyt długi)",
                    read_timeout=60,
                    write_timeout=60,
            )
            record_download_for(context, chat_id, title, "audio_upload", "audio_upload_transcription", file_size_mb, None)
            _clear_session_context_value(context, chat_id, "audio_file_path", legacy_key="audio_file_path")
            _clear_session_context_value(context, chat_id, "audio_file_title", legacy_key="audio_file_title")
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

        # Send summary as message(s)
        await send_long_message(
            context.bot, chat_id, summary_result.summary_text,
            header=f"*{escape_md(title)} - {summary_result.summary_type_name}*\n\n"
        )

        await update_status("Wysyłanie pliku z pełną transkrypcją...")

        with open(transcript_path, 'rb') as f:
            await context.bot.send_document(
                chat_id=chat_id,
                document=f,
                filename=os.path.basename(transcript_path),
                caption=f"Pełna transkrypcja: {title}",
                read_timeout=60,
                write_timeout=60,
            )

        record_download_for(
            context, chat_id, title, "audio_upload",
            f"audio_upload_transcription_summary_{summary_type}",
            file_size_mb, None,
        )
        _clear_session_context_value(context, chat_id, "audio_file_path", legacy_key="audio_file_path")
        _clear_session_context_value(context, chat_id, "audio_file_title", legacy_key="audio_file_title")
        await update_status("Transkrypcja i podsumowanie zostały wysłane!")

    else:
        await update_status("Transkrypcja zakończona.\n\nWysyłanie transkrypcji...")

        transcript_result = load_transcript_result(transcript_path)
        display_text = transcript_result.display_text

        # Send transcript in chat if short enough, otherwise file only
        if len(display_text) <= 30000:
            await send_long_message(
                context.bot, chat_id, display_text,
                header=f"*Transkrypcja: {escape_md(title)}*\n\n"
            )

        # Send file as attachment
        with open(transcript_path, 'rb') as f:
            await context.bot.send_document(
                chat_id=chat_id,
                document=f,
                filename=os.path.basename(transcript_path),
                caption=f"Transkrypcja: {title}" if len(display_text) <= 30000
                    else f"Transkrypcja: {title} ({len(display_text):,} znaków — tylko plik)",
                read_timeout=60,
                write_timeout=60,
            )

        # Clean up source MP3 and chunk transcripts
        try:
            cleanup_transcription_artifacts(
                source_media_path=mp3_path,
                output_dir=chat_download_path,
                transcript_prefix=os.path.splitext(os.path.basename(mp3_path))[0],
            )
        except Exception as e:
            logging.error(f"Error deleting audio files: {e}")

        record_download_for(context, chat_id, title, "audio_upload", "audio_upload_transcription", file_size_mb, None)
        _clear_session_context_value(context, chat_id, "audio_file_path", legacy_key="audio_file_path")
        _clear_session_context_value(context, chat_id, "audio_file_title", legacy_key="audio_file_title")
        await update_status("Transkrypcja została wysłana!")


async def show_audio_summary_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays summary type selection for uploaded audio files."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    title = _get_session_context_value(context, chat_id, "audio_file_title", legacy_key="audio_file_title", default='Plik audio')

    keyboard = [
        [InlineKeyboardButton("1. Krótkie podsumowanie", callback_data="audio_summary_option_1")],
        [InlineKeyboardButton("2. Szczegółowe podsumowanie", callback_data="audio_summary_option_2")],
        [InlineKeyboardButton("3. Podsumowanie w punktach", callback_data="audio_summary_option_3")],
        [InlineKeyboardButton("4. Podział zadań na osoby", callback_data="audio_summary_option_4")],
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await safe_edit_message(
        query,
        f"*{escape_md(title)}*\n\nWybierz rodzaj podsumowania:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def show_subtitle_source_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, url, with_summary=False):
    """Shows menu to choose between YouTube subtitles and AI transcription.

    If the video has subtitles, presents a choice. Otherwise falls through
    to the existing AI transcription flow seamlessly.
    """
    query = update.callback_query
    chat_id = update.effective_chat.id

    await safe_edit_message(query, "Sprawdzanie dostępnych napisów...")

    info = get_video_info(url)
    if not info:
        media_name = get_media_label(_get_session_context_value(context, chat_id, "platform", legacy_key="platform"))
        await query.edit_message_text(f"Wystąpił błąd podczas pobierania informacji o {media_name}.")
        return

    title = info.get('title', 'Nieznany tytuł')
    duration = info.get('duration', 0)
    duration_min = duration / 60 if duration else 0
    subs = get_available_subtitles(info)

    # No subtitles available — go directly to AI transcription
    if not subs['has_any']:
        if with_summary:
            # For very long videos, warn that summary may not work
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

    # Build subtitle source selection menu
    summary_suffix = "_sum" if with_summary else ""
    original_lang = subs.get('original_lang')
    keyboard = []

    def _lang_label(lang_code, suffix=""):
        """Build button label with original language marker."""
        label = f"  {lang_code.upper()}"
        if original_lang and lang_code == original_lang:
            label += " (oryginal)"
        if suffix:
            label += f" {suffix}"
        return label

    # Manual subtitles section
    if subs['manual']:
        keyboard.append([InlineKeyboardButton(
            "--- Napisy YouTube (manualne) ---", callback_data="noop"
        )])
        for lang in subs['manual']:
            keyboard.append([InlineKeyboardButton(
                _lang_label(lang), callback_data=f"sub_lang_{lang}{summary_suffix}"
            )])

    # Auto-generated subtitles section
    if subs['auto']:
        keyboard.append([InlineKeyboardButton(
            "--- Napisy automatyczne ---", callback_data="noop"
        )])
        for lang in subs['auto']:
            keyboard.append([InlineKeyboardButton(
                _lang_label(lang, "(auto)"), callback_data=f"sub_auto_{lang}{summary_suffix}"
            )])

    # AI transcription option
    keyboard.append([InlineKeyboardButton(
        "Transkrypcja AI (Whisper)", callback_data=f"sub_src_ai{summary_suffix}"
    )])
    keyboard.append([InlineKeyboardButton("Powrót", callback_data="back")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    # Duration warnings
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
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


def _parse_subtitle_callback(data: str):
    """Parses sub_lang_XX[_sum] or sub_auto_XX[_sum] callback data.

    Returns:
        tuple: (lang, auto, with_summary) or None on invalid data.
    """
    with_summary = data.endswith('_sum')

    if data.startswith('sub_lang_'):
        rest = data[len('sub_lang_'):]
        if with_summary:
            rest = rest[:-4]  # remove '_sum'
        if not rest:
            return None
        return (rest, False, with_summary)

    if data.startswith('sub_auto_'):
        rest = data[len('sub_auto_'):]
        if with_summary:
            rest = rest[:-4]  # remove '_sum'
        if not rest:
            return None
        return (rest, True, with_summary)

    return None


async def _handle_subtitle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, url, data):
    """Routes sub_lang_XX / sub_auto_XX callbacks to subtitle download."""
    parsed = _parse_subtitle_callback(data)
    if not parsed:
        await update.callback_query.edit_message_text("Nieobsługiwana opcja napisów.")
        return

    lang, auto, with_summary = parsed

    if with_summary:
        # Store pending subtitle info for summary type selection
        _set_session_context_value(context, update.effective_chat.id, "subtitle_pending", {
            'url': url,
            'lang': lang,
            'auto': auto,
        }, legacy_key="subtitle_pending")
        await show_subtitle_summary_options(update, context)
    else:
        await handle_subtitle_download(update, context, url, lang, auto, summary=False)


async def _handle_subtitle_summary_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, url, data):
    """Routes sub_sum_N callbacks to subtitle download with summary."""
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
        update, context,
        pending['url'], pending['lang'], pending['auto'],
        summary=True, summary_type=summary_type,
    )


async def show_subtitle_summary_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays summary type selection for subtitle-based transcription."""
    query = update.callback_query

    keyboard = [
        [InlineKeyboardButton("1. Krótkie podsumowanie", callback_data="sub_sum_1")],
        [InlineKeyboardButton("2. Szczegółowe podsumowanie", callback_data="sub_sum_2")],
        [InlineKeyboardButton("3. Podsumowanie w punktach", callback_data="sub_sum_3")],
        [InlineKeyboardButton("4. Podział zadań na osoby", callback_data="sub_sum_4")],
        [InlineKeyboardButton("Powrót", callback_data="back")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await safe_edit_message(
        query,
        "Wybierz rodzaj podsumowania dla napisów:",
        reply_markup=reply_markup,
    )


async def handle_subtitle_download(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url, lang, auto,
    summary=False,
    summary_type=None,
):
    """Downloads YouTube subtitles, parses to text, optionally generates summary.

    This is the subtitle equivalent of download_file() with transcribe=True,
    but skips audio download entirely — only fetches subtitle track.
    """
    query = update.callback_query
    chat_id = update.effective_chat.id

    async def update_status(text):
        await safe_edit_message(query, text)

    sub_type = "automatycznych" if auto else "manualnych"
    await update_status(f"Pobieranie napisów YouTube ({lang.upper()}, {sub_type})...")

    chat_download_path = os.path.join(DOWNLOAD_PATH, str(chat_id))
    os.makedirs(chat_download_path, exist_ok=True)

    # Get video title for filename
    info = get_video_info(url)
    title = info.get('title', 'Nieznany tytuł') if info else 'Nieznany tytuł'

    # Download subtitle file via yt-dlp (no audio download)
    loop = asyncio.get_event_loop()
    sub_path = await loop.run_in_executor(
        _executor,
        lambda: download_subtitles(url, lang, chat_download_path, auto=auto, title=title)
    )

    if not sub_path or not os.path.exists(sub_path):
        await update_status("Nie udało się pobrać napisów. Spróbuj transkrypcji AI.")
        return

    # Parse subtitle file to plain text
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

    # Check if summary was requested but text is too long
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
            context.bot, chat_id, summary_result.summary_text,
            header=f"*{escape_md(title)} - {summary_result.summary_type_name}*\n\n"
        )

        await update_status("Wysyłanie pliku z transkrypcją napisów...")

        with open(transcript_path, 'rb') as f:
            await context.bot.send_document(
                chat_id=chat_id,
                document=f,
                filename=os.path.basename(transcript_path),
                caption=f"Napisy YouTube ({lang.upper()}): {title}",
                read_timeout=60,
                write_timeout=60,
            )

        # Clean up raw subtitle source file (VTT/SRT)
        try:
            os.remove(sub_path)
        except Exception as e:
            logging.error(f"Error deleting subtitle file: {e}")

        record_download_for(
            context, chat_id, title, url,
            f"yt_subtitles_{lang}_summary_{summary_type}",
            0, None, selected_format=f"sub_{lang}",
        )
        await update_status("Napisy i podsumowanie zostały wysłane!")

    else:
        await update_status("Napisy pobrane.\n\nWysyłanie transkrypcji...")

        display_text = transcript_text
        if len(display_text) <= 30000:
            await send_long_message(
                context.bot, chat_id, display_text,
                header=f"*Napisy YouTube ({lang.upper()}): {escape_md(title)}*\n\n"
            )

        with open(transcript_path, 'rb') as f:
            await context.bot.send_document(
                chat_id=chat_id,
                document=f,
                filename=os.path.basename(transcript_path),
                caption=f"Napisy YouTube ({lang.upper()}): {title}" if len(display_text) <= 30000
                    else f"Napisy ({lang.upper()}): {title} ({len(display_text):,} znaków — tylko plik)",
                read_timeout=60,
                write_timeout=60,
            )

        # Clean up subtitle source file
        try:
            os.remove(sub_path)
        except Exception as e:
            logging.error(f"Error deleting subtitle file: {e}")

        record_download_for(
            context, chat_id, title, url,
            f"yt_subtitles_{lang}",
            0, None, selected_format=f"sub_{lang}",
        )
        await update_status("Napisy zostały wysłane!")
