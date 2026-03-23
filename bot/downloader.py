"""
Downloader module for YouTube Downloader Telegram Bot.

Handles video/audio downloading via yt-dlp.
"""

import logging
import os
from datetime import datetime

import requests
import yt_dlp
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

def progress_hook(d):
    """
    Progress hook called by yt-dlp to track download progress.

    Args:
        d: Progress dictionary from yt-dlp
    """
    if d['status'] == 'downloading':
        if d.get('total_bytes'):
            percent = round(float(d['downloaded_bytes'] / d['total_bytes'] * 100), 1)
            print(f"\rDownloading: {percent}% [{d['downloaded_bytes']/1024/1024:.1f}MB / {d['total_bytes']/1024/1024:.1f}MB]", end='')
        elif d.get('total_bytes_estimate'):
            percent = round(float(d['downloaded_bytes'] / d['total_bytes_estimate'] * 100), 1)
            print(f"\rDownloading: {percent}% [{d['downloaded_bytes']/1024/1024:.1f}MB / estimated {d['total_bytes_estimate']/1024/1024:.1f}MB]", end='')
        else:
            print(f"\rDownloading: [{d['downloaded_bytes']/1024/1024:.1f}MB downloaded]", end='')
    elif d['status'] == 'finished':
        print("\nDownload finished, processing...")
    elif d['status'] == 'error':
        print(f"\nError during download: {d.get('error')}")


def get_basic_ydl_opts(*, include_progress_hooks: bool = False):
    """
    Returns basic configuration for yt-dlp.

    Returns:
        dict: yt-dlp options dictionary
    """
    opts = {
        'quiet': True,
        'no_warnings': True,
    }
    if include_progress_hooks:
        opts['progress_hooks'] = [progress_hook]
    if os.path.exists(COOKIES_FILE):
        opts['cookiefile'] = COOKIES_FILE
    return opts


def get_video_info(url):
    """
    Gets video information without downloading.

    Args:
        url: YouTube video URL

    Returns:
        dict or None: Video info dictionary or None on error
    """
    return _get_video_info(url, cookies_file=COOKIES_FILE)


def download_youtube_video(
    url,
    format_id=None,
    audio_only=False,
    audio_format='mp3',
    audio_quality='192',
    time_range_start=None,
    time_range_end=None,
    video_duration=None,
):
    """
    Downloads YouTube video or audio.

    Args:
        url: YouTube video URL
        format_id: Specific format ID to download
        audio_only: If True, download audio only
        audio_format: Audio format (mp3, m4a, wav, flac)
        audio_quality: Audio quality (bitrate)
        time_range_start: Start time (HH:MM:SS, MM:SS, or seconds)
        time_range_end: End time (HH:MM:SS, MM:SS, or seconds)
        video_duration: Total video duration in seconds (optional, for range validation)

    Returns:
        bool: True on success, False on error
    """
    logging.debug("Starting download for URL: %s, format: %s...", url, format_id)
    try:
        normalized_format_id = normalize_format_id(format_id)
        normalized_audio_format = audio_format.strip().lower() if audio_format else "mp3"
        normalized_audio_quality = str(audio_quality).strip() if audio_quality is not None else "192"
        if normalized_audio_format and not is_valid_audio_format(normalized_audio_format):
            print(f"[ERROR] Unsupported audio format: {normalized_audio_format}")
            return False

        if audio_only and not is_valid_audio_quality(normalized_audio_format, normalized_audio_quality):
            print(f"[ERROR] Unsupported audio quality {normalized_audio_quality} for format {normalized_audio_format}")
            return False

        normalized_time_range_start = parse_time_seconds(time_range_start)
        normalized_time_range_end = parse_time_seconds(time_range_end)

        if time_range_start is not None and time_range_end is not None:
            if normalized_time_range_start is None or normalized_time_range_end is None:
                print("[ERROR] Invalid time range values.")
                return False
            if normalized_time_range_start >= normalized_time_range_end:
                print("[ERROR] Start time must be earlier than end time.")
                return False

        if (time_range_start is None) != (time_range_end is None):
            print("[ERROR] Both --start and --to must be provided.")
            return False

        # Validate time range against video duration when known
        if video_duration is not None and normalized_time_range_start is not None:
            if normalized_time_range_start >= video_duration:
                print(f"[ERROR] Start time ({normalized_time_range_start}s) is at or beyond video duration ({video_duration}s).")
                return False
            if normalized_time_range_end > video_duration:
                print(f"[ERROR] End time ({normalized_time_range_end}s) exceeds video duration ({video_duration}s).")
                return False

        if normalized_format_id is not None and not is_valid_ytdlp_format_id(normalized_format_id):
            print(f"[ERROR] Unsupported format id: {normalized_format_id}")
            return False

        current_date = datetime.now().strftime("%Y-%m-%d")

        ydl_opts = {
            'outtmpl': f'{current_date} %(title)s.%(ext)s',
            'progress_hooks': [progress_hook],
            'quiet': True,
            'no_warnings': False,
            'ignoreerrors': False,
            'socket_timeout': 30,
            'retries': 3,
            'fragment_retries': 3,
        }
        if os.path.exists(COOKIES_FILE):
            ydl_opts['cookiefile'] = COOKIES_FILE

        if audio_only:
            print(f"[DEBUG] Configuring audio-only download ({normalized_audio_format})")
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': normalized_audio_format,
                    'preferredquality': normalized_audio_quality,
                }],
            })
        elif format_id:
            ydl_opts['format'] = normalized_format_id
            print(f"[DEBUG] Set format: {normalized_format_id}")
        else:
            print("[DEBUG] Using default format (best quality)")

        if normalized_time_range_start is not None:
            ydl_opts['download_sections'] = [{
                'start_time': normalized_time_range_start,
                'end_time': normalized_time_range_end,
            }]
            ydl_opts['force_keyframes_at_cuts'] = True

        print("[DEBUG] Initializing YoutubeDL...")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            print("[DEBUG] Starting download...")
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'Unknown title')
            print(f"[DEBUG] Downloaded file info: Title={title}")

        print(f"\nDownload completed successfully")
        return True

    except Exception as e:
        print(f"[DEBUG] Error during download: {str(e)}")
        print(f"Error: {str(e)}")
        return False


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
