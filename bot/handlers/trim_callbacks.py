"""✂️ audio trimming flow: ask for ranges, cut fragments, send them.

Callbacks handled here: ``trim_src_<token>`` (source in the trim store),
``trim_upload`` (audio sent to the bot) and ``trim_cancel``. ``trim_dl`` is
routed in bot/telegram_callbacks.py because it calls the download flows,
which import this module.

See also: bot/services/trim_store.py, bot/services/audio_trim_service.py,
bot/handlers/audio_delivery.py, bot/handlers/time_range.py.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.handlers.audio_delivery import AudioDeliveryError, max_sendable_audio_mb, send_audio_file
from bot.handlers.common_ui import escape_md, safe_edit_message
from bot.handlers.time_range import TimeRangeError, format_timestamp, parse_time_ranges, resolve_ranges
from bot.jobs import JobCancellation, JobDescriptor, job_registry
from bot.runtime import record_download_for
from bot.security_limits import TRIM_PENDING_INPUT_TIMEOUT_MIN
from bot.security_policy import extract_url_from_text
from bot.security_throttling import check_rate_limit
from bot.services.audio_trim_service import (
    AudioTrimError,
    cut_fragment,
    fragment_filename,
    fragment_label,
    probe_duration,
)
from bot.services.trim_store import TrimSource, expires_at, load_source, retain_source
from bot.session_context import (
    clear_session_context_value,
    get_session_context_value,
    set_session_context_value,
)
from bot.session_store import PendingTrimInput

_PENDING_FIELD = "pending_trim"
_TRANSCRIPT_PENDING_FIELD = "pending_transcript_prompt"
_MONTHS = ("sty", "lut", "mar", "kwi", "maj", "cze", "lip", "sie", "wrz", "paź", "lis", "gru")

EXPIRED_TEXT = (
    "Plik wygasł (minęły 24 h) albo został usunięty. Wyślij to MP3 do bota, żeby je przyciąć."
)
AUTH_REQUIRED_TEXT = "Wymagane uwierzytelnienie — wyślij kod PIN."
BUSY_TEXT = "Poczekaj, aż skończę poprzednie cięcie, albo przerwij je komendą /stop."
START_FAILED_TEXT = "Nie udało się uruchomić przycinania. Spróbuj ponownie."
RATE_LIMIT_TEXT = "Przekroczono limit requestów. Spróbuj ponownie za chwilę."
NO_ROOM_TEXT = (
    "Na serwerze brakuje miejsca, żeby przechować plik do cięcia. "
    "Pobierz całość przyciskiem Audio (MP3)."
)
TRIM_AFTER_DOWNLOAD_INTRO = "Pobrano całość — plik nie zostanie wysłany, wytnij z niego fragmenty.\n\n"
CANCEL_MARKUP = InlineKeyboardMarkup([[InlineKeyboardButton("Anuluj", callback_data="trim_cancel")]])


def _now() -> datetime:
    return datetime.now(UTC)


def _is_authorized(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    # Imported lazily, as in bot/handlers/inbound_media.py, to keep handler
    # modules free of import cycles.
    from bot.handlers.command_access import _is_authorized as shared_is_authorized

    return shared_is_authorized(context, user_id)


def _get_pending(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> PendingTrimInput | None:
    pending = get_session_context_value(context, chat_id, _PENDING_FIELD, legacy_key=_PENDING_FIELD)
    return pending if isinstance(pending, PendingTrimInput) else None


def _clear_pending(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    clear_session_context_value(context, chat_id, _PENDING_FIELD, legacy_key=_PENDING_FIELD)


def _set_pending(context: ContextTypes.DEFAULT_TYPE, chat_id: int, pending: PendingTrimInput) -> None:
    # Only one text input may be pending per chat: a trim prompt supersedes a
    # custom transcript instruction (transcript_prompt_handlers does the reverse).
    clear_session_context_value(
        context, chat_id, _TRANSCRIPT_PENDING_FIELD, legacy_key=_TRANSCRIPT_PENDING_FIELD
    )
    set_session_context_value(context, chat_id, _PENDING_FIELD, pending, legacy_key=_PENDING_FIELD)


def fragments_phrase(count: int) -> str:
    """Polish count phrase: 1 fragment, 2 fragmenty, 5 fragmentów."""

    if count == 1:
        return "1 fragment"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return f"{count} fragmenty"
    return f"{count} fragmentów"


def _format_expiry(source: TrimSource) -> str:
    local = expires_at(source).astimezone()
    return f"{local.day} {_MONTHS[local.month - 1]}, {local:%H:%M}"


def build_trim_prompt_text(source: TrimSource, intro: str = "") -> str:
    return (
        f"{intro}✂️ Przycinanie: *{escape_md(source.title)}*\n"
        f"Długość: {format_timestamp(source.duration_sec)}\n\n"
        "Wpisz zakres (albo kilka po przecinku):\n"
        "`1:30-4:45` — jeden fragment\n"
        "`2:15-` — od 2:15 do końca\n"
        "`-5:00` — od początku do 5:00\n"
        "`1:00-2:00, 5:30-7:00` — dwa pliki\n\n"
        f"Plik jest dostępny do {_format_expiry(source)}."
    )


async def start_trim_prompt(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    chat_id: int,
    requester_id: int,
    source: TrimSource,
    query=None,
    intro: str = "",
) -> None:
    """Ask for ranges; edits the callback message when ``query`` is given."""

    _set_pending(
        context,
        chat_id,
        PendingTrimInput(token=source.token, requester_id=requester_id, created_at=_now()),
    )
    text = build_trim_prompt_text(source, intro)
    if query is not None:
        await safe_edit_message(query, text, reply_markup=CANCEL_MARKUP, parse_mode="Markdown")
        return
    await context.bot.send_message(
        chat_id=chat_id, text=text, reply_markup=CANCEL_MARKUP, parse_mode="Markdown"
    )


async def offer_trim_after_download(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    chat_id: int,
    requester_id: int,
    file_path: str,
    title: str,
    performer: str | None,
    query,
) -> bool:
    """"✂️ Pobierz i przytnij": keep a fresh download in the store and ask for ranges."""

    try:
        duration = await probe_duration(Path(file_path))
    except AudioTrimError as exc:
        logging.error("Cannot probe downloaded audio for trimming: %s", exc)
        await safe_edit_message(
            query,
            "Nie udało się odczytać długości pobranego pliku. Pobierz całość przyciskiem Audio (MP3).",
        )
        return False
    source = retain_source(
        chat_id, file_path, title=title, performer=performer, duration_sec=round(duration)
    )
    if source is None:
        await safe_edit_message(query, NO_ROOM_TEXT)
        return False
    await start_trim_prompt(
        context,
        chat_id=chat_id,
        requester_id=requester_id,
        source=source,
        query=query,
        intro=TRIM_AFTER_DOWNLOAD_INTRO,
    )
    return True


async def ensure_trim_authorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Trim sources outlive restarts and /logout, so buttons re-check the PIN state."""

    if _is_authorized(context, update.effective_user.id):
        return True
    await context.bot.send_message(chat_id=update.effective_chat.id, text=AUTH_REQUIRED_TEXT)
    return False


