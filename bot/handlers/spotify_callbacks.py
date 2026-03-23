"""Spotify episode download callback flows."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import DOWNLOAD_PATH, get_runtime_value
from bot.handlers.common_ui import escape_md, safe_edit_message, send_long_message
from bot.runtime import record_download_for
from bot.services.spotify_service import download_resolved_audio
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


_executor = ThreadPoolExecutor(max_workers=2)


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
