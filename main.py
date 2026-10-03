#!/usr/bin/env python3
"""
YouTube Downloader Telegram Bot - Main Entry Point

A Telegram bot for downloading YouTube videos/audio with AI-powered
transcription and summarization capabilities.
"""

import sys
import logging
import threading
import curses

from telegram import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from bot.config import initialize_runtime
from bot.cleanup import monitor_disk_space, periodic_cleanup
from bot.cli import parse_arguments, cli_mode, curses_main
from bot.runtime import attach_runtime, build_app_runtime, get_config_value_for
from bot.handlers.command_access import (
    start,
    help_command,
    logout_command,
    status_command,
    history_command,
    cleanup_command,
    users_command,
)
from bot.handlers.spotify_auth_commands import (
    spotify_login_command,
    spotify_logout_command,
)
from bot.handlers.inbound_media import (
    handle_youtube_link,
    handle_audio_upload,
    handle_video_upload,
)
from bot.telegram_callbacks import handle_callback
from bot.telegram_commands import stop_command
from bot.spotify_oauth import start_spotify_oauth_callback_server

LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'


def configure_logging() -> None:
    """Send INFO and above to stderr (journald under systemd) in bot mode.

    force=True is required: importing bot.config logs at module load, and that
    first call already installs a default WARNING handler, which turns a plain
    basicConfig() here into a silent no-op.
    """
    logging.basicConfig(format=LOG_FORMAT, level=logging.INFO, force=True)
    # httpx logs every request URL at INFO, and Bot API URLs embed the bot
    # token (https://api.telegram.org/bot<TOKEN>/...), so keep it at WARNING.
    logging.getLogger("httpx").setLevel(logging.WARNING)


USER_COMMANDS = [
    BotCommand("start", "Rozpocznij korzystanie z bota"),
    BotCommand("help", "Pomoc i instrukcje"),
    BotCommand("stop", "Zatrzymaj trwające operacje"),
    BotCommand("history", "Historia pobrań"),
    BotCommand("logout", "Wyloguj się"),
]

ADMIN_COMMANDS = [
    BotCommand("status", "Miejsce na dysku"),
    BotCommand("cleanup", "Usuń pliki starsze niż 24 h"),
    BotCommand("users", "Lista autoryzowanych użytkowników"),
    BotCommand("spotify_login", "Połącz konto Spotify"),
    BotCommand("spotify_logout", "Odłącz konto Spotify"),
]


async def set_bot_commands(application):
    """Sets Telegram bot menu commands, scoping admin commands to the admin chat.

    Mirrors the admin rules of `_is_admin` (bot/handlers/command_access.py):
    - ADMIN_CHAT_ID unset: everyone is an admin, so the default scope gets the full list;
    - ADMIN_CHAT_ID valid: the default scope gets user commands, the admin chat gets all;
    - ADMIN_CHAT_ID set but invalid: nobody is an admin, so only user commands are published.
    """
    bot = application.bot
    admin_chat_id = get_config_value_for(application, "ADMIN_CHAT_ID", "")

    if not admin_chat_id:
        await bot.set_my_commands(
            USER_COMMANDS + ADMIN_COMMANDS, scope=BotCommandScopeDefault()
        )
    else:
        await bot.set_my_commands(USER_COMMANDS, scope=BotCommandScopeDefault())
        try:
            admin_id = int(admin_chat_id)
        except (ValueError, TypeError):
            logging.warning(
                "ADMIN_CHAT_ID is not a valid integer (%r); admin commands are not published",
                admin_chat_id,
            )
        else:
            await bot.set_my_commands(
                USER_COMMANDS + ADMIN_COMMANDS, scope=BotCommandScopeChat(admin_id)
            )

    logging.info("Set Telegram bot menu commands")


def start_background_services() -> None:
    """Start background maintenance services used by the Telegram bot."""

    cleanup_thread = threading.Thread(target=periodic_cleanup, daemon=True)
    cleanup_thread.start()
    logging.info("Started automatic file cleanup thread")

    monitor_disk_space()
    start_spotify_oauth_callback_server()


def build_application(runtime=None):
    """Create and configure the Telegram application object."""

    application = (
        ApplicationBuilder()
        .token(get_config_value_for(runtime, "TELEGRAM_BOT_TOKEN", ""))
        .connect_timeout(30)
        .read_timeout(60)
        .write_timeout(60)
        .build()
    )

    if runtime is not None:
        attach_runtime(application, runtime)

    application.job_queue.run_once(lambda context: set_bot_commands(application), when=1)
    return application


ERROR_NOTICE_TEXT = (
    "⚠️ Nie udało się obsłużyć tej akcji z powodu błędu po stronie bota. "
    "Spróbuj ponownie — jeśli to się powtórzy, wyślij link lub plik jeszcze raz."
)
# Transient or user-caused errors: logged, but telling the user would only add noise.
# NOTE: BadRequest subclasses NetworkError in PTB, so it is silent as well.
_SILENT_ERRORS = (NetworkError, RetryAfter, Forbidden)


