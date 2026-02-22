"""
Downloader module for YouTube Downloader Telegram Bot.

Handles video/audio downloading via yt-dlp.
"""

import logging
import os
import re
from datetime import datetime

import yt_dlp

COOKIES_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cookies.txt")


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
        str: Sanitized filename safe for filesystem use
    """
    invalid_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
    for char in invalid_chars:
        filename = filename.replace(char, '-')
    # Remove path traversal sequences
    filename = filename.replace('..', '')
    # Remove control characters
    filename = ''.join(c for c in filename if c.isprintable())
    # Limit length (preserve extension room)
    if len(filename) > 200:
        filename = filename[:200]
    # Fallback for empty filename
    if not filename.strip():
        filename = "download"
    return filename.strip()


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


def parse_time_seconds(time_value):
    """Converts HH:MM:SS, MM:SS, or seconds input into integer seconds."""
    if time_value is None:
        return None

    if isinstance(time_value, bool):
        return None
    if isinstance(time_value, int):
        if time_value < 0:
            return None
        return time_value
    if isinstance(time_value, float):
        if time_value < 0:
            return None
        return int(time_value)

    if not isinstance(time_value, str):
        return None

    time_str = time_value.strip()
    if not time_str:
        return None

    parts = time_str.split(':')
    if len(parts) not in {1, 2, 3}:
        return None

    try:
        values = [int(part) for part in parts]
    except ValueError:
        return None

    if any(v < 0 for v in values):
        return None

    if len(parts) == 1:
        return values[0]
    if len(parts) == 2:
        return values[0] * 60 + values[1]
    return values[0] * 3600 + values[1] * 60 + values[2]


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
    opts = {
        'quiet': True,
        'no_warnings': True,
        'progress_hooks': [progress_hook],
    }
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
    try:
        ydl_opts = get_basic_ydl_opts()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info
    except Exception as e:
        print(f"Error getting video info: {str(e)}")
        return None


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
    """Returns available subtitle info from yt-dlp info dict.

    Reads info['subtitles'] (manual) and info['automatic_captions'] (auto-generated).
    Priority: pl, en first, then alphabetically. Limited to 6 manual + 4 auto.

    Args:
        info: Video info dictionary from yt-dlp.

    Returns:
        dict with keys: 'manual', 'auto', 'has_any'.
    """
    if not info or not isinstance(info, dict):
        return {'manual': {}, 'auto': {}, 'has_any': False}

    priority_langs = ['pl', 'en']

    def sort_languages(langs_dict):
        if not langs_dict:
            return []
        priority = [l for l in priority_langs if l in langs_dict]
        rest = sorted(l for l in langs_dict if l not in priority_langs)
        return priority + rest

    manual_subs = info.get('subtitles') or {}
    auto_subs = info.get('automatic_captions') or {}

    manual_sorted = sort_languages(manual_subs)[:6]
    auto_sorted = sort_languages(auto_subs)[:4]

    manual = {lang: manual_subs[lang] for lang in manual_sorted}
    auto = {lang: auto_subs[lang] for lang in auto_sorted}

    return {
        'manual': manual,
        'auto': auto,
        'has_any': bool(manual or auto),
    }


def download_subtitles(url, lang, output_dir, auto=False, title=""):
    """Downloads subtitles via yt-dlp with skip_download=True.

    Args:
        url: YouTube video URL.
        lang: Subtitle language code (e.g. 'en', 'pl').
        output_dir: Directory to save subtitle file.
        auto: If True, download auto-generated captions.
        title: Video title for filename.

    Returns:
        Path to downloaded subtitle file, or None on error.
    """
    try:
        safe_title = sanitize_filename(title) if title else "subtitles"
        current_date = datetime.now().strftime("%Y-%m-%d")
        output_template = os.path.join(output_dir, f"{current_date} {safe_title}")

        ydl_opts = {
            'skip_download': True,
            'writesubtitles': not auto,
            'writeautomaticsub': auto,
            'subtitleslangs': [lang],
            'subtitlesformat': 'vtt/srt/best',
            'outtmpl': f"{output_template}.%(ext)s",
            'quiet': True,
            'no_warnings': True,
        }
        if os.path.exists(COOKIES_FILE):
            ydl_opts['cookiefile'] = COOKIES_FILE

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Find the downloaded subtitle file
        for ext in ['vtt', 'srt', 'ass', 'json3', 'srv1', 'srv2', 'srv3', 'lrc']:
            candidate = f"{output_template}.{lang}.{ext}"
            if os.path.exists(candidate):
                return candidate

        logging.warning(f"Subtitle file not found after download for lang={lang}, auto={auto}")
        return None

    except Exception as e:
        logging.error(f"Error downloading subtitles: {e}")
        return None


def parse_subtitle_file(file_path: str) -> str:
    """Parses VTT/SRT subtitle file to clean plain text.

    Removes WEBVTT header, timestamps, sequence numbers, HTML tags.
    Deduplicates consecutive identical lines (common in auto-captions).

    Args:
        file_path: Path to subtitle file (.vtt or .srt).

    Returns:
        Clean plain text string.
    """
    if not file_path or not os.path.exists(file_path):
        return ""

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    lines = content.split('\n')
    text_lines = []

    # Patterns to skip
    # VTT: 00:00:01.000 --> 00:00:04.000, SRT: 00:00:01,000 --> 00:00:04,000
    timestamp_pattern = re.compile(
        r'^\d{1,2}:\d{2}:\d{2}[.,]\d{2,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[.,]\d{2,3}'
    )
    sequence_pattern = re.compile(r'^\d+$')
    html_tag_pattern = re.compile(r'<[^>]+>')

    for line in lines:
        stripped = line.strip()

        # Skip empty, WEBVTT header, NOTE blocks, STYLE blocks
        if not stripped:
            continue
        if stripped.startswith('WEBVTT'):
            continue
        if stripped.startswith('NOTE'):
            continue
        if stripped.startswith('STYLE'):
            continue
        if stripped.startswith('Kind:') or stripped.startswith('Language:'):
            continue

        # Skip timestamps
        if timestamp_pattern.match(stripped):
            continue

        # Skip sequence numbers (SRT format)
        if sequence_pattern.match(stripped):
            continue

        # Remove HTML tags (<c>, <b>, <i>, etc.) and yt-dlp position tags
        cleaned = html_tag_pattern.sub('', stripped)
        cleaned = cleaned.strip()

        if not cleaned:
            continue

        # Deduplicate consecutive identical lines
        if text_lines and text_lines[-1] == cleaned:
            continue

        text_lines.append(cleaned)

    return '\n'.join(text_lines)


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
