"""
Telegram callback compatibility module.

This module keeps the legacy callback import surface stable while delegating
feature logic to extracted handler modules.
"""

from __future__ import annotations

import asyncio

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.config import get_runtime_value
from bot.downloader_metadata import get_video_info
from bot.downloader_validation import is_valid_audio_format, is_valid_ytdlp_format_id
from bot.handlers import download_callbacks as _download_callbacks_module
from bot.handlers import media_extras_callbacks as _media_extras_callbacks_module
from bot.handlers import playlist_callbacks as _playlist_callbacks_module
from bot.handlers import transcription_callbacks as _transcription_callbacks_module
from bot.handlers.callback_parsing import parse_download_callback, parse_summary_option
from bot.handlers.common_ui import (
    build_main_keyboard,
    escape_md as _shared_escape_md,
    format_bytes,
    format_eta,
    safe_edit_message,
    send_long_message,
)
from bot.handlers.download_callbacks import (
    apply_time_range_preset as _extracted_apply_time_range_preset,
    back_to_main_menu as _extracted_back_to_main_menu,
    create_progress_hook as _extracted_create_progress_hook,
    download_file as _extracted_download_file,
    download_spotify_resolved as _extracted_download_spotify_resolved,
    show_time_range_options as _extracted_show_time_range_options,
)
from bot.handlers.media_extras_callbacks import (
    _handle_instagram_download as _extracted_handle_instagram_download,
    _show_spotify_summary_options as _extracted_show_spotify_summary_options,
    handle_formats_list as _extracted_handle_formats_list,
)
from bot.handlers.playlist_callbacks import (
    download_playlist as _extracted_download_playlist,
    handle_playlist_callback as _extracted_handle_playlist_callback,
)
from bot.handlers.transcription_callbacks import (
    _handle_subtitle_callback as _extracted_handle_subtitle_callback,
    _handle_subtitle_summary_callback as _extracted_handle_subtitle_summary_callback,
    _parse_subtitle_callback as _extracted_parse_subtitle_callback,
    handle_subtitle_download as _extracted_handle_subtitle_download,
    show_audio_summary_options as _extracted_show_audio_summary_options,
    show_subtitle_source_menu as _extracted_show_subtitle_source_menu,
    show_subtitle_summary_options as _extracted_show_subtitle_summary_options,
    show_summary_options as _extracted_show_summary_options,
    transcribe_audio_file as _extracted_transcribe_audio_file,
)
from bot.security_policy import get_media_label, normalize_url
from bot.security_throttling import check_rate_limit
from bot.services.playlist_service import build_playlist_message, load_playlist
from bot.services.spotify_service import download_resolved_audio
from bot.session_context import (
    clear_session_value as _clear_session_value,
    get_session_context_value as _get_session_context_value,
    get_session_value as _get_session_value,
)
from bot.session_store import download_progress as _download_progress, user_time_ranges, user_urls


def escape_md(text: str) -> str:
    """Compatibility wrapper for shared Markdown escaping."""

    return _shared_escape_md(text)


def _build_main_keyboard(platform: str, large_file: bool = False) -> list:
    """Compatibility wrapper for shared command/callback keyboard builder."""

    return build_main_keyboard(platform, large_file=large_file)