async def _start_upload_trim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat_id = update.effective_chat.id
    path = get_session_context_value(context, chat_id, "audio_file_path", legacy_key="audio_file_path")
    title = get_session_context_value(
        context, chat_id, "audio_file_title", legacy_key="audio_file_title", default="Plik audio"
    )
    if not path or not Path(path).is_file():
        await safe_edit_message(query, "Sesja wygasła — wyślij plik ponownie.")
        return
    try:
        duration = await probe_duration(Path(path))
    except AudioTrimError as exc:
        logging.error("Cannot probe uploaded audio for trimming: %s", exc)
        await safe_edit_message(query, "Nie udało się odczytać długości pliku audio. Wyślij go ponownie.")
        return
    # Hardlink, not move: transcribing the same upload keeps using the original path.
    source = retain_source(
        chat_id, path, title=title, performer=None, duration_sec=round(duration), link=True
    )
    if source is None:
        await safe_edit_message(query, "Na serwerze brakuje miejsca, żeby przechować plik do cięcia.")
        return
    # A new message keeps the upload menu (transcription buttons) usable.
    await start_trim_prompt(
        context, chat_id=chat_id, requester_id=update.effective_user.id, source=source
    )


async def handle_trim_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    """Route trim_src_*, trim_upload and trim_cancel callbacks."""

    if not await ensure_trim_authorized(update, context):
        return
    query = update.callback_query
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if data == "trim_cancel":
        pending = _get_pending(context, chat_id)
        if pending is not None and pending.requester_id == user_id:
            _clear_pending(context, chat_id)
            await safe_edit_message(query, "Anulowano przycinanie.")
            return
        await safe_edit_message(query, "Ta prośba nie jest już aktywna.")
        return

    if data.startswith("trim_src_"):
        source = load_source(chat_id, data.removeprefix("trim_src_"))
        if source is None:
            # A new message: the button may sit under an audio whose text cannot be edited.
            await context.bot.send_message(chat_id=chat_id, text=EXPIRED_TEXT)
            return
        await start_trim_prompt(context, chat_id=chat_id, requester_id=user_id, source=source)
        return

    if data == "trim_upload":
        await _start_upload_trim(update, context)
        return

    await safe_edit_message(query, "Nieobsługiwana akcja przycinania.")


