"""
Downloader module for YouTube Downloader Telegram Bot.

Handles video/audio downloading via yt-dlp.
"""

import logging
import re
from datetime import datetime

import yt_dlp


FORMAT_ID_PATTERN = re.compile(r"^(?:best|worst|bestvideo|bestaudio|worstaudio|worstvideo)$|^(?:\d+[pP]?)$|^(?:\d+(?:[+x]\d+){0,3})$")
SUPPORTED_AUDIO_FORMATS = ("mp3", "m4a", "wav", "flac", "ogg", "opus")
AUDIO_FORMATS = set(SUPPORTED_AUDIO_FORMATS)

QUALITY_RANGE_BY_CODEC = {
    "mp3": (0, 330),
    "opus": (0, 9),
    "vorbis": (0, 9),
    "ogg": (0, 9),
}


def sanitize_filename(filename):
    """
    Removes invalid characters from filename.

    Args:
        filename: Original filename

    Returns:
        str: Sanitized filename
    """
    invalid_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
    for char in invalid_chars:
        filename = filename.replace(char, '-')
    return filename


def is_valid_ytdlp_format_id(format_id):
    """Returns True if format ID is safe and supported by CLI/TG UI flows."""

    if not isinstance(format_id, str):
        return False
    normalized = format_id.strip().lower()
    return bool(FORMAT_ID_PATTERN.fullmatch(normalized))


def is_valid_audio_format(audio_format):
    """Returns True for allowed audio conversion formats."""

    if not isinstance(audio_format, str):
        return False
    return audio_format.strip().lower() in AUDIO_FORMATS


def is_valid_audio_quality(audio_format, audio_quality):
    """Returns True when audio quality is supported for selected codec."""

    if not isinstance(audio_format, str):
        return False

    normalized_format = audio_format.strip().lower()
    if normalized_format not in SUPPORTED_AUDIO_FORMATS:
        return False

    if isinstance(audio_quality, bool):
        return False
    try:
        normalized_quality = int(str(audio_quality).strip())
    except (TypeError, ValueError):
        return False

    if normalized_quality < 0:
        return False

    quality_range = QUALITY_RANGE_BY_CODEC.get(normalized_format)
    if quality_range is None:
        return True

    min_quality, max_quality = quality_range
    return min_quality <= normalized_quality <= max_quality


def normalize_format_id(format_id, *, default="best"):
    """Normalizes shortcut/legacy format aliases."""

    if format_id is None:
        return None

    normalized = format_id.strip().lower()
    if normalized == "auto":
        return default
    return normalized


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


def get_basic_ydl_opts():
    """
    Returns basic configuration for yt-dlp.

    Returns:
        dict: yt-dlp options dictionary
    """
    return {
        'quiet': True,
        'no_warnings': True,
        'progress_hooks': [progress_hook],
    }


def get_video_info(url):
    """
    Gets video information without downloading.

    Args:
        url: YouTube video URL

    Returns:
        dict or None: Video info dictionary or None on error
    """
    try:
        ydl_opts = get_basic_ydl_opts()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info
    except Exception as e:
        print(f"Error getting video info: {str(e)}")
        return None


def download_youtube_video(url, format_id=None, audio_only=False, audio_format='mp3', audio_quality='192'):
    """
    Downloads YouTube video or audio.

    Args:
        url: YouTube video URL
        format_id: Specific format ID to download
        audio_only: If True, download audio only
        audio_format: Audio format (mp3, m4a, wav, flac)
        audio_quality: Audio quality (bitrate)

    Returns:
        bool: True on success, False on error
    """
    logging.debug(f"Starting download for URL: {url}, format: {format_id}...")
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


def validate_url(url):
    """
    Checks if URL is a valid YouTube link.

    Args:
        url: URL to validate

    Returns:
        bool: True if valid, False otherwise
    """
    from bot.security import validate_youtube_url

    valid = validate_youtube_url(url)
    if not valid:
        print("Error: Invalid URL. Provide a YouTube video link.")
    return valid
