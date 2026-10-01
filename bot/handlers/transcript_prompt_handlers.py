"""Follow-up custom-prompt actions for completed transcripts."""

from __future__ import annotations

import logging
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.downloader_validation import sanitize_filename
from bot.handlers.common_ui import escape_md, safe_edit_message, send_long_message
from bot.runtime import get_config_value_for, record_download_for
from bot.security_throttling import RATE_LIMIT_MESSAGE, check_rate_limit
from bot.services.transcription_service import (
    generate_custom_analysis_artifact,
    load_transcript_result,
)
from bot.session_context import (
    clear_session_context_value,
    get_session_context_value,
    set_session_context_value,
)
from bot.session_store import PendingTranscriptPrompt, TranscriptContext
from bot.transcription_limits import (
    CUSTOM_PROMPT_MAX_CHARS,
    is_custom_analysis_input_too_long,
)

TRANSCRIPT_CONTEXT_TTL = timedelta(hours=24)
MAX_TRANSCRIPT_CONTEXTS = 3

_CONTEXTS_FIELD = "transcript_contexts"
_CONTEXTS_LEGACY_KEY = "transcript_contexts"
_PENDING_FIELD = "pending_transcript_prompt"
_PENDING_LEGACY_KEY = "pending_transcript_prompt"
_executor = ThreadPoolExecutor(max_workers=2)


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _context_is_current(item: TranscriptContext, now: datetime) -> bool:
    created_at = item.created_at
    if not isinstance(created_at, datetime):
        return False
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return now - created_at <= TRANSCRIPT_CONTEXT_TTL and os.path.exists(item.transcript_path)


def _get_contexts(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> dict[str, TranscriptContext]:
    stored = get_session_context_value(
        context,
        chat_id,
        _CONTEXTS_FIELD,
        legacy_key=_CONTEXTS_LEGACY_KEY,
        default={},
    )
    return dict(stored) if isinstance(stored, dict) else {}


def _store_contexts(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    contexts: dict[str, TranscriptContext],
) -> None:
    if contexts:
        set_session_context_value(
            context,
            chat_id,
            _CONTEXTS_FIELD,
            contexts,
            legacy_key=_CONTEXTS_LEGACY_KEY,
        )
        return
    clear_session_context_value(
        context,
        chat_id,
        _CONTEXTS_FIELD,
        legacy_key=_CONTEXTS_LEGACY_KEY,
    )


def _prune_contexts(
    contexts: dict[str, TranscriptContext],
    *,
    now: datetime | None = None,
) -> dict[str, TranscriptContext]:
    current_time = now or _now_utc()
    current = {
        token: item
        for token, item in contexts.items()
        if isinstance(item, TranscriptContext) and _context_is_current(item, current_time)
    }

    def _created_timestamp(pair: tuple[str, TranscriptContext]) -> float:
        created_at = pair[1].created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        return created_at.timestamp()

    ordered = sorted(current.items(), key=_created_timestamp, reverse=True)
    return dict(ordered[:MAX_TRANSCRIPT_CONTEXTS])


def register_transcript_context(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    chat_id: int,
    requester_id: int,
    transcript_path: str,
    title: str,
) -> str | None:
    """Register one completed transcript and return its callback-safe token."""

    if not transcript_path or not os.path.exists(transcript_path):
        return None

    contexts = _prune_contexts(_get_contexts(context, chat_id))
    token = secrets.token_urlsafe(6)
    while token in contexts:
        token = secrets.token_urlsafe(6)

    contexts[token] = TranscriptContext(
        transcript_path=str(transcript_path),
        title=title,
        requester_id=requester_id,
        created_at=_now_utc(),
    )
    contexts = _prune_contexts(contexts)
    _store_contexts(context, chat_id, contexts)
    return token


def _prompt_keyboard(token: str, *, repeat: bool = False) -> InlineKeyboardMarkup:
    label = "✍️ Zadaj kolejne polecenie" if repeat else "✍️ Własne polecenie"
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=f"tr_prompt_{token}")]]
    )


async def offer_custom_transcript_prompt(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    chat_id: int,
    requester_id: int,
    transcript_path: str,
    title: str,
) -> str | None:
    """Register a transcript and send the custom-instruction action button."""

    token = register_transcript_context(
        context,
        chat_id=chat_id,
        requester_id=requester_id,
        transcript_path=transcript_path,
        title=title,
    )
    if token is None:
        return None

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Możesz teraz wykonać własne polecenie na tej transkrypcji.",
            reply_markup=_prompt_keyboard(token),
        )
    except Exception as exc:
        logging.warning("Could not send custom transcript prompt button: %s", exc)
    return token


def _find_context(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    token: str,
) -> TranscriptContext | None:
    contexts = _prune_contexts(_get_contexts(context, chat_id))
    _store_contexts(context, chat_id, contexts)
    return contexts.get(token)


def _get_pending_prompt(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
) -> PendingTranscriptPrompt | None:
    pending = get_session_context_value(
        context,
        chat_id,
        _PENDING_FIELD,
        legacy_key=_PENDING_LEGACY_KEY,
    )
    return pending if isinstance(pending, PendingTranscriptPrompt) else None


