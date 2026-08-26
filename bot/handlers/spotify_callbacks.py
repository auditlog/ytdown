"""Spotify episode download callback flows."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import DOWNLOAD_PATH, get_runtime_value
from bot.handlers.common_ui import escape_md, safe_edit_message, send_long_message
from bot.handlers.transcript_prompt_handlers import offer_custom_transcript_prompt
from bot.mtproto import (
    mtproto_unavailability_reason as _mtproto_unavailability_reason,
    send_audio_mtproto,
    send_video_mtproto,
)
from bot.runtime import record_download_for
from bot.security_limits import TELEGRAM_UPLOAD_LIMIT_MB
from bot.services.spotify_service import download_resolved_audio
from bot.services.spotify_video_service import (
    VideoEpisode,
    download_episode_media,
    get_video_error_message,
)
from bot.services.transcription_service import (
    cleanup_transcription_artifacts,
    generate_summary_artifact,
    load_transcript_result,
    run_transcription_with_progress,
    transcript_too_long_for_summary,
)
from bot.session_context import (
    clear_session_context_value as _clear_session_context_value,
    get_session_value as _get_session_value,
)
from bot.session_store import user_urls
from bot.spotify_video import SpotifyVideoCancelled, SpotifyVideoError, list_profiles


_executor = ThreadPoolExecutor(max_workers=2)

# Telegram rate-limits message edits; throttle progress updates to roughly
# one every this many seconds. Read as a module global (not a bound default
# argument) so tests can monkeypatch it down to 0 for deterministic runs.
_PROGRESS_EDIT_MIN_INTERVAL_SEC = 3.0


class _ThrottledProgressReporter:
    """Decides which Spotify download progress updates reach the user.

    ``record_and_check`` is called from a worker thread (``download_track``
    runs inside an executor), so it must stay a plain, non-async method that
    only touches its own instance state -- no Telegram API calls happen
    here. It returns the text to show, or None when the update should be
    skipped: either because the rendered text has not changed (Telegram
    rejects a no-op edit) or because less than ``min_interval`` seconds have
    passed since the last update that was actually shown.
    """

    def __init__(self, label: str, *, min_interval: float, time_source=time.monotonic):
        self._label = label
        self._min_interval = min_interval
        self._time_source = time_source
        self._last_text: str | None = None
        self._last_edit_time: float | None = None

    def record_and_check(self, done: int, total: int) -> str | None:
        if not total:
            return None
        text = f"Pobieranie {self._label}: {done}/{total}"
        if text == self._last_text:
            return None
        now = self._time_source()
        if self._last_edit_time is not None and now - self._last_edit_time < self._min_interval:
            return None
        self._last_text = text
        self._last_edit_time = now
        return text


async def download_spotify_resolved(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    resolved: dict,
    audio_format: str = "mp3",
    transcribe: bool = False,
    summary: bool = False,
    summary_type: int | None = None,
):
    """Download a resolved Spotify episode, optionally transcribe and summarise."""

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
            await _handle_transcription(
                update, context, chat_id, title, downloaded_file_path,
                file_size_mb, chat_download_path, summary, summary_type,
                update_status,
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


async def download_spotify_video(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    session_data: dict,
    *,
    height: int | None,
):
    """Download a Spotify episode as native video (or native audio) and send it.

    ``height`` selects an H.264 video profile from the manifest; ``None``
    downloads the AAC audio track only. Both paths share the same
    manifest-driven pipeline in ``bot.services.spotify_video_service``.
    """

    query = update.callback_query
    chat_id = update.effective_chat.id
    title = session_data.get("title", "Spotify episode")

    async def update_status(text):
        await safe_edit_message(query, text)

    chat_download_path = os.path.join(DOWNLOAD_PATH, str(chat_id))
    os.makedirs(chat_download_path, exist_ok=True)

    label = "wideo" if height else "audio"
    await update_status(f"Pobieranie {label} ze Spotify...")

    # download_track invokes progress_cb from a worker thread (it runs
    # inside _executor), while editing a Telegram message is async and must
    # happen on the event loop thread. The running loop is captured here,
    # before dispatching any work to the executor, and progress_cb bridges
    # back to it via run_coroutine_threadsafe rather than awaiting or
    # touching the bot API directly from the worker thread.
    loop = asyncio.get_event_loop()
    reporter = _ThrottledProgressReporter(label, min_interval=_PROGRESS_EDIT_MIN_INTERVAL_SEC)

    def progress_cb(done, total):
        text = reporter.record_and_check(done, total)
        if text is not None:
            asyncio.run_coroutine_threadsafe(update_status(text), loop)

    downloaded_path = None
    try:
        # Profiles are recomputed from the stored manifest rather than kept
        # in the session, since Profile dataclass instances are not
        # JSON-friendly. Doing this inside the try block means a manifest
        # whose shape changed underneath us (Spotify API drift) surfaces as
        # the same "api_changed" Polish message as every other manifest
        # failure, instead of an unhandled exception.
        manifest = session_data["manifest"]
        episode = VideoEpisode(
            episode_id=session_data["episode_id"],
            title=title,
            show_name=session_data.get("show_name", ""),
            duration_ms=int(session_data.get("duration_ms") or 0),
            manifest=manifest,
            profiles=list_profiles(manifest),
            subtitle_languages=session_data.get("subtitle_languages", []),
        )

        downloaded_path = await download_episode_media(
            episode=episode,
            height=height,
            output_dir=chat_download_path,
            executor=_executor,
            progress_cb=progress_cb,
        )

        file_size_mb = os.path.getsize(downloaded_path) / (1024 * 1024)
        await update_status(f"Pobieranie zakończone ({file_size_mb:.1f} MB).\n\nWysyłanie...")

        if file_size_mb > TELEGRAM_UPLOAD_LIMIT_MB:
            reason = _mtproto_unavailability_reason()
            if reason is not None:
                await update_status(
                    f"Plik za duży dla Bot API ({file_size_mb:.0f} MB, "
                    f"limit: {TELEGRAM_UPLOAD_LIMIT_MB} MB).\n{reason}"
                )
                return
            if height is None:
                ok = await send_audio_mtproto(
                    chat_id, downloaded_path, title=title, caption=title[:200]
                )
            else:
                ok = await send_video_mtproto(chat_id, downloaded_path, caption=title[:200])
            if not ok:
                await update_status("Wysyłanie pliku przez MTProto nie powiodło się.")
                return
        else:
            with open(downloaded_path, "rb") as file_obj:
                if height is None:
                    await context.bot.send_audio(
                        chat_id=chat_id,
                        audio=file_obj,
                        title=title,
                        caption=title[:200],
                        read_timeout=120,
                        write_timeout=120,
                    )
                else:
                    await context.bot.send_video(
                        chat_id=chat_id,
                        video=file_obj,
                        caption=title[:200],
                        read_timeout=120,
                        write_timeout=120,
                    )

        record_download_for(
            context,
            chat_id,
            title,
            _get_session_value(context, chat_id, "current_url", user_urls) or "",
            f"spotify_native_{'audio' if height is None else str(height) + 'p'}",
            file_size_mb,
        )
        _clear_session_context_value(context, chat_id, "spotify_video", legacy_key="spotify_video")
        await update_status(f"Gotowe: {title}")

    except SpotifyVideoCancelled:
        await update_status("Pobieranie anulowane.")
    except SpotifyVideoError as exc:
        await update_status(get_video_error_message(str(exc)))
    except Exception as exc:
        logging.error("Error downloading Spotify video: %s", exc)
        await update_status(f"Błąd pobierania: {str(exc)[:200]}")
    finally:
        if downloaded_path and os.path.exists(downloaded_path):
            try:
                os.remove(downloaded_path)
            except OSError:
                pass


async def _handle_transcription(
    update, context, chat_id, title, downloaded_file_path,
    file_size_mb, chat_download_path, summary, summary_type, update_status,
):
    """Transcribe and optionally summarise a downloaded Spotify episode."""

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
        await _maybe_generate_summary(
            context, chat_id, title, transcript_text, sanitized_title,
            chat_download_path, update_status,
            summary_type=summary_type,
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
    await offer_custom_transcript_prompt(
        context,
        chat_id=chat_id,
        requester_id=update.effective_user.id,
        transcript_path=transcript_path,
        title=title,
    )


async def _maybe_generate_summary(
    context, chat_id, title, transcript_text, sanitized_title,
    chat_download_path, update_status, *, summary_type,
):
    """Generate an AI summary if keys are available and text is short enough."""

    if not get_runtime_value("CLAUDE_API_KEY", ""):
        await update_status(
            "Transkrypcja zakończona.\n\nPodsumowanie niedostępne — brak klucza CLAUDE_API_KEY.\nWysyłam samą transkrypcję."
        )
        return
    if transcript_too_long_for_summary(transcript_text):
        await update_status(
            "Transkrypcja zakończona, ale tekst jest zbyt długi na podsumowanie AI.\n\nWysyłam samą transkrypcję."
        )
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
    if summary_result:
        await send_long_message(
            context.bot,
            chat_id,
            summary_result.summary_text,
            header=f"*Podsumowanie: {escape_md(title)}*\n\n",
        )