async def handle_pending_trim_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Consume typed ranges before normal URL/text handling."""

    chat_id = update.effective_chat.id
    requester_id = update.effective_user.id
    pending = _get_pending(context, chat_id)
    if pending is None or pending.requester_id != requester_id:
        return False

    text = (update.message.text or "").strip()
    if _now() - pending.created_at > timedelta(minutes=TRIM_PENDING_INPUT_TIMEOUT_MIN):
        _clear_pending(context, chat_id)
        return False
    if extract_url_from_text(text):
        # A new link means the user moved on; let the normal flow handle it.
        _clear_pending(context, chat_id)
        return False

    source = load_source(chat_id, pending.token)
    if source is None:
        _clear_pending(context, chat_id)
        await update.message.reply_text(EXPIRED_TEXT)
        return True

    try:
        ranges = resolve_ranges(parse_time_ranges(text), source.duration_sec)
    except TimeRangeError as exc:
        # No parse_mode: the message echoes user input.
        await update.message.reply_text(str(exc))
        return True

    if not check_rate_limit(requester_id):
        await update.message.reply_text(RATE_LIMIT_TEXT)
        return True
    if any(job.kind == "trim" for job in job_registry.list_for_chat(chat_id)):
        await update.message.reply_text(BUSY_TEXT)
        return True

    _clear_pending(context, chat_id)
    status_message = await update.message.reply_text(
        f"✂️ Przygotowuję {fragments_phrase(len(ranges))}..."
    )
    # The application processes updates one at a time (no concurrent_updates in
    # main.py), so awaiting the job here would hold /stop and the "Zatrzymaj"
    # buttons back until every fragment is sent. The job therefore runs as a
    # PTB task. It is registered first, so a range typed before the task
    # starts already gets BUSY_TEXT.
    cancellation = _register_trim_job(chat_id, source)
    job = run_trim_job(
        context,
        chat_id=chat_id,
        requester_id=requester_id,
        source=source,
        ranges=ranges,
        status_message=status_message,
        cancellation=cancellation,
    )
    try:
        # PTB routes exceptions raised by the task to the application's error handlers.
        context.application.create_task(job, update=update)
    except Exception as exc:
        logging.error("Could not schedule trim job: token=%s: %s", source.token, exc)
        job.close()  # never started; closing avoids a "never awaited" warning
        job_registry.unregister(cancellation.job_id)
        await _edit_status(status_message, START_FAILED_TEXT)
    return True


async def _edit_status(message, text: str, reply_markup=None) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except Exception as exc:
        logging.warning("Trim status update failed: %s", exc)


def _sent_before(sent: int) -> str:
    return f"\n\nWysłano wcześniej: {fragments_phrase(sent)}." if sent else ""


def _register_trim_job(chat_id: int, source: TrimSource) -> JobCancellation:
    """Make the job visible to /stop and to the BUSY_TEXT check."""

    descriptor = JobDescriptor(
        job_id="",
        chat_id=chat_id,
        kind="trim",
        label=f"Przycinanie: {source.title}"[:80],
        started_at=datetime.now(),
    )
    return job_registry.register(chat_id, descriptor)


async def run_trim_job(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    chat_id: int,
    requester_id: int,
    source: TrimSource,
    ranges: list,
    status_message,
    cancellation: JobCancellation | None = None,
) -> None:
    """Cut and send each fragment; the source stays in the store for more cuts.

    ``cancellation`` is the handle of a job already registered by the caller
    (handle_pending_trim_input); without it the job registers itself. Either
    way the job is unregistered when this coroutine ends.
    """

    if cancellation is None:
        cancellation = _register_trim_job(chat_id, source)
    again_markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✂️ Tnij dalej", callback_data=f"trim_src_{source.token}")]]
    )
    total = len(ranges)
    sent = 0

    try:
        limit_mb = max_sendable_audio_mb()
        for index, fragment in enumerate(ranges, start=1):
            if cancellation.event.is_set():
                break
            label = fragment_label(fragment)
            fragment_title = f"{source.title} [{label}]"
            dest = source.workspace / fragment_filename(source.title, fragment, source.path.suffix)
            try:
                await _edit_status(status_message, f"✂️ Tnę fragment {index}/{total} ({label})...")
                cut_started = time.monotonic()
                try:
                    await cut_fragment(
                        source.path, fragment, dest, title_tag=fragment_title, cancellation=cancellation
                    )
                    logging.info(
                        "Trim cut: token=%s range=%s took %.1fs",
                        source.token, label, time.monotonic() - cut_started,
                    )
                except AudioTrimError as exc:
                    if cancellation.event.is_set():
                        break
                    logging.error("Trim cut failed: token=%s range=%s: %s", source.token, label, exc)
                    await _edit_status(
                        status_message,
                        f"Nie udało się wyciąć fragmentu {label}. "
                        f"Pozostałe fragmenty nie zostały wysłane.{_sent_before(sent)}",
                        again_markup,
                    )
                    return

                size_mb = dest.stat().st_size / (1024 * 1024)
                if size_mb > limit_mb:
                    await _edit_status(
                        status_message,
                        f"Fragment {label} ma {size_mb:.0f} MB — za dużo do wysłania. "
                        f"Wybierz krótszy zakres.{_sent_before(sent)}",
                        again_markup,
                    )
                    return

                await _edit_status(status_message, f"✂️ Wysyłam fragment {index}/{total} ({label})...")
                try:
                    await send_audio_file(
                        context,
                        chat_id,
                        dest,
                        title=fragment_title,
                        performer=source.performer,
                        caption=fragment_title[:200],
                        cancellation=cancellation,
                    )
                except Exception as exc:
                    if cancellation.event.is_set():
                        break
                    logging.error("Trim send failed: token=%s range=%s: %s", source.token, label, exc)
                    message = (
                        str(exc)
                        if isinstance(exc, AudioDeliveryError)
                        else f"Nie udało się wysłać fragmentu {label}. Spróbuj ponownie."
                    )
                    await _edit_status(status_message, f"{message}{_sent_before(sent)}", again_markup)
                    return
                sent += 1
            finally:
                dest.unlink(missing_ok=True)
    finally:
        job_registry.unregister(cancellation.job_id)

    if cancellation.event.is_set():
        await _edit_status(status_message, f"⏹ Przerwano po {sent} z {total} fragmentów.", again_markup)
        return
    record_download_for(context, requester_id, source.title, "", "audio_trim")
    await _edit_status(status_message, f"Gotowe: {fragments_phrase(sent)}.", again_markup)
