"""
Downloader compatibility facade.

Re-exports from narrower downloader modules so existing consumers
(tests, CLI, external scripts) keep working with ``from bot.downloader import …``.
"""

import os

from bot.downloader_media import (
    _load_instagram_cookies as _media_load_instagram_cookies,
    download_photo as _download_photo,
    download_thumbnail as _download_thumbnail,
    get_instagram_post_info as _get_instagram_post_info,
    is_photo_entry as _is_photo_entry,
)
from bot.downloader_metadata import get_video_info as _get_video_info
from bot.downloader_playlist import (
    get_playlist_info,
    is_playlist_url,
    is_pure_playlist_url,
    strip_playlist_params,
)
from bot.downloader_subtitles import (
    download_subtitles as _download_subtitles,
    get_available_subtitles as _get_available_subtitles,
    parse_subtitle_file as _parse_subtitle_file,
)
from bot.config import COOKIES_FILE
from bot.downloader_validation import (
    AUDIO_FORMATS,
    SUPPORTED_AUDIO_FORMATS,
    is_valid_audio_format,
    is_valid_audio_quality,
    is_valid_ytdlp_format_id,
    normalize_format_id,
    parse_time_seconds,
    sanitize_filename,
)

from bot.downloader_core import (  # noqa: E402 — re-exports for compatibility
    download_youtube_video,
    get_basic_ydl_opts,
    progress_hook,
)


def get_video_info(url):
    """Gets video information without downloading."""

    return _get_video_info(url, cookies_file=COOKIES_FILE)


def get_available_subtitles(info: dict) -> dict:
    """Return available subtitle info from yt-dlp info dict."""

    return _get_available_subtitles(info)


def download_subtitles(url, lang, output_dir, auto=False, title=""):
    """Download subtitles via yt-dlp with skip_download=True."""

    return _download_subtitles(url, lang, output_dir, auto=auto, title=title)


def parse_subtitle_file(file_path: str) -> str:
    """Parse a VTT/SRT subtitle file into clean plain text."""

    return _parse_subtitle_file(file_path)

def _load_instagram_cookies():
    """Load Instagram session cookies from the configured cookies file."""

    return _media_load_instagram_cookies(cookies_file=COOKIES_FILE)


def get_instagram_post_info(url):
    """Fetch Instagram post info using configured cookies-aware media helpers."""

    return _get_instagram_post_info(url, cookies_file=COOKIES_FILE)


def is_photo_entry(info: dict) -> bool:
    """Return True if an info dict represents a photo instead of video."""

    return _is_photo_entry(info)


def download_photo(url: str, output_path: str) -> str | None:
    """Download a photo from direct URL."""

    return _download_photo(url, output_path)


def download_thumbnail(info: dict, output_dir: str, embed: bool = False) -> str | None:
    """Download video thumbnail from yt-dlp info dict."""

    return _download_thumbnail(info, output_dir, embed=embed)


def validate_url(url):
    """
    Checks if URL is from a supported platform.

    Args:
        url: URL to validate

    Returns:
        bool: True if valid, False otherwise
    """
    from bot.security import validate_url as _validate_url

    return _validate_url(url)
