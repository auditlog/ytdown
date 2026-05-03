"""
MTProto file transfer module using pyrogram.

Provides large file download (>20 MB) and upload (>50 MB) capability
via Telegram's MTProto protocol, bypassing standard Bot API size limits.

Requires: pip install pyrogram
Configuration: TELEGRAM_API_ID and TELEGRAM_API_HASH in api_key.md or env vars.
These are free credentials from https://my.telegram.org
"""

import asyncio
import logging
import os
from typing import TYPE_CHECKING

from bot.config import get_runtime_value

if TYPE_CHECKING:
    from bot.jobs import JobCancellation


def mtproto_unavailability_reason() -> str | None:
    """Return a user-facing explanation of what is missing for MTProto, or None.

    Distinguishes between the three failure modes so callers can give precise
    guidance instead of always blaming missing API credentials (which used to
    mislead users who simply had not installed pyrogram).
    """

    try:
        import pyrogram  # noqa: F401
        has_pyrogram = True
    except ImportError:
        has_pyrogram = False
    except Exception as exc:
        # pyrogram <2.x calls asyncio.get_event_loop() at import time which
        # raises RuntimeError on Python 3.12+ when no loop is running. Treat
        # any other import-time failure as "not usable" rather than crashing
        # the surrounding handler.
        logging.warning("pyrogram import failed: %s", exc)
        has_pyrogram = False

    has_creds = bool(get_runtime_value("TELEGRAM_API_ID")) and bool(
        get_runtime_value("TELEGRAM_API_HASH")
    )

    if has_pyrogram and has_creds:
        return None
    if not has_pyrogram and not has_creds:
        return (
            "Zainstaluj pakiet pyrogram oraz skonfiguruj "
            "TELEGRAM_API_ID i TELEGRAM_API_HASH."
        )
    if not has_pyrogram:
        return "Zainstaluj pakiet pyrogram, aby wysyłać większe pliki."
    return "Skonfiguruj TELEGRAM_API_ID i TELEGRAM_API_HASH, aby wysyłać większe pliki."


def is_mtproto_available() -> bool:
    """Checks if pyrogram is installed and API credentials are configured."""
    return mtproto_unavailability_reason() is None


