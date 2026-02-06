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

from bot.config import BOT_TOKEN
from bot.cleanup import monitor_disk_space, periodic_cleanup
from bot.cli import parse_arguments, cli_mode, curses_main
from bot.telegram_commands import (
    start,
    help_command,
    status_command,
    history_command,
    cleanup_command,
    users_command,
    handle_youtube_link,
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
        BotCommand("users", "Zarządzanie użytkownikami")
    ]

    await application.bot.set_my_commands(commands)
    logging.info("Set Telegram bot menu commands")


def main():
    """Main function - entry point for the bot."""
    # Parse command line arguments
    args = parse_arguments()

    # Check for CLI mode
    if args.cli:
        cli_mode(args)
        return

    # Check for interactive mode (no arguments)
    if len(sys.argv) == 1 and sys.stdin.isatty():
        # If running interactively without arguments, could start curses menu
        # But default is to start Telegram bot
        pass

    # Start cleanup thread
    cleanup_thread = threading.Thread(target=periodic_cleanup, daemon=True)
    cleanup_thread.start()
    logging.info("Started automatic file cleanup thread")

    # Initial disk space check
    monitor_disk_space()

    # Create bot application
    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(60)
        .write_timeout(60)
        .build()
    )

    # Set bot commands menu
    application.job_queue.run_once(lambda context: set_bot_commands(application), when=1)

    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("cleanup", cleanup_command))
    application.add_handler(CommandHandler("users", users_command))

    # Handler for text messages (including PIN and links)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_youtube_link))

    application.add_handler(CallbackQueryHandler(handle_callback))

    # Start the bot
    logging.info("Starting Telegram bot...")
    application.run_polling()


if __name__ == "__main__":
    main()
