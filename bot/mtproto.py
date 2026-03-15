"""
MTProto file download module using pyrogram.

Provides large file download capability (>20 MB) via Telegram's MTProto
protocol, bypassing the standard Bot API 20 MB limit on getFile.

Requires: pip install pyrogram
Configuration: TELEGRAM_API_ID and TELEGRAM_API_HASH in api_key.md or env vars.
These are free credentials from https://my.telegram.org
"""

import logging
import os

from bot.config import CONFIG


def is_mtproto_available() -> bool:
    """Checks if pyrogram is installed and API credentials are configured."""
    if not CONFIG.get("TELEGRAM_API_ID") or not CONFIG.get("TELEGRAM_API_HASH"):
        return False
    try:
        import pyrogram  # noqa: F401
        return True
    except ImportError:
        return False


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

    api_id = CONFIG.get("TELEGRAM_API_ID", "")
    api_hash = CONFIG.get("TELEGRAM_API_HASH", "")
    if not api_id or not api_hash:
        logging.error("TELEGRAM_API_ID/TELEGRAM_API_HASH not configured")
        return False

    # Use in-memory session to avoid session file creation
    session_name = f"bot_download_{chat_id}_{message_id}"
    session_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "downloads")
    os.makedirs(session_dir, exist_ok=True)

    client = Client(
        name=session_name,
        api_id=int(api_id),
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