def _parse_api_id(value) -> int | None:
    """Return TELEGRAM_API_ID as int, or None when the value is invalid.

    Guards pyrogram Client construction: a non-numeric API_ID (e.g. a typo in
    api_key.md) would otherwise raise ValueError outside the surrounding
    try/except in send_*_mtproto / download_file_mtproto and crash the handler
    instead of returning False with a logged reason.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        logging.error("TELEGRAM_API_ID is not a valid integer: %r", value)
        return None


async def download_file_mtproto(bot_token: str, chat_id: int, message_id: int, dest_path: str) -> bool:
    """Downloads a file from Telegram via MTProto (no size limit).

    Creates a temporary pyrogram Client, downloads the media from the
    specified message, and saves it to dest_path.

    Args:
        bot_token: Telegram bot token for authentication.
        chat_id: Chat ID where the file message was sent.
        message_id: Message ID containing the file.
        dest_path: Local file path to save the downloaded file.

    Returns:
        True on success, False on error.
    """
    try:
        from pyrogram import Client
    except ImportError:
        logging.error("pyrogram not installed — cannot download large files")
        return False

    api_id = get_runtime_value("TELEGRAM_API_ID", "")
    api_hash = get_runtime_value("TELEGRAM_API_HASH", "")
    if not api_id or not api_hash:
        logging.error("TELEGRAM_API_ID/TELEGRAM_API_HASH not configured")
        return False

    api_id_int = _parse_api_id(api_id)
    if api_id_int is None:
        return False

    # Use in-memory session to avoid session file creation
    session_name = f"bot_download_{chat_id}_{message_id}"
    session_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "downloads")
    os.makedirs(session_dir, exist_ok=True)

    client = Client(
        name=session_name,
        api_id=api_id_int,
        api_hash=api_hash,
        bot_token=bot_token,
        workdir=session_dir,
        in_memory=True,
    )

    try:
        async with client:
            msg = await client.get_messages(chat_id, message_id)
            if not msg or not msg.media:
                logging.error("MTProto: message %d in chat %d has no media", message_id, chat_id)
                return False

            downloaded = await msg.download(file_name=dest_path)
            if downloaded and os.path.exists(dest_path):
                logging.info("MTProto download OK: %s (%.1f MB)",
                             os.path.basename(dest_path),
                             os.path.getsize(dest_path) / (1024 * 1024))
                return True

            logging.error("MTProto download returned no path")
            return False

    except Exception as e:
        logging.error("MTProto download failed: %s", e)
        return False


def _build_client(chat_id: int, tag: str, api_id: int, api_hash: str) -> "Client":
    """Create a pyrogram Client configured for bot-mode MTProto operations.

    Callers are expected to have already validated api_id via _parse_api_id so
    this function never raises for malformed configuration.
    """

    from pyrogram import Client

    session_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "downloads"
    )
    os.makedirs(session_dir, exist_ok=True)

    return Client(
        name=f"bot_{tag}_{chat_id}",
        api_id=api_id,
        api_hash=api_hash,
        bot_token=get_runtime_value("TELEGRAM_BOT_TOKEN", ""),
        workdir=session_dir,
        in_memory=True,
    )


async def send_audio_mtproto(
    chat_id: int,
    file_path: str,
    title: str | None = None,
    caption: str | None = None,
    thumb_path: str | None = None,
    *,
    cancellation: "JobCancellation | None" = None,
) -> bool:
    """Send an audio file via MTProto (up to 2 GB).

    When cancellation is provided, the underlying upload task is attached
    so /stop can cancel it.

    Args:
        chat_id: Destination chat ID.
        file_path: Local path to the audio file.
        title: Audio track title shown in the player.
        caption: Message caption.
        thumb_path: Optional thumbnail image path.
        cancellation: Optional job cancellation handle; when set, the upload
            coroutine is wrapped in a Task and assigned to
            cancellation.pyrogram_task so /stop can abort it.

    Returns:
        True on success, False on error.
    """

    try:
        from pyrogram import Client  # noqa: F401
    except ImportError:
        logging.error("pyrogram not installed — cannot send large audio")
        return False

    api_id = get_runtime_value("TELEGRAM_API_ID", "")
    api_hash = get_runtime_value("TELEGRAM_API_HASH", "")
    if not api_id or not api_hash:
        logging.error("TELEGRAM_API_ID/TELEGRAM_API_HASH not configured")
        return False

    api_id_int = _parse_api_id(api_id)
    if api_id_int is None:
        return False

    client = _build_client(chat_id, "send_audio", api_id_int, api_hash)

    try:
        async with client:
            coro = client.send_audio(
                chat_id=chat_id,
                audio=file_path,
                title=title,
                caption=caption,
                thumb=thumb_path,
            )
            if cancellation is not None:
                task = asyncio.ensure_future(coro)
                cancellation.pyrogram_task = task
                try:
                    await task
                except asyncio.CancelledError:
                    return False
            else:
                await coro
            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            logging.info(
                "MTProto send_audio OK: %s (%.1f MB) to chat %d",
                os.path.basename(file_path), file_size_mb, chat_id,
            )
            return True
    except Exception as e:
        logging.error("MTProto send_audio failed: %s", e)
        return False


async def send_video_mtproto(
    chat_id: int,
    file_path: str,
    caption: str | None = None,
    thumb_path: str | None = None,
    *,
    cancellation: "JobCancellation | None" = None,
) -> bool:
    """Send a video file via MTProto (up to 2 GB).

    When cancellation is provided, the underlying upload task is attached
    so /stop can cancel it.

    Args:
        chat_id: Destination chat ID.
        file_path: Local path to the video file.
        caption: Message caption.
        thumb_path: Optional thumbnail image path.
        cancellation: Optional job cancellation handle; when set, the upload
            coroutine is wrapped in a Task and assigned to
            cancellation.pyrogram_task so /stop can abort it.

    Returns:
        True on success, False on error.
    """

    try:
        from pyrogram import Client  # noqa: F401
    except ImportError:
        logging.error("pyrogram not installed — cannot send large video")
        return False

    api_id = get_runtime_value("TELEGRAM_API_ID", "")
    api_hash = get_runtime_value("TELEGRAM_API_HASH", "")
    if not api_id or not api_hash:
        logging.error("TELEGRAM_API_ID/TELEGRAM_API_HASH not configured")
        return False

    api_id_int = _parse_api_id(api_id)
    if api_id_int is None:
        return False

    client = _build_client(chat_id, "send_video", api_id_int, api_hash)

    try:
        async with client:
            coro = client.send_video(
                chat_id=chat_id,
                video=file_path,
                caption=caption,
                thumb=thumb_path,
            )
            if cancellation is not None:
                task = asyncio.ensure_future(coro)
                cancellation.pyrogram_task = task
                try:
                    await task
                except asyncio.CancelledError:
                    return False
            else:
                await coro
            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            logging.info(
                "MTProto send_video OK: %s (%.1f MB) to chat %d",
                os.path.basename(file_path), file_size_mb, chat_id,
            )
            return True
    except Exception as e:
        logging.error("MTProto send_video failed: %s", e)
        return False


async def send_document_mtproto(
    chat_id: int,
    file_path: str,
    caption: str | None = None,
    file_name: str | None = None,
    *,
    cancellation: "JobCancellation | None" = None,
) -> bool:
    """Send a document file via MTProto (up to 2 GB).

    Used to ship 7z volumes (.7z.001, ...) so Telegram does not try to
    render them as media. ``file_name`` (when provided) overrides the
    visible attachment name in the chat — useful when the on-disk path
    contains a workspace prefix we don't want users to see.

    When cancellation is provided, the underlying upload task is attached
    so /stop can cancel it.

    Args:
        chat_id: Destination chat ID.
        file_path: Local path to the document.
        caption: Optional message caption.
        file_name: Optional override for the displayed filename.
        cancellation: Optional job cancellation handle; when set, the upload
            coroutine is wrapped in a Task and assigned to
            cancellation.pyrogram_task so /stop can abort it.

    Returns:
        True on success, False on error.
    """

    try:
        from pyrogram import Client  # noqa: F401
    except ImportError:
        logging.error("pyrogram not installed — cannot send large document")
        return False

    api_id = get_runtime_value("TELEGRAM_API_ID", "")
    api_hash = get_runtime_value("TELEGRAM_API_HASH", "")
    if not api_id or not api_hash:
        logging.error("TELEGRAM_API_ID/TELEGRAM_API_HASH not configured")
        return False

    api_id_int = _parse_api_id(api_id)
    if api_id_int is None:
        return False

    client = _build_client(chat_id, "send_document", api_id_int, api_hash)

    try:
        async with client:
            send_kwargs: dict = {
                "chat_id": chat_id,
                "document": file_path,
                "caption": caption,
            }
            if file_name is not None:
                send_kwargs["file_name"] = file_name
            coro = client.send_document(**send_kwargs)
            if cancellation is not None:
                task = asyncio.ensure_future(coro)
                cancellation.pyrogram_task = task
                try:
                    await task
                except asyncio.CancelledError:
                    return False
            else:
                await coro
            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            logging.info(
                "MTProto send_document OK: %s (%.1f MB) to chat %d",
                os.path.basename(file_path), file_size_mb, chat_id,
            )
            return True
    except Exception as e:
        logging.error("MTProto send_document failed: %s", e)
        return False