def _build_playlist_message(playlist_info: dict) -> tuple[str, InlineKeyboardMarkup]:
    """Compatibility wrapper for playlist menu rendering."""

    return build_playlist_message(playlist_info)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callback queries and route them through extracted flows."""

    query = update.callback_query
    await query.answer()
    data = query.data

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if not check_rate_limit(user_id):
        await query.edit_message_text("Przekroczono limit requestów. Spróbuj ponownie za chwilę.")
        return

    if data.startswith("pl_"):
        await handle_playlist_callback(update, context, data)
        return

    if data == "audio_transcribe":
        await transcribe_audio_file(update, context)
        return
    if data == "audio_transcribe_summary":
        await show_audio_summary_options(update, context)
        return
    if data.startswith("audio_summary_option_"):
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

    if "castbox.fm" in url:
        url = await asyncio.get_event_loop().run_in_executor(None, normalize_url, url)

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
        platform = _get_session_context_value(context, chat_id, "platform", legacy_key="platform")

        if platform == "spotify":
            resolved = _get_session_context_value(context, chat_id, "spotify_resolved", legacy_key="spotify_resolved")
            if not resolved:
                await query.edit_message_text("Sesja Spotify wygasła. Wyślij link ponownie.")
                return
            await download_spotify_resolved(update, context, resolved, selected_format, transcribe=False)
            return

        if media_type == "audio" and mode == "format_id":
            if not is_valid_ytdlp_format_id(selected_format):
                await query.edit_message_text("Nieobsługiwany format. Spróbuj wybrać format ponownie.")
                return
            await download_file(update, context, "audio", selected_format, url, use_format_id=True)
            return
        if media_type == "audio":
            if not is_valid_audio_format(selected_format):
                await query.edit_message_text("Nieobsługiwany format audio. Spróbuj wybrać format ponownie.")
                return
            await download_file(update, context, "audio", selected_format, url)
            return
        if media_type == "video":
            if not is_valid_ytdlp_format_id(selected_format):
                await query.edit_message_text("Nieobsługiwany format. Spróbuj wybrać format ponownie.")
                return
            await download_file(update, context, "video", selected_format, url)
            return

        await query.edit_message_text("Nieobsługiwany format. Spróbuj wybrać format ponownie.")
        return

    if data == "transcribe_summary":
        if _get_session_context_value(context, chat_id, "platform", legacy_key="platform") == "spotify":
            await _show_spotify_summary_options(update, context)
        else:
            await show_subtitle_source_menu(update, context, url, with_summary=True)
        return

    if data.startswith("summary_option_"):
        option = parse_summary_option(data)
        if option is None:
            await query.edit_message_text("Nieobsługiwana opcja podsumowania.")
            return

        if _get_session_context_value(context, chat_id, "platform", legacy_key="platform") == "spotify":
            resolved = _get_session_context_value(context, chat_id, "spotify_resolved", legacy_key="spotify_resolved")
            if resolved:
                await download_spotify_resolved(
                    update,
                    context,
                    resolved,
                    "mp3",
                    transcribe=True,
                    summary=True,
                    summary_type=option,
                )
            else:
                await query.edit_message_text("Sesja Spotify wygasła. Wyślij link ponownie.")
        else:
            await download_file(update, context, "audio", "mp3", url, transcribe=True, summary=True, summary_type=option)
        return

    if data == "transcribe":
        if _get_session_context_value(context, chat_id, "platform", legacy_key="platform") == "spotify":
            resolved = _get_session_context_value(context, chat_id, "spotify_resolved", legacy_key="spotify_resolved")
            if resolved:
                await download_spotify_resolved(update, context, resolved, "mp3", transcribe=True)
            else:
                await query.edit_message_text("Sesja Spotify wygasła. Wyślij link ponownie.")
        else:
            await show_subtitle_source_menu(update, context, url, with_summary=False)
        return

    if data == "sub_src_ai":
        await download_file(update, context, "audio", "mp3", url, transcribe=True)
        return
    if data == "sub_src_ai_sum":
        await show_summary_options(update, context, url)
        return
    if data.startswith("sub_lang_") or data.startswith("sub_auto_"):
        await _handle_subtitle_callback(update, context, url, data)
        return
    if data.startswith("sub_sum_"):
        await _handle_subtitle_summary_callback(update, context, url, data)
        return
    if data == "formats":
        await handle_formats_list(update, context, url)
        return
    if data == "time_range":
        await show_time_range_options(update, context, url)
        return
    if data == "time_range_clear":
        _clear_session_value(context, chat_id, "time_range", user_time_ranges)
        await back_to_main_menu(update, context, url)
        return
    if data.startswith("time_range_preset_"):
        await apply_time_range_preset(update, context, url, data.replace("time_range_preset_", ""))
        return
    if data == "back":
        await back_to_main_menu(update, context, url)


def _sync_download_callback_dependencies() -> None:
    """Keep extracted download callback helpers aligned with this module globals."""

    _download_callbacks_module.get_video_info = get_video_info
    _download_callbacks_module.load_playlist = load_playlist
    _download_callbacks_module.download_resolved_audio = download_resolved_audio
    _download_callbacks_module.back_to_main_menu = back_to_main_menu


def _sync_playlist_callback_dependencies() -> None:
    """Keep extracted playlist callback helpers aligned with this module globals."""

    _playlist_callbacks_module.get_video_info = get_video_info
    _playlist_callbacks_module.load_playlist = load_playlist


def _sync_media_extras_dependencies() -> None:
    """Keep extracted media-extra callback helpers aligned with this module globals."""

    _media_extras_callbacks_module.get_video_info = get_video_info


def _sync_transcription_callback_dependencies() -> None:
    """Keep extracted transcription callback helpers aligned with this module globals."""

    _transcription_callbacks_module.get_video_info = get_video_info
    _transcription_callbacks_module.get_runtime_value = get_runtime_value
    _transcription_callbacks_module.show_summary_options = show_summary_options
    _transcription_callbacks_module.download_file = download_file


def create_progress_hook(chat_id):
    return _extracted_create_progress_hook(chat_id)


async def _handle_instagram_download(update: Update, context: ContextTypes.DEFAULT_TYPE, url, callback_data: str):
    _sync_media_extras_dependencies()
    return await _extracted_handle_instagram_download(update, context, url, callback_data)


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
    """Compatibility wrapper for extracted download flow.

    User-facing fallback remains: "Wystąpił błąd podczas pobierania".
    """

    _sync_download_callback_dependencies()
    return await _extracted_download_file(
        update,
        context,
        type,
        format,
        url,
        transcribe=transcribe,
        summary=summary,
        summary_type=summary_type,
        use_format_id=use_format_id,
        audio_quality=audio_quality,
    )


async def handle_formats_list(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    _sync_media_extras_dependencies()
    return await _extracted_handle_formats_list(update, context, url)


async def handle_playlist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    _sync_playlist_callback_dependencies()
    return await _extracted_handle_playlist_callback(update, context, data)


async def download_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE, callback_data: str):
    _sync_playlist_callback_dependencies()
    return await _extracted_download_playlist(update, context, callback_data)


async def _show_spotify_summary_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _sync_media_extras_dependencies()
    return await _extracted_show_spotify_summary_options(update, context)


async def download_spotify_resolved(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    resolved: dict,
    audio_format: str = "mp3",
    transcribe: bool = False,
    summary: bool = False,
    summary_type: int | None = None,
):
    _sync_download_callback_dependencies()
    return await _extracted_download_spotify_resolved(
        update,
        context,
        resolved,
        audio_format=audio_format,
        transcribe=transcribe,
        summary=summary,
        summary_type=summary_type,
    )


async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    _sync_download_callback_dependencies()
    return await _extracted_back_to_main_menu(update, context, url)


async def show_time_range_options(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    _sync_download_callback_dependencies()
    return await _extracted_show_time_range_options(update, context, url)


async def apply_time_range_preset(update: Update, context: ContextTypes.DEFAULT_TYPE, url, preset):
    _sync_download_callback_dependencies()
    return await _extracted_apply_time_range_preset(update, context, url, preset)


async def show_summary_options(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    _sync_transcription_callback_dependencies()
    return await _extracted_show_summary_options(update, context, url)


async def transcribe_audio_file(update: Update, context: ContextTypes.DEFAULT_TYPE, summary=False, summary_type=None):
    _sync_transcription_callback_dependencies()
    return await _extracted_transcribe_audio_file(update, context, summary=summary, summary_type=summary_type)


async def show_audio_summary_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _sync_transcription_callback_dependencies()
    return await _extracted_show_audio_summary_options(update, context)


async def show_subtitle_source_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, url, with_summary=False):
    _sync_transcription_callback_dependencies()
    return await _extracted_show_subtitle_source_menu(update, context, url, with_summary=with_summary)


def _parse_subtitle_callback(data: str):
    return _extracted_parse_subtitle_callback(data)


async def _handle_subtitle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, url, data):
    _sync_transcription_callback_dependencies()
    return await _extracted_handle_subtitle_callback(update, context, url, data)


async def _handle_subtitle_summary_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, url, data):
    _sync_transcription_callback_dependencies()
    return await _extracted_handle_subtitle_summary_callback(update, context, url, data)


async def show_subtitle_summary_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _sync_transcription_callback_dependencies()
    return await _extracted_show_subtitle_summary_options(update, context)


async def handle_subtitle_download(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url,
    lang,
    auto,
    summary=False,
    summary_type=None,
):
    _sync_transcription_callback_dependencies()
    return await _extracted_handle_subtitle_download(
        update,
        context,
        url,
        lang,
        auto,
        summary=summary,
        summary_type=summary_type,
    )