def _clear_pending_prompt(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    clear_session_context_value(
        context,
        chat_id,
        _PENDING_FIELD,
        legacy_key=_PENDING_LEGACY_KEY,
    )


async def handle_transcript_prompt_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    data: str,
) -> None:
    """Start or cancel collection of a custom transcript instruction."""

    query = update.callback_query
    chat_id = update.effective_chat.id
    requester_id = update.effective_user.id

    if data.startswith("tr_prompt_cancel_"):
        token = data.removeprefix("tr_prompt_cancel_")
        pending = _get_pending_prompt(context, chat_id)
        if (
            pending is not None
            and pending.transcript_token == token
            and pending.requester_id == requester_id
        ):
            _clear_pending_prompt(context, chat_id)
            await safe_edit_message(
                query,
                "Anulowano własne polecenie.",
                reply_markup=_prompt_keyboard(token),
            )
            return
        await safe_edit_message(query, "Ta prośba nie jest już aktywna.")
        return

    token = data.removeprefix("tr_prompt_")
    transcript_context = _find_context(context, chat_id, token)
    if transcript_context is None:
        await safe_edit_message(
            query,
            "Kontekst transkrypcji wygasł albo plik został już usunięty.",
        )
        return

    if transcript_context.requester_id != requester_id:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Tylko osoba, która utworzyła tę transkrypcję, może użyć tego przycisku.",
        )
        return

    # Only one text input may be pending per chat; see bot/handlers/trim_callbacks.py.
    clear_session_context_value(context, chat_id, "pending_trim", legacy_key="pending_trim")
    set_session_context_value(
        context,
        chat_id,
        _PENDING_FIELD,
        PendingTranscriptPrompt(
            transcript_token=token,
            requester_id=requester_id,
        ),
        legacy_key=_PENDING_LEGACY_KEY,
    )
    cancel_markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Anuluj", callback_data=f"tr_prompt_cancel_{token}")]]
    )
    await safe_edit_message(
        query,
        "Napisz, co mam zrobić z transkrypcją.\n\n"
        "Przykład: „Wypisz najważniejsze argumenty i oceń ich mocne oraz słabe strony”.\n\n"
        f"Maksymalna długość: {CUSTOM_PROMPT_MAX_CHARS} znaków.",
        reply_markup=cancel_markup,
    )


async def handle_pending_transcript_prompt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> bool:
    """Consume a pending custom instruction before normal URL/text handling."""

    chat_id = update.effective_chat.id
    requester_id = update.effective_user.id
    pending = _get_pending_prompt(context, chat_id)
    if pending is None or pending.requester_id != requester_id:
        return False

    prompt = (update.message.text or "").strip()
    if not prompt:
        await update.message.reply_text("Polecenie nie może być puste. Spróbuj ponownie.")
        return True
    if len(prompt) > CUSTOM_PROMPT_MAX_CHARS:
        await update.message.reply_text(
            f"Polecenie jest za długie ({len(prompt)} znaków). "
            f"Maksymalna długość to {CUSTOM_PROMPT_MAX_CHARS} znaków."
        )
        return True
    if not check_rate_limit(requester_id):
        await update.message.reply_text(RATE_LIMIT_MESSAGE)
        return True

    _clear_pending_prompt(context, chat_id)
    transcript_context = _find_context(context, chat_id, pending.transcript_token)
    if transcript_context is None:
        await update.message.reply_text(
            "Kontekst transkrypcji wygasł albo plik został już usunięty."
        )
        return True

    claude_api_key = get_config_value_for(context, "CLAUDE_API_KEY", "")
    if not claude_api_key:
        await update.message.reply_text(
            "Funkcja niedostępna — brak klucza CLAUDE_API_KEY. "
            "Skontaktuj się z administratorem."
        )
        return True

    try:
        transcript_result = load_transcript_result(transcript_context.transcript_path)
    except OSError as exc:
        logging.error("Cannot load transcript for custom analysis: %s", exc)
        await update.message.reply_text(
            "Nie udało się odczytać transkrypcji. Plik mógł zostać już usunięty."
        )
        return True

    transcript_text = transcript_result.display_text
    if is_custom_analysis_input_too_long(transcript_text, prompt):
        await update.message.reply_text(
            "Transkrypcja wraz z poleceniem jest zbyt długa dla analizy AI."
        )
        return True

    status_message = await update.message.reply_text(
        "Analizuję transkrypcję według Twojego polecenia...\n"
        "To może potrwać około minuty."
    )
    repeat_markup = _prompt_keyboard(pending.transcript_token, repeat=True)

    try:
        result = await generate_custom_analysis_artifact(
            transcript_text=transcript_text,
            prompt=prompt,
            title=transcript_context.title,
            sanitized_title=sanitize_filename(transcript_context.title),
            output_dir=os.path.dirname(transcript_context.transcript_path) or ".",
            executor=_executor,
            artifact_id=secrets.token_hex(4),
            api_key=claude_api_key,
        )
        if result is None:
            await status_message.edit_text(
                "Nie udało się wykonać polecenia. Spróbuj ponownie.",
                reply_markup=repeat_markup,
            )
            return True

        await send_long_message(
            context.bot,
            chat_id,
            result.analysis_text,
            header=f"*Własna analiza: {escape_md(transcript_context.title)}*\n\n",
        )
        with open(result.analysis_path, "rb") as file_obj:
            await context.bot.send_document(
                chat_id=chat_id,
                document=file_obj,
                filename=os.path.basename(result.analysis_path),
                caption=f"Własna analiza transkrypcji: {transcript_context.title}"[:200],
                read_timeout=60,
                write_timeout=60,
            )

        record_download_for(
            context,
            requester_id,
            transcript_context.title,
            "",
            "transcription_custom_prompt",
        )
        await status_message.edit_text(
            "Własna analiza została wysłana.",
            reply_markup=repeat_markup,
        )
    except Exception as exc:
        logging.exception("Custom transcript analysis delivery failed: %s", exc)
        await status_message.edit_text(
            "Wystąpił błąd podczas wykonywania polecenia. Spróbuj ponownie.",
            reply_markup=repeat_markup,
        )

    return True
