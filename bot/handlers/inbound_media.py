"""Inbound media and link entry handlers."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from bot.config import DOWNLOAD_PATH, get_runtime_value
from bot.downloader_media import get_instagram_post_info, is_photo_entry
from bot.download_errors import build_media_error_message
from bot.downloader_metadata import get_video_info, get_video_info_with_error
from bot.downloader_playlist import is_playlist_url, is_pure_playlist_url
from bot.security_limits import FFMPEG_TIMEOUT, MAX_FILE_SIZE_MB, MAX_PLAYLIST_ITEMS, RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW
from bot.security_pin import get_block_remaining_seconds, is_user_blocked
from bot.security_policy import (
    detect_platform,
    estimate_file_size,
    extract_url_from_text,
    get_media_label,
    normalize_url,
    validate_url,
)
from bot.security_throttling import check_rate_limit
from bot.services.auth_service import store_pending_action
from bot.runtime import get_app_runtime
from bot.services.playlist_service import build_playlist_message, load_playlist
from bot.session_store import block_until, user_playlist_data, user_time_ranges, user_urls
from bot.services.spotify_service import (
    build_episode_caption_data,
    get_resolution_error_message,
    resolve_episode,
)
from bot.services.spotify_video_service import (
    build_quality_options,
    get_video_error_message,
    resolve_video_episode,
)
from bot.spotify_video import SpotifyVideoError
from bot.handlers.common_ui import (
    build_instagram_photo_keyboard as _build_instagram_photo_keyboard,
    build_main_keyboard as _build_main_keyboard,
    build_spotify_episode_keyboard,
    escape_md,
)
from bot.handlers.time_range import parse_time_range as _shared_parse_time_range
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
from bot.handlers.inbound_audio import (
    MTPROTO_MAX_FILE_SIZE_MB,
    TELEGRAM_DOWNLOAD_LIMIT_MB,
    _extract_audio_info,
    extracted_process_audio_file,
)
from bot.handlers.inbound_video import (
    _extract_video_info,
    extracted_process_video_file,
)
from bot.handlers.transcript_prompt_handlers import handle_pending_transcript_prompt


async def handle_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from bot.handlers.command_access import handle_pin as _handle_pin

    return await _handle_pin(update, context)


def _is_authorized(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    from bot.handlers.command_access import _is_authorized as _shared_is_authorized

    return _shared_is_authorized(context, user_id)


def parse_time_range(text: str) -> dict | None:
    return _shared_parse_time_range(text)



async def process_youtube_link(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    return await extracted_process_youtube_link(update, context, url)


async def process_playlist_link(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    return await extracted_process_playlist_link(update, context, url)


async def _process_spotify_episode(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    return await extracted_process_spotify_episode(update, context, url)


async def process_audio_file(update: Update, context: ContextTypes.DEFAULT_TYPE, audio_info: dict | None = None):
    return await extracted_process_audio_file(update, context, audio_info)


async def process_video_file(update: Update, context: ContextTypes.DEFAULT_TYPE, video_info: dict | None = None):
    return await extracted_process_video_file(update, context, video_info)


async def handle_audio_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle voice messages, audio files, and audio documents."""
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


