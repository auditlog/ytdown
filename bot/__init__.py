"""
YouTube Downloader Telegram Bot - Package

This package contains all modules for the YouTube downloader bot.
"""

# Core modules (minimal dependencies)
from bot.config import (
    CONFIG,
    BOT_TOKEN,
    PIN_CODE,
    DOWNLOAD_PATH,
    authorized_users,
    load_config,
    load_authorized_users,
    save_authorized_users,
)

from bot.security import (
    check_rate_limit,
    validate_youtube_url,
    manage_authorized_user,
    estimate_file_size,
    user_urls,
)

from bot.cleanup import (
    cleanup_old_files,
    get_disk_usage,
    monitor_disk_space,
    periodic_cleanup,
)

# Modules with optional dependencies (graceful handling)
try:
    from bot.transcription import (
        transcribe_mp3_file,
        generate_summary,
    )
except ImportError:
    transcribe_mp3_file = None
    generate_summary = None

try:
    from bot.downloader import (
        get_video_info,
        download_youtube_video,
        sanitize_filename,
    )
except ImportError:
    get_video_info = None
    download_youtube_video = None
    sanitize_filename = None

try:
    from bot.telegram_commands import (
        start,
        help_command,
        status_command,
        cleanup_command,
        users_command,
        handle_youtube_link,
        handle_pin,
        process_youtube_link,
        handle_audio_upload,
    )
except ImportError:
    start = None
    help_command = None
    status_command = None
    cleanup_command = None
    users_command = None
    handle_youtube_link = None
    handle_pin = None
    process_youtube_link = None
    handle_audio_upload = None

try:
    from bot.telegram_callbacks import (
        handle_callback,
        download_file,
        handle_formats_list,
        show_summary_options,
        back_to_main_menu,
        transcribe_audio_file,
        show_audio_summary_options,
    )
except ImportError:
    handle_callback = None
    download_file = None
    handle_formats_list = None
    show_summary_options = None
    back_to_main_menu = None
    transcribe_audio_file = None
    show_audio_summary_options = None

__all__ = [
    # Config
    'CONFIG',
    'BOT_TOKEN',
    'PIN_CODE',
    'DOWNLOAD_PATH',
    'authorized_users',
    'load_config',
    'load_authorized_users',
    'save_authorized_users',
    # Security
    'check_rate_limit',
    'validate_youtube_url',
    'manage_authorized_user',
    'estimate_file_size',
    'user_urls',
    # Cleanup
    'cleanup_old_files',
    'get_disk_usage',
    'monitor_disk_space',
    'periodic_cleanup',
    # Transcription
    'transcribe_mp3_file',
    'generate_summary',
    # Downloader
    'get_video_info',
    'download_youtube_video',
    'sanitize_filename',
    # Telegram commands
    'start',
    'help_command',
    'status_command',
    'cleanup_command',
    'users_command',
    'handle_youtube_link',
    'handle_pin',
    'process_youtube_link',
    'handle_audio_upload',
    # Telegram callbacks
    'handle_callback',
    'download_file',
    'handle_formats_list',
    'show_summary_options',
    'back_to_main_menu',
    'transcribe_audio_file',
    'show_audio_summary_options',
]
