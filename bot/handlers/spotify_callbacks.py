"""Spotify audio and native video download callback flows."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import DOWNLOAD_PATH, get_runtime_value
from bot.downloader_validation import sanitize_filename
from bot.handlers.audio_delivery import AudioDeliveryError, TRIM_AVAILABLE_HINT, send_audio_with_trim
from bot.handlers.common_ui import escape_md, safe_edit_message, send_long_message
from bot.handlers.trim_callbacks import offer_trim_after_download
from bot.handlers.transcript_prompt_handlers import offer_custom_transcript_prompt
from bot.mtproto import (
    mtproto_unavailability_reason as _mtproto_unavailability_reason,
    send_video_mtproto,
)
from bot.runtime import record_download_for
from bot.security_limits import TELEGRAM_UPLOAD_LIMIT_MB
from bot.services.spotify_service import download_resolved_audio
from bot.services.spotify_video_service import (
    VideoEpisode,
    download_episode_media,
    get_download_error_message,
    transcript_from_subtitles,
)
from bot.services.transcription_service import (
    MISSING_CLAUDE_KEY_TEXT,
    MISSING_GROQ_KEY_TEXT,
    SUMMARY_FAILED_KEEP_TRANSCRIPT_TEXT,
    cleanup_transcription_artifacts,
    generate_summary_artifact,
    load_transcript_result,
    missing_transcription_key_message,
    run_transcription_with_progress,
    transcript_too_long_for_summary,
)
from bot.session_context import (
    clear_session_context_value_if as _clear_session_context_value_if,
    get_session_value as _get_session_value,
)
from bot.session_store import user_urls
from bot.spotify_video import SpotifyVideoCancelled, SpotifyVideoError, list_profiles


_executor = ThreadPoolExecutor(max_workers=2)

# Telegram rate-limits message edits; throttle progress updates to roughly
# one every this many seconds. Read as a module global (not a bound default
# argument) so tests can monkeypatch it down to 0 for deterministic runs.
_PROGRESS_EDIT_MIN_INTERVAL_SEC = 3.0

# What the user is told during the download phases that report no per-segment
# progress of their own. download_episode_media names the phase in English;
# the wording the user sees belongs here, with the rest of the UI text.
_PHASE_MESSAGES = {
    "audio": "Pobieranie ścieżki dźwiękowej...",
    "mux": "Łączenie obrazu z dźwiękiem...",
}


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


class _SpotifyProgressBridge:
    """Bridges a download's worker-thread progress_cb calls to throttled,
    correctly-ordered Telegram status edits.

    download_track/download_episode_media invoke ``progress_cb``
    synchronously from a worker thread (they run inside an executor),
    while editing a Telegram message is async and must happen on the
    event loop thread. ``callback`` -- passed as the pipeline's
    ``progress_cb`` -- bridges back to the loop captured at construction
    time via ``run_coroutine_threadsafe`` rather than awaiting or touching
    the bot API directly from the worker thread. The loop is captured
    here, before any caller dispatches work to the executor.

    Call ``await bridge.drain()`` in a ``finally`` before issuing any
    further status edit (whether control proceeds to a success path or an
    except block) -- see `drain` for why that ordering matters. One
    instance is scoped to a single download; construct a fresh one per
    call, same as `_ThrottledProgressReporter`.
    """

    def __init__(self, label: str, *, update_status, min_interval: float):
        self._reporter = _ThrottledProgressReporter(label, min_interval=min_interval)
        self._update_status = update_status
        self._loop = asyncio.get_event_loop()
        # Tracks every scheduled-but-not-yet-confirmed-finished progress
        # edit, not just the most recent one: an edit that takes longer
        # than the throttle window can still be in flight when the *next*
        # progress_cb call schedules another one, and every one of them
        # (not just whichever happens to be last) must be waited on before
        # the caller's first post-download status edit -- see `drain`.
        # Waiting is what guarantees ordering; a plain flag checked inside
        # the scheduled coroutine does not work for this
        # (run_coroutine_threadsafe defers creating its Task through a
        # call_soon_threadsafe hop, and FIFO callback ordering guarantees
        # that deferred Task's first step runs before this coroutine can
        # resume and flip any such flag -- confirmed with a standalone
        # repro of the real cross-thread, run_in_executor call shape). A
        # shared asyncio.Lock would work too, for the same FIFO reason: by
        # the time this coroutine resumes to acquire it, every progress
        # task scheduled so far has either already acquired it or queued
        # as a waiter ahead of us, so the acquire here is never truly
        # uncontended in this call shape. Concrete futures were chosen
        # instead because they say directly what is actually needed --
        # wait for exactly the N edits that were dispatched -- without
        # inventing an acquire/release protocol around each one to get
        # there. Directly awaiting each concrete concurrent.futures.Future
        # that run_coroutine_threadsafe hands back (via asyncio.wrap_future)
        # has none of the plain-flag's gaps, because that Future object
        # exists synchronously the moment it's returned, regardless of
        # whether its Task has started.
        self._pending: list["concurrent.futures.Future"] = []

    async def _push(self, text: str) -> None:
        try:
            await self._update_status(text)
        except Exception:
            # A status edit must never disturb the download. This is most
            # commonly RetryAfter (Telegram flood control) or Forbidden
            # (the user blocked the bot) -- neither is a NetworkError or
            # TimedOut, so safe_edit_message doesn't swallow them. Warning
            # level, not debug: an operator needs to see flood control or
            # a block even though the download keeps going, and contained
            # here is the *only* place this failure is ever visible --
            # asyncio.futures._chain_future's _call_set_state copies this
            # coroutine's exception onto the concurrent.futures.Future
            # that nobody awaits or retrieves, which silently clears the
            # Task's own "exception was never retrieved" warning in the
            # process. Left uncaught, this failure would not be logged
            # anywhere at all, not even as that warning.
            logging.warning("Spotify progress edit failed", exc_info=True)

    def callback(self, done: int, total: int) -> None:
        """The ``progress_cb`` to pass into the download pipeline."""
        try:
            text = self._reporter.record_and_check(done, total)
            if text is not None:
                self._pending.append(
                    asyncio.run_coroutine_threadsafe(self._push(text), self._loop)
                )
        except Exception:
            # callback runs synchronously on download_track's worker
            # thread; an uncaught raise here propagates into its caller
            # and (in the real pipeline) triggers the .part-file cleanup
            # that discards a partially downloaded episode. A broken
            # progress report must never be able to do that.
            logging.warning("Spotify progress bridge failed", exc_info=True)

    async def drain(self) -> None:
        """Wait for every progress edit dispatched so far to finish.

        Every progress edit scheduled from the worker thread -- not just
        the most recent one -- can still be queued or genuinely mid-flight
        against Telegram's API when this is called. Two concurrent
        edit_message_text calls on the same message have no ordering
        guarantee between their underlying HTTP requests -- awaiting every
        one of these is what guarantees the status message the caller
        issues next is always the one the user sees last, regardless of
        how many progress edits are still outstanding.
        """
        if not self._pending:
            return
        try:
            # return_exceptions=True: without it, gather cancels every
            # other still-pending future the moment any one of them
            # raises, so this drain would stop short of actually waiting
            # for all of them -- exactly the ordering guarantee this
            # method exists to provide. It also stops a bare
            # CancelledError from a cancelled future from escaping past
            # the except below and replacing the caller's own exception.
            await asyncio.gather(
                *(asyncio.wrap_future(f, loop=self._loop) for f in self._pending),
                return_exceptions=True,
            )
        except Exception:
            # _push already contains its own failures, so this branch
            # should be unreachable in practice; kept only so a
            # wrap/chain/gather failure can't take the download down with
            # it either.
            logging.warning("Waiting for pending Spotify progress edits failed", exc_info=True)


async def download_spotify_resolved(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    resolved: dict,
    audio_format: str = "mp3",
    transcribe: bool = False,
    summary: bool = False,
    summary_type: int | None = None,
    trim_after: bool = False,
):
    """Download resolved Spotify audio, optionally transcribe and summarise."""

    query = update.callback_query
    chat_id = update.effective_chat.id
    title = resolved.get("title", "Spotify audio")
    artist = resolved.get("artist", "")
    is_music_track = bool(artist or resolved.get("spotify_url"))
    # Captured up front: a newer link may replace the session URL while this job runs.
    job_url = _get_session_value(context, chat_id, "current_url", user_urls) or ""

    async def update_status(text):
        await safe_edit_message(query, text)

    if transcribe:
        # Fail fast: check API keys before downloading anything.
        missing_key_text = missing_transcription_key_message(get_runtime_value, summary=summary)
        if missing_key_text:
            await update_status(missing_key_text)
            return False

    await update_status("Pobieranie utworu..." if is_music_track else "Pobieranie odcinka podcastu...")
    chat_download_path = os.path.join(DOWNLOAD_PATH, str(chat_id))
    os.makedirs(chat_download_path, exist_ok=True)

    source = resolved["source"]
    downloaded_file_path = None

    try:
        if source == "itunes":
            source_label = "iTunes"
        elif source == "youtube_music":
            source_label = "YT Music"
        else:
            source_label = "YouTube"
        await update_status(f"Pobieranie audio z {source_label}...")
        downloaded_file_path = await download_resolved_audio(
            resolved=resolved,
            audio_format=audio_format,
            output_dir=chat_download_path,
            executor=_executor,
        )
        if not downloaded_file_path:
            await update_status("Nie udało się pobrać pliku audio.")
            return False

        file_size_mb = os.path.getsize(downloaded_file_path) / (1024 * 1024)

        if transcribe:
            transcription_ok = await _handle_transcription(
                update, context, chat_id, title, downloaded_file_path,
                file_size_mb, chat_download_path, summary, summary_type,
                update_status,
                resolved=resolved, job_url=job_url,
            )
            downloaded_file_path = None
            if not transcription_ok:
                # Keep the error message _handle_transcription just showed.
                return False
        elif trim_after:
            offered = await offer_trim_after_download(
                context,
                chat_id=chat_id,
                requester_id=update.effective_user.id,
                file_path=downloaded_file_path,
                title=title,
                performer=artist or None,
                query=query,
            )
            if offered:
                record_download_for(
                    context,
                    chat_id,
                    title,
                    job_url,
                    "spotify_trim_source",
                    file_size_mb,
                )
            return offered
        else:
            await update_status(f"Wysyłanie pliku ({file_size_mb:.1f} MB)...")
            trim_source = await send_audio_with_trim(
                context,
                chat_id,
                downloaded_file_path,
                title=title,
                performer=artist or None,
                caption=title[:200],
            )
            record_download_for(
                context,
                chat_id,
                title,
                job_url,
                f"spotify_audio_{audio_format}",
                file_size_mb,
            )
            _clear_session_context_value_if(
                context, chat_id, "spotify_resolved", resolved, legacy_key="spotify_resolved"
            )
            if trim_source is not None:
                await update_status(f"Gotowe: {title}\n\n{TRIM_AVAILABLE_HINT}")
                return True

        await update_status(f"Gotowe: {title}")
        return True
    except AudioDeliveryError as exc:
        await update_status(str(exc))
        return False
    except Exception as exc:
        logging.error("Error downloading Spotify audio: %s", exc)
        await update_status(f"Błąd pobierania: {str(exc)[:200]}")
        return False
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
    # Captured up front: a newer link may replace the session URL while this job runs.
    job_url = _get_session_value(context, chat_id, "current_url", user_urls) or ""

    async def update_status(text):
        await safe_edit_message(query, text)

    chat_download_path = os.path.join(DOWNLOAD_PATH, str(chat_id))
    os.makedirs(chat_download_path, exist_ok=True)

    label = "audio" if height is None else "wideo"
    await update_status(f"Pobieranie {label} ze Spotify...")

    bridge = _SpotifyProgressBridge(
        label, update_status=update_status, min_interval=_PROGRESS_EDIT_MIN_INTERVAL_SEC
    )

    async def report_phase(phase):
        text = _PHASE_MESSAGES.get(phase)
        if text is None:
            return
        # Task 11's ordering guarantee covers every status write, not only
        # the one issued after the download returns: drain first so a
        # progress edit still in flight cannot land on top of this one.
        await bridge.drain()
        await update_status(text)

    downloaded_path = None
    trim_source = None
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

        try:
            downloaded_path = await download_episode_media(
                episode=episode,
                height=height,
                output_dir=chat_download_path,
                executor=_executor,
                progress_cb=bridge.callback,
                phase_cb=report_phase,
            )
        finally:
            # Wait for every progress edit dispatched so far, before any
            # further status edit is issued -- whether control proceeds to
            # the success path below or to one of the except blocks
            # further down; this finally runs before control reaches
            # those too. See _SpotifyProgressBridge.drain for why.
            await bridge.drain()

        file_size_mb = os.path.getsize(downloaded_path) / (1024 * 1024)
        await update_status(f"Pobieranie zakończone ({file_size_mb:.1f} MB).\n\nWysyłanie...")

        if height is None:
            trim_source = await send_audio_with_trim(
                context, chat_id, downloaded_path, title=title, caption=title[:200]
            )
        elif file_size_mb > TELEGRAM_UPLOAD_LIMIT_MB:
            reason = _mtproto_unavailability_reason()
            if reason is not None:
                await update_status(
                    f"Plik za duży dla Bot API ({file_size_mb:.0f} MB, "
                    f"limit: {TELEGRAM_UPLOAD_LIMIT_MB} MB).\n{reason}"
                )
                return
            ok = await send_video_mtproto(chat_id, downloaded_path, caption=title[:200])
            if not ok:
                await update_status("Wysyłanie pliku przez MTProto nie powiodło się.")
                return
        else:
            with open(downloaded_path, "rb") as file_obj:
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
            job_url,
            f"spotify_native_{'audio' if height is None else str(height) + 'p'}",
            file_size_mb,
        )
        _clear_session_context_value_if(
            context, chat_id, "spotify_video", session_data, legacy_key="spotify_video"
        )
        if trim_source is not None:
            await update_status(f"Gotowe: {title}\n\n{TRIM_AVAILABLE_HINT}")
        else:
            await update_status(f"Gotowe: {title}")

    except SpotifyVideoCancelled:
        await update_status("Pobieranie anulowane.")
    except AudioDeliveryError as exc:
        await update_status(str(exc))
    except SpotifyVideoError as exc:
        # A failed segment fetch raises a descriptive English sentence, not a
        # mapped reason code, and it is the failure this pipeline hits most
        # often. Without this log the operator had nothing at all to go on;
        # the message itself has its signed CDN query strings redacted where
        # it is composed (bot/spotify_video.py::_redact_url_query).
        logging.error("Spotify video download failed: %s", exc)
        await update_status(get_download_error_message(str(exc)))
    except Exception as exc:
        logging.error("Error downloading Spotify video: %s", exc)
        await update_status(f"Błąd pobierania: {str(exc)[:200]}")
    finally:
        if downloaded_path and os.path.exists(downloaded_path):
            try:
                os.remove(downloaded_path)
            except OSError:
                pass


async def transcribe_spotify_video(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    session_data: dict,
    *,
    summary: bool = False,
    summary_type: int | None = None,
):
    """Transcribe a Spotify episode resolved to a native video manifest.

    Prefers Spotify's own WebVTT subtitles when the episode ships them --
    that skips downloading any audio at all. Falls back to downloading the
    native AAC audio track and running it through Groq, the same pipeline
    the legacy (``spotify_resolved``) audio transcription path uses, when
    the episode has no subtitles or Spotify fails to serve the one it
    advertised.
    """

    query = update.callback_query
    chat_id = update.effective_chat.id
    title = session_data.get("title", "Spotify episode")
    # Captured up front: a newer link may replace the session URL while this job runs.
    job_url = _get_session_value(context, chat_id, "current_url", user_urls) or ""

    async def update_status(text):
        await safe_edit_message(query, text)

    chat_download_path = os.path.join(DOWNLOAD_PATH, str(chat_id))
    os.makedirs(chat_download_path, exist_ok=True)

    downloaded_file_path = None
    transcript_path = None

    if summary and summary_type and not get_runtime_value("CLAUDE_API_KEY", ""):
        # Fail fast, before any subtitle or audio download. The Groq key is
        # only needed by the audio fallback below, so it is checked there.
        await update_status(MISSING_CLAUDE_KEY_TEXT)
        return

    try:
        # Session state can carry a manifest that no longer parses -- a
        # stale one surviving from an earlier episode, or genuine Spotify
        # API drift -- so building the episode happens inside this try,
        # same as the identical construction in download_spotify_video,
        # the function immediately above: a manifest-shape failure here must surface
        # as the same "api_changed" Polish message every other manifest
        # failure produces, not an unhandled exception that leaves the
        # status message frozen forever.
        #
        # profiles is left empty here (unlike download_spotify_video):
        # neither branch below reads it -- the subtitle path only touches
        # subtitle_languages/manifest, and download_episode_media(height=
        # None) resolves the audio profile from the manifest itself via
        # find_audio_profile_id. Calling list_profiles(manifest) here
        # would just be a second, redundant manifest-shape check; skipping
        # it lets any drift surface through download_episode_media's own
        # already-tested "api_changed" normalization instead.
        manifest = session_data["manifest"]
        episode = VideoEpisode(
            episode_id=session_data["episode_id"],
            title=title,
            show_name=session_data.get("show_name", ""),
            duration_ms=int(session_data.get("duration_ms") or 0),
            manifest=manifest,
            profiles=[],
            subtitle_languages=session_data.get("subtitle_languages", []),
        )
        # Mirrors download_episode_media's own base_name formula exactly
        # (bot/services/spotify_video_service.py) -- title can legitimately
        # be an empty string (Spotify's embed entity can carry one), and if
        # this diverged from that formula, cleanup_transcription_artifacts's
        # transcript_prefix would stop matching the real per-part chunk
        # files on the audio+Groq fallback path, leaking them until the
        # 24h sweep.
        sanitized_title = sanitize_filename(title or "spotify_episode")

        if episode.subtitle_languages:
            await update_status("Pobieranie napisów ze Spotify...")
            transcript_path = await asyncio.get_event_loop().run_in_executor(
                _executor,
                lambda: transcript_from_subtitles(
                    episode=episode,
                    output_dir=chat_download_path,
                    sanitized_title=sanitized_title,
                ),
            )

        if transcript_path is None:
            # No subtitles on this episode, or Spotify failed to serve the
            # one it advertised -- fall back to downloading the native AAC
            # audio track and transcribing it with Groq.
            if not get_runtime_value("GROQ_API_KEY", ""):
                await update_status(MISSING_GROQ_KEY_TEXT)
                return

            await update_status("Pobieranie audio ze Spotify...")
            # Reuses the same reporter/drain machinery download_spotify_video
            # uses -- see _SpotifyProgressBridge -- so this fallback reports
            # live progress instead of leaving the status message frozen on
            # "Pobieranie audio..." for the whole download.
            bridge = _SpotifyProgressBridge(
                "audio", update_status=update_status, min_interval=_PROGRESS_EDIT_MIN_INTERVAL_SEC
            )
            try:
                downloaded_file_path = await download_episode_media(
                    episode=episode,
                    height=None,
                    output_dir=chat_download_path,
                    executor=_executor,
                    progress_cb=bridge.callback,
                )
            finally:
                # Wait for every progress edit dispatched so far, before
                # any further status edit is issued -- see
                # _SpotifyProgressBridge.drain for why.
                await bridge.drain()
            file_size_mb = os.path.getsize(downloaded_file_path) / (1024 * 1024)
            await update_status(
                f"Pobieranie zakończone ({file_size_mb:.1f} MB).\n\n"
                "Rozpoczynanie transkrypcji audio...\nTo może potrwać kilka minut."
            )
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

        summary_failed = False
        if summary and summary_type:
            summary_failed = await _maybe_generate_summary(
                context, chat_id, title, transcript_text, sanitized_title,
                chat_download_path, update_status, summary_type=summary_type,
            )

        if not summary_failed:
            # Otherwise keep the "summary failed, sending transcript" status visible.
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

        record_size_mb = (
            os.path.getsize(downloaded_file_path) / (1024 * 1024)
            if downloaded_file_path
            else os.path.getsize(transcript_path) / (1024 * 1024)
        )
        record_download_for(
            context,
            chat_id,
            title,
            job_url,
            "spotify_transcribe",
            record_size_mb,
        )
        _clear_session_context_value_if(
            context, chat_id, "spotify_video", session_data, legacy_key="spotify_video"
        )

        if downloaded_file_path:
            cleanup_transcription_artifacts(
                source_media_path=downloaded_file_path,
                output_dir=chat_download_path,
                transcript_prefix=sanitized_title,
            )
            downloaded_file_path = None

        await offer_custom_transcript_prompt(
            context,
            chat_id=chat_id,
            requester_id=update.effective_user.id,
            transcript_path=transcript_path,
            title=title,
        )
        await update_status(f"Gotowe: {title}")

    except SpotifyVideoCancelled:
        await update_status("Pobieranie anulowane.")
    except SpotifyVideoError as exc:
        # Same reasoning as download_spotify_video's branch above: the Groq
        # fallback downloads the native audio track through the very same
        # segment fetcher, so it reaches this branch the same way.
        logging.error("Spotify video transcription failed: %s", exc)
        await update_status(get_download_error_message(str(exc)))
    except Exception as exc:
        logging.error("Error transcribing Spotify video episode: %s", exc)
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
    resolved=None, job_url="",
):
    """Transcribe and optionally summarise a downloaded Spotify episode.

    Returns False when transcription failed (the error is already shown in the
    status message), True once the transcript has been delivered.
    """

    await update_status(
        f"Pobieranie zakończone ({file_size_mb:.1f} MB).\n\nRozpoczynanie transkrypcji audio...\nTo może potrwać kilka minut."
    )
    transcript_path = await run_transcription_with_progress(
        source_path=downloaded_file_path,
        output_dir=chat_download_path,
        executor=_executor,
        status_callback=update_status,
    )
    if not transcript_path or not os.path.exists(transcript_path):
        await update_status("Wystąpił błąd podczas transkrypcji.")
        return False

    transcript_result = load_transcript_result(transcript_path)
    transcript_text = transcript_result.display_text
    sanitized_title = os.path.splitext(os.path.basename(downloaded_file_path))[0]

    summary_failed = False
    if summary and summary_type:
        summary_failed = await _maybe_generate_summary(
            context, chat_id, title, transcript_text, sanitized_title,
            chat_download_path, update_status,
            summary_type=summary_type,
        )

    if not summary_failed:
        # Otherwise keep the "summary failed, sending transcript" status visible.
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
        job_url,
        "spotify_transcribe",
        file_size_mb,
    )
    _clear_session_context_value_if(
        context, chat_id, "spotify_resolved", resolved, legacy_key="spotify_resolved"
    )
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
    return True


async def _maybe_generate_summary(
    context, chat_id, title, transcript_text, sanitized_title,
    chat_download_path, update_status, *, summary_type,
):
    """Generate an AI summary if keys are available and text is short enough.

    Returns True only when the summary was attempted and failed.
    """

    if not get_runtime_value("CLAUDE_API_KEY", ""):
        await update_status(
            "Transkrypcja zakończona.\n\nPodsumowanie niedostępne — brak klucza CLAUDE_API_KEY.\nWysyłam samą transkrypcję."
        )
        return False
    if transcript_too_long_for_summary(transcript_text):
        await update_status(
            "Transkrypcja zakończona, ale tekst jest zbyt długi na podsumowanie AI.\n\nWysyłam samą transkrypcję."
        )
        return False

    await update_status("Transkrypcja zakończona.\n\nGeneruję podsumowanie AI...\nTo może potrwać około minuty.")
    try:
        summary_result = await generate_summary_artifact(
            transcript_text=transcript_text,
            summary_type=summary_type,
            title=title,
            sanitized_title=sanitized_title,
            output_dir=chat_download_path,
            executor=_executor,
        )
    except Exception as exc:
        logging.error("Summary generation failed: %s", exc)
        summary_result = None
    if not summary_result:
        # The caller still delivers the transcript file right after this.
        await update_status(SUMMARY_FAILED_KEEP_TRANSCRIPT_TEXT)
        return True
    await send_long_message(
        context.bot,
        chat_id,
        summary_result.summary_text,
        header=f"*Podsumowanie: {escape_md(title)}*\n\n",
    )
    return False