async def handle_video_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle video file uploads and offer transcription."""
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



async def handle_youtube_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles YouTube links and custom time range input."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    message_text = update.message.text

    pin_handled = await handle_pin(update, context)
    if pin_handled:
        return

    if not _is_authorized(context, user_id):
        # Extract URL up-front so that the replay after successful PIN auth
        # (command_access replays pending_action via process_youtube_link,
        # which expects a clean URL) gets the same input as the authorized path.
        pending_payload = extract_url_from_text(message_text) or message_text
        store_pending_action(_get_auth_state(context, chat_id), kind="url", payload=pending_payload)
        await update.message.reply_text(
            "Wymagane uwierzytelnienie!\n\n"
            "Proszę podaj 8-cyfrowy kod PIN, aby uzyskać dostęp."
        )
        return

    if await handle_pending_transcript_prompt(update, context):
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

    # Extract the first supported URL if the message contains descriptive text
    # around the link (e.g. "please download: https://youtu.be/...").
    extracted_url = extract_url_from_text(message_text)
    if extracted_url:
        message_text = extracted_url

    if "castbox.fm" in message_text:
        import asyncio

        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            message_text = await loop.run_in_executor(executor, normalize_url, message_text)

    if not validate_url(message_text):
        from bot.platforms import PLATFORMS

        platform_lines = "\n".join(
            f"- {p.display_name} ({p.domains[0]})" for p in PLATFORMS
        )
        await update.message.reply_text(
            "Nieprawidłowy URL!\n\n"
            "Obsługiwane platformy:\n"
            f"{platform_lines}"
        )
        return

    await process_youtube_link(update, context, message_text)


def _build_playlist_message(playlist_info: dict, context=None) -> tuple[str, InlineKeyboardMarkup]:
    """Compatibility wrapper around the playlist service message builder.

    When ``context`` is supplied, the live ``AppRuntime`` is queried so that
    ``archive_available`` reflects whether 7-zip is present on this host.
    """
    runtime = get_app_runtime(context) if context is not None else None
    archive_available = runtime.archive_available if runtime is not None else False
    return build_playlist_message(playlist_info, archive_available=archive_available)


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
    msg, reply_markup = _build_playlist_message(playlist_info, context=context)
    await progress_message.edit_text(msg, reply_markup=reply_markup, parse_mode="Markdown")


async def extracted_process_spotify_episode(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    """Resolves a Spotify episode URL and shows download options.

    Video resolution runs first because it needs only a session cookie, while
    the legacy iTunes/YouTube path needs Spotify Web API credentials. Both
    may apply, so the keyboard is assembled from whichever source actually
    resolved -- a button that cannot work under the current configuration is
    never shown. If neither resolves, the user gets the most specific error
    available.
    """
    chat_id = update.effective_chat.id
    progress_message = await update.message.reply_text("Spotify: sprawdzanie odcinka...")

    _set_session_value(context, chat_id, "current_url", url, user_urls)

    video_episode = None
    video_error = None
    try:
        video_episode = await asyncio.get_event_loop().run_in_executor(
            None, resolve_video_episode, url
        )
    except SpotifyVideoError as exc:
        video_error = str(exc)
    except Exception:
        # Spotify drift does not always reach us as a SpotifyVideoError, and
        # anything that escapes this handler leaves the user's "Spotify:
        # sprawdzanie odcinka..." message frozen forever with no error at
        # all -- the failure mode design spec 10 rules out, on the breakage
        # spec 12 calls most likely. At the *resolution* stage "api_changed"
        # is the honest description of any unexpected failure, which is why
        # the catch is broadened only here: the download and send paths have
        # their own distinguishable failures (ffmpeg_missing, a failed
        # segment fetch) that must keep their own identity.
        logging.exception("Unexpected failure resolving Spotify video episode")
        video_error = "api_changed"

    try:
        resolved = await resolve_episode(url)
    except Exception:
        # The legacy iTunes/YouTube resolver sat outside the guard entirely,
        # so anything it raised froze the status message the same way. Its
        # failure is not Spotify video API drift, so it stays reported as an
        # unavailable fallback rather than borrowing the "api_changed" text.
        logging.exception("Unexpected failure resolving Spotify audio fallback")
        resolved = None

    fallback_available = resolved is not None and resolved.get("source") in ("itunes", "youtube")

    # Resolve spotify_video/spotify_resolved to match THIS attempt's outcome
    # unconditionally, before any early return. A chat's session can already
    # hold a previous episode's data (including a manifest with signed CDN
    # URLs); if a later attempt fails on both paths, that stale episode must
    # not survive untouched -- it would otherwise get handed to Task 12's
    # transcript flow as if it belonged to the current URL.
    if video_episode is not None:
        _set_session_context_value(
            context,
            chat_id,
            "spotify_video",
            {
                "episode_id": video_episode.episode_id,
                "title": video_episode.title,
                "show_name": video_episode.show_name,
                "duration_ms": video_episode.duration_ms,
                "manifest": video_episode.manifest,
                "subtitle_languages": video_episode.subtitle_languages,
            },
            legacy_key="spotify_video",
        )
    else:
        _clear_session_context_value(context, chat_id, "spotify_video", legacy_key="spotify_video")

    if fallback_available:
        _set_session_context_value(
            context, chat_id, "spotify_resolved", resolved, legacy_key="spotify_resolved"
        )
    else:
        _clear_session_context_value(context, chat_id, "spotify_resolved", legacy_key="spotify_resolved")

    if video_episode is None and not fallback_available:
        error_message = (
            get_video_error_message(video_error)
            if video_error
            else get_resolution_error_message(resolved) or "Nie udało się przygotować tego odcinka."
        )
        await progress_message.edit_text(error_message)
        return

    quality_options = build_quality_options(video_episode) if video_episode else []

    if video_episode is not None:
        title = video_episode.title
        show_name = video_episode.show_name
        duration_seconds = video_episode.duration_ms // 1000
        duration_str = f"{duration_seconds // 60}:{duration_seconds % 60:02d}" if duration_seconds else "?"
        source_line = "Źródło: Spotify (wideo)"
    else:
        caption_data = build_episode_caption_data(resolved)
        title = caption_data["title"]
        show_name = caption_data["show_name"]
        duration_str = caption_data["duration_str"]
        source_line = f"Źródło audio: {caption_data['source_label']}"

    show_info = f"\nPodcast: {escape_md(show_name)}" if show_name else ""
    # Video resolution failed but the legacy audio path still worked -- tell
    # the user why native video/audio buttons are missing instead of staying
    # silent about it.
    notice = f"\n\n{get_video_error_message(video_error)}" if video_error and fallback_available else ""

    reply_markup = InlineKeyboardMarkup(
        build_spotify_episode_keyboard(
            quality_options=quality_options,
            has_native_audio=video_episode is not None,
            has_fallback_audio=fallback_available,
        )
    )
    await progress_message.edit_text(
        f"*{escape_md(title)}*{show_info}\n"
        f"Czas trwania: {duration_str}\n"
        f"{source_line}{notice}\n\n"
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
    _clear_session_context_value(context, chat_id, "spotify_video", legacy_key="spotify_video")
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
    info, fetch_error = get_video_info_with_error(url)
    if not info:
        # Surface why it failed (age gate, expired cookies, geo block, ...) instead of
        # one generic sentence that leaves the user guessing whether to retry.
        await progress_message.edit_text(build_media_error_message(media_name, fetch_error))
        return

    title = info.get("title", "Nieznany tytuł")
    duration = int(info.get("duration") or 0)
    duration_str = f"{duration // 60}:{duration % 60:02d}" if duration else "?"
    estimated_size = estimate_file_size(info)
    size_warning = ""

    from bot.platforms import get_platform

    config = get_platform(platform)
    is_podcast = config.is_podcast if config else False
    large_file = bool(estimated_size and estimated_size > MAX_FILE_SIZE_MB)
    if large_file:
        size_warning = (
            f"\n*Uwaga:* Szacowany rozmiar najlepszej jakości: {estimated_size:.1f} MB "
            f"(limit: {MAX_FILE_SIZE_MB} MB)\n"
        )
        keyboard = _build_main_keyboard(platform, large_file=True)
    else:
        keyboard = _build_main_keyboard(platform)

    time_range = _get_session_value(context, chat_id, "time_range", user_time_ranges)
    time_range_info = f"\n✂️ Zakres: {time_range['start']} - {time_range['end']}" if time_range else ""

    # Explain what "najwyższa" and "średnia" mean — only relevant when those
    # labels are shown (non-podcast, non-large-file flow).
    quality_hint = ""
    if not is_podcast and not large_file:
        quality_hint = (
            "\n_Najwyższa_ = najlepsza dostępna rozdzielczość (do 4K/2160p)."
            "  _Średnia_ = 720p HD.\n"
        )

    await progress_message.edit_text(
        f"*{escape_md(title)}*\nCzas trwania: {duration_str}{size_warning}{time_range_info}{quality_hint}\n"
        f"Wybierz format do pobrania:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )

