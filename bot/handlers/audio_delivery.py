"""Send one audio file, optionally with the ✂️ trim button under it.

Every single-audio send site goes through here (bot/handlers/download_callbacks.py,
bot/handlers/spotify_callbacks.py) and so do trimmed fragments
(bot/handlers/trim_callbacks.py). See also: bot/services/trim_store.py.
"""

from __future__ import annotations

import logging
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.archive import volume_size_for
from bot.mtproto import mtproto_unavailability_reason, send_audio_mtproto
from bot.security_limits import TELEGRAM_UPLOAD_LIMIT_MB
from bot.services.audio_trim_service import AudioTrimError, probe_duration
from bot.services.trim_store import SUPPORTED_EXTENSIONS, TrimSource, discard_source, retain_source

TRIM_BUTTON_LABEL = "✂️ Przytnij"
TRIM_AVAILABLE_HINT = "✂️ Pod plikiem jest przycisk „Przytnij” — działa przez 24 h."


class AudioDeliveryError(RuntimeError):
    """Delivery failed; str(exc) is a Polish, user-facing message."""


def trim_button(token: str, label: str = TRIM_BUTTON_LABEL) -> tuple[str, str]:
    return label, f"trim_src_{token}"


def max_sendable_audio_mb() -> int:
    """Largest file the active transport can deliver, in MiB."""

    if mtproto_unavailability_reason() is None:
        return volume_size_for(use_mtproto=True)
    return TELEGRAM_UPLOAD_LIMIT_MB


async def send_audio_file(
    context,
    chat_id: int,
    path,
    *,
    title: str,
    performer: str | None = None,
    caption: str | None = None,
    thumb_path: str | None = None,
    filename: str | None = None,
    buttons: list[tuple[str, str]] | None = None,
    cancellation=None,
) -> None:
    """Send through the Bot API up to its limit, through MTProto above it."""

    file_path = Path(path)
    display_name = filename or file_path.name
    size_mb = file_path.stat().st_size / (1024 * 1024)

    if size_mb <= TELEGRAM_UPLOAD_LIMIT_MB:
        reply_markup = (
            InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data)] for label, data in buttons])
            if buttons
            else None
        )
        thumb_file = open(thumb_path, "rb") if thumb_path else None
        try:
            with file_path.open("rb") as file_obj:
                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=file_obj,
                    filename=display_name,
                    title=title,
                    performer=performer,
                    caption=caption,
                    thumbnail=thumb_file,
                    reply_markup=reply_markup,
                    read_timeout=120,
                    write_timeout=120,
                )
        finally:
            if thumb_file is not None:
                thumb_file.close()
        return

    reason = mtproto_unavailability_reason()
    if reason is not None:
        raise AudioDeliveryError(
            f"Plik za duży dla Bot API ({size_mb:.0f} MB, limit: {TELEGRAM_UPLOAD_LIMIT_MB} MB).\n{reason}"
        )
    ok = await send_audio_mtproto(
        chat_id,
        str(file_path),
        title=title,
        caption=caption,
        thumb_path=thumb_path,
        performer=performer,
        file_name=display_name,
        buttons=buttons,
        cancellation=cancellation,
    )
    if not ok:
        raise AudioDeliveryError("Wysyłanie pliku przez MTProto nie powiodło się.")


async def send_audio_with_trim(
    context,
    chat_id: int,
    path,
    *,
    title: str,
    performer: str | None = None,
    caption: str | None = None,
    thumb_path: str | None = None,
    cancellation=None,
) -> TrimSource | None:
    """Send one audio file with ✂️ and keep it in the trim store for 24 h.

    Falls back to a plain send when the file cannot be kept (unsupported
    format, low disk, unreadable duration). When a TrimSource is returned the
    file has moved into the store, so callers must not reuse ``path``.
    """

    original = Path(path)
    source = None
    if original.suffix.lower() not in SUPPORTED_EXTENSIONS:
        # retain_source would refuse it anyway; skip the ffprobe run.
        logging.info("Not offering trim for %s: unsupported format", original.name)
    else:
        try:
            duration = await probe_duration(original)
        except AudioTrimError as exc:
            logging.warning("Not offering trim for %s: %s", original.name, exc)
        else:
            source = retain_source(
                chat_id, original, title=title, performer=performer, duration_sec=round(duration)
            )

    if source is None:
        await send_audio_file(
            context, chat_id, original, title=title, performer=performer, caption=caption,
            thumb_path=thumb_path, cancellation=cancellation,
        )
        return None

    try:
        await send_audio_file(
            context, chat_id, source.path, title=title, performer=performer, caption=caption,
            thumb_path=thumb_path, filename=original.name, buttons=[trim_button(source.token)],
            cancellation=cancellation,
        )
    except BaseException:
        discard_source(source)
        raise
    return source
