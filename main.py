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

from telegram import BotCommand
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
from bot.telegram_commands import (
    start,
    help_command,
    logout_command,
    status_command,
    history_command,
    cleanup_command,
    users_command,
    handle_youtube_link,
    handle_audio_upload,
    handle_video_upload,
)
from bot.telegram_callbacks import handle_callback

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)


async def set_bot_commands(application):
    """Sets Telegram bot menu commands."""
    commands = [
        BotCommand("start", "Rozpocznij korzystanie z bota"),
        BotCommand("help", "Pomoc i instrukcje"),
        BotCommand("status", "Sprawdź przestrzeń dyskową"),
        BotCommand("history", "Historia pobrań"),
        BotCommand("cleanup", "Usuń stare pliki (>24h)"),
        BotCommand("users", "Zarządzanie użytkownikami"),
        BotCommand("logout", "Wyloguj się z bota")
    ]

    await application.bot.set_my_commands(commands)
    logging.info("Set Telegram bot menu commands")


def start_background_services() -> None:
    """Start background maintenance services used by the Telegram bot."""

    cleanup_thread = threading.Thread(target=periodic_cleanup, daemon=True)
    cleanup_thread.start()
    logging.info("Started automatic file cleanup thread")

    monitor_disk_space()


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


def register_handlers(application) -> None:
    """Register all Telegram command, message, and callback handlers."""

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("cleanup", cleanup_command))
    application.add_handler(CommandHandler("users", users_command))
    application.add_handler(CommandHandler("logout", logout_command))

    # Handler for text messages (including PIN and links)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_youtube_link))

    # Handlers for audio uploads (voice messages, audio files, audio documents)
    application.add_handler(MessageHandler(filters.VOICE, handle_audio_upload))
    application.add_handler(MessageHandler(filters.AUDIO, handle_audio_upload))
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
    application.add_handler(MessageHandler(audio_doc_filter, handle_audio_upload))

    # Handlers for video uploads (native video + video documents)
    video_doc_filter = (
        filters.VIDEO
        | filters.Document.MimeType("video/mp4")
        | filters.Document.MimeType("video/quicktime")
        | filters.Document.MimeType("video/x-matroska")
        | filters.Document.MimeType("video/x-msvideo")
        | filters.Document.MimeType("video/webm")
    )
    application.add_handler(MessageHandler(video_doc_filter, handle_video_upload))

    application.add_handler(CallbackQueryHandler(handle_callback))


def main():
    """Main function - entry point for the bot."""
    args = parse_arguments()

    if args.cli:
        initialize_runtime()
        cli_mode(args)
        return

    if len(sys.argv) == 1 and sys.stdin.isatty():
        pass

    initialize_runtime()
    start_background_services()
    application = build_application(runtime=build_app_runtime())
    register_handlers(application)

    logging.info("Starting Telegram bot...")
    application.run_polling()


if __name__ == "__main__":
    main()
