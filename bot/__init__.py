"""
YouTube Downloader Telegram Bot package exports.

Import failures in runtime modules should surface immediately during package
import instead of being masked by fallback `None` assignments.
"""

from bot.cleanup import (
    cleanup_old_files,
    get_disk_usage,
    monitor_disk_space,
    periodic_cleanup,
)
from bot.config import (
    CONFIG,
    DOWNLOAD_PATH,
    load_authorized_users,
    load_config,
    save_authorized_users,
)
from bot.downloader import download_youtube_video
from bot.downloader_media import download_photo, get_instagram_post_info, is_photo_entry
from bot.downloader_metadata import get_video_info
from bot.downloader_playlist import get_playlist_info, is_playlist_url, strip_playlist_params
from bot.downloader_subtitles import download_subtitles, get_available_subtitles, parse_subtitle_file
from bot.downloader_validation import sanitize_filename
from bot.security import (
    MAX_PLAYLIST_ITEMS,
    check_rate_limit,
    detect_platform,
    estimate_file_size,
    manage_authorized_user,
    user_playlist_data,
    user_urls,
    validate_url,
    validate_youtube_url,
)
from bot.telegram_callbacks import (
    back_to_main_menu,
    download_file,
    download_playlist,
    handle_callback,
    handle_formats_list,
    handle_subtitle_download,
    show_audio_summary_options,
    show_subtitle_source_menu,
    show_subtitle_summary_options,
    show_summary_options,
    transcribe_audio_file,
)
from bot.telegram_commands import (
    cleanup_command,
    handle_audio_upload,
    handle_pin,
    handle_video_upload,
    handle_youtube_link,
    help_command,
    process_playlist_link,
    process_youtube_link,
    start,
    status_command,
    users_command,
)
from bot.transcription import (
    estimate_token_count,
    generate_summary,
    is_text_too_long_for_correction,
    is_text_too_long_for_summary,
    transcribe_mp3_file,
)

__all__ = [
    "CONFIG",
    "DOWNLOAD_PATH",
    "load_config",
    "load_authorized_users",
    "save_authorized_users",
    "check_rate_limit",
    "validate_url",
    "validate_youtube_url",
    "detect_platform",
    "manage_authorized_user",
    "estimate_file_size",
    "user_urls",
    "cleanup_old_files",
    "get_disk_usage",
    "monitor_disk_space",
    "periodic_cleanup",
    "transcribe_mp3_file",
    "generate_summary",
    "get_video_info",
    "download_youtube_video",
    "sanitize_filename",
    "get_available_subtitles",
    "download_subtitles",
    "parse_subtitle_file",
    "is_playlist_url",
    "get_playlist_info",
    "strip_playlist_params",
    "get_instagram_post_info",
    "is_photo_entry",
    "download_photo",
    "start",
    "help_command",
    "status_command",
    "cleanup_command",
    "users_command",
    "handle_youtube_link",
    "handle_pin",
    "process_youtube_link",
    "process_playlist_link",
    "handle_audio_upload",
    "handle_video_upload",
    "handle_callback",
    "download_file",
    "download_playlist",
    "handle_formats_list",
    "show_summary_options",
    "back_to_main_menu",
    "transcribe_audio_file",
    "show_audio_summary_options",
    "show_subtitle_source_menu",
    "handle_subtitle_download",
    "show_subtitle_summary_options",
]