def _is_message_not_modified(error) -> bool:
    """True for the harmless BadRequest raised when an edit repeats the same content."""

    return isinstance(error, BadRequest) and "message is not modified" in str(error).lower()


async def on_error(update, context) -> None:
    """Log unhandled handler errors and tell the user the action failed."""

    error = context.error
    if _is_message_not_modified(error):
        # Double clicks / repeated progress edits: nothing failed for the user.
        logging.debug("Ignoring 'message is not modified': %s", error)
        return
    logging.error("Unhandled error while processing update", exc_info=error)

    chat = getattr(update, "effective_chat", None)
    if chat is None or isinstance(error, _SILENT_ERRORS):
        return
    try:
        await context.bot.send_message(chat_id=chat.id, text=ERROR_NOTICE_TEXT)
    except Exception:
        logging.warning("Could not deliver error notice to chat %s", chat.id, exc_info=True)


def register_handlers(application) -> None:
    """Register all Telegram command, message, and callback handlers."""

    # Only fresh private/group messages: edited messages and channel posts must
    # not re-trigger link processing, PIN checks or uploads.
    NEW_MESSAGES = filters.UpdateType.MESSAGE

    application.add_handler(CommandHandler("start", start, filters=NEW_MESSAGES))
    application.add_handler(CommandHandler("help", help_command, filters=NEW_MESSAGES))
    application.add_handler(CommandHandler("status", status_command, filters=NEW_MESSAGES))
    application.add_handler(CommandHandler("history", history_command, filters=NEW_MESSAGES))
    application.add_handler(CommandHandler("cleanup", cleanup_command, filters=NEW_MESSAGES))
    application.add_handler(CommandHandler("users", users_command, filters=NEW_MESSAGES))
    application.add_handler(CommandHandler("logout", logout_command, filters=NEW_MESSAGES))
    application.add_handler(CommandHandler("stop", stop_command, filters=NEW_MESSAGES))
    application.add_handler(CommandHandler("spotify_login", spotify_login_command, filters=NEW_MESSAGES))
    application.add_handler(CommandHandler("spotify_logout", spotify_logout_command, filters=NEW_MESSAGES))

    # Handler for text messages (including PIN and links)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & NEW_MESSAGES, handle_youtube_link))

    # NOTE: callbacks and uploads use block=False so a long download, playlist,
    # packing or transcription does not stall every other update (including /stop
    # and other chats) while PTB processes updates one at a time. Text and command
    # handlers stay blocking to keep their ordering. Same idea as the trim job in
    # bot/handlers/trim_callbacks.py; the one-job-per-chat guard for callbacks
    # lives in bot/telegram_callbacks.py (busy set in bot/jobs.py).
    # Handlers for audio uploads (voice messages, audio files, audio documents)
    application.add_handler(MessageHandler(filters.VOICE & NEW_MESSAGES, handle_audio_upload, block=False))
    application.add_handler(MessageHandler(filters.AUDIO & NEW_MESSAGES, handle_audio_upload, block=False))
    audio_doc_filter = (
        filters.Document.MimeType("audio/ogg")
        | filters.Document.MimeType("audio/mpeg")
        | filters.Document.MimeType("audio/mp4")
        | filters.Document.MimeType("audio/x-m4a")
        | filters.Document.MimeType("audio/wav")
        | filters.Document.MimeType("audio/flac")
        | filters.Document.MimeType("audio/opus")
        | filters.Document.MimeType("audio/webm")
        | filters.Document.MimeType("audio/aac")
        | filters.Document.MimeType("audio/amr")
        | filters.Document.MimeType("audio/x-caf")
    )
    application.add_handler(MessageHandler(audio_doc_filter & NEW_MESSAGES, handle_audio_upload, block=False))

    # Handlers for video uploads (native video + video documents)
    video_doc_filter = (
        filters.VIDEO
        | filters.Document.MimeType("video/mp4")
        | filters.Document.MimeType("video/quicktime")
        | filters.Document.MimeType("video/x-matroska")
        | filters.Document.MimeType("video/x-msvideo")
        | filters.Document.MimeType("video/webm")
    )
    application.add_handler(MessageHandler(video_doc_filter & NEW_MESSAGES, handle_video_upload, block=False))

    application.add_handler(CallbackQueryHandler(handle_callback, block=False))
    application.add_error_handler(on_error)


def main():
    """Main function - entry point for the bot."""
    args = parse_arguments()

    if args.cli:
        initialize_runtime()
        cli_mode(args)
        return

    if len(sys.argv) == 1 and sys.stdin.isatty():
        pass

    # Bot mode only: INFO lines on stderr would corrupt the CLI's curses UI.
    configure_logging()
    initialize_runtime()
    start_background_services()
    application = build_application(runtime=build_app_runtime())
    register_handlers(application)

    logging.info("Starting Telegram bot...")
    application.run_polling()


if __name__ == "__main__":
    main()
