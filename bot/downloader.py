"""
Downloader module for YouTube Downloader Telegram Bot.

Handles video/audio downloading via yt-dlp.
"""

import logging
import os
import re
from datetime import datetime
from io import BytesIO

import requests
import yt_dlp
from bot.downloader_metadata import get_video_info as _get_video_info
from bot.downloader_playlist import (
    get_playlist_info,
    is_playlist_url,
    is_pure_playlist_url,
    strip_playlist_params,
)
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

COOKIES_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cookies.txt")

IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp'}


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
    Manual: pl, en, original lang first, then alphabetically (max 6).
    Auto: only pl, en, and original language (no random langs like aa/ab).

    Args:
        info: Video info dictionary from yt-dlp.

    Returns:
        dict with keys: 'manual', 'auto', 'has_any', 'original_lang'.
    """
    if not info or not isinstance(info, dict):
        return {'manual': {}, 'auto': {}, 'has_any': False,
                'original_lang': None}

    original_lang = info.get('language') or None
    priority_langs = ['pl', 'en']

    def sort_languages(langs_dict, limit=None):
        if not langs_dict:
            return []
        # Build ordered list: pl, en, original lang, then rest alphabetically
        seen = set()
        result = []
        for l in priority_langs:
            if l in langs_dict and l not in seen:
                result.append(l)
                seen.add(l)
        if original_lang and original_lang in langs_dict and original_lang not in seen:
            result.append(original_lang)
            seen.add(original_lang)
        rest = sorted(l for l in langs_dict if l not in seen)
        result.extend(rest)
        if limit:
            result = result[:limit]
        return result

    manual_subs = info.get('subtitles') or {}
    auto_subs = info.get('automatic_captions') or {}

    manual_sorted = sort_languages(manual_subs, limit=6)
    # Auto subs: only show pl, en, and original language — skip random langs
    auto_target = []
    for l in priority_langs:
        if l in auto_subs:
            auto_target.append(l)
    if original_lang and original_lang in auto_subs and original_lang not in auto_target:
        auto_target.append(original_lang)

    manual = {lang: manual_subs[lang] for lang in manual_sorted}
    auto = {lang: auto_subs[lang] for lang in auto_target}

    return {
        'manual': manual,
        'auto': auto,
        'has_any': bool(manual or auto),
        'original_lang': original_lang,
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

def _load_instagram_cookies():
    """Loads Instagram session cookies from the cookies file.

    Returns:
        dict: Cookie name-value pairs, or empty dict on error.
    """
    cookies = {}
    if not os.path.exists(COOKIES_FILE):
        return cookies
    try:
        with open(COOKIES_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    parts = line.split('\t')
                    if len(parts) >= 7 and 'instagram' in parts[0]:
                        cookies[parts[5]] = parts[6]
    except Exception as e:
        logging.error("Error loading Instagram cookies: %s", e)
    return cookies


def _get_instaloader_context():
    """Creates an instaloader context with session cookies.

    Returns:
        instaloader.Instaloader or None: Configured instance, or None on error.
    """
    try:
        import instaloader
    except ImportError:
        logging.error("instaloader not installed")
        return None

    cookies = _load_instagram_cookies()
    session_id = cookies.get('sessionid', '')
    if not session_id:
        logging.warning("No Instagram sessionid in cookies")
        return None

    loader = instaloader.Instaloader(max_connection_attempts=1)
    for name in ('sessionid', 'ds_user_id', 'csrftoken', 'ig_did', 'mid', 'rur'):
        if cookies.get(name):
            loader.context._session.cookies.set(name, cookies[name], domain='.instagram.com')
    loader.context.username = cookies.get('ds_user_id', '')
    return loader


def get_instagram_post_info(url):
    """Fetches full Instagram post info using instaloader.

    Returns a dict compatible with the rest of the bot code:
    - For carousels: {'_type': 'playlist', 'entries': [...], 'title': ...}
    - For single photos: {'ext': 'jpg', 'url': ..., 'title': ...}
    - For videos: falls through to yt-dlp (returns None)

    Args:
        url: Instagram post URL.

    Returns:
        dict or None: Post info dictionary or None on error.
    """
    # Extract shortcode from URL
    match = re.search(r'/(?:p|reel)/([A-Za-z0-9_-]+)', url)
    if not match:
        return None
    shortcode = match.group(1)

    loader = _get_instaloader_context()
    if not loader:
        return _get_instagram_post_info_ytdlp(url)

    try:
        import instaloader
        post = instaloader.Post.from_shortcode(loader.context, shortcode)
        title = f"Post by {post.owner_username}"

        if post.typename == 'GraphSidecar':
            entries = []
            for node in post.get_sidecar_nodes():
                entry = {
                    'title': title,
                    'url': node.display_url,
                    'is_video': node.is_video,
                }
                if node.is_video:
                    entry['video_url'] = node.video_url
                else:
                    entry['ext'] = 'jpg'
                entries.append(entry)
            return {
                '_type': 'playlist',
                'entries': entries,
                'title': title,
                'playlist_count': len(entries),
            }

        if not post.is_video:
            return {
                'ext': 'jpg',
                'url': post.url,
                'title': title,
            }

        # Video post — let yt-dlp handle it
        return None

    except Exception as e:
        logging.error("instaloader error for %s: %s", url, e)
        # Fallback to yt-dlp only for photo detection;
        # if yt-dlp returns video data, return None to let standard flow handle it
        info = _get_instagram_post_info_ytdlp(url)
        if info and (info.get('formats') or info.get('duration')):
            return None  # Video — let yt-dlp standard flow handle it
        return info


def _get_instagram_post_info_ytdlp(url):
    """Fallback: fetches Instagram post info via yt-dlp."""
    try:
        ydl_opts = get_basic_ydl_opts()
        ydl_opts['ignore_no_formats_error'] = True
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        logging.error("Error getting Instagram post info for %s: %s", url, e)
        return None


def is_photo_entry(info: dict) -> bool:
    """Returns True if an info dict represents a photo (not video).

    Args:
        info: Single entry info dict.

    Returns:
        True if the entry is a photo.
    """
    if not info:
        return False
    # Explicit is_video flag from instaloader
    if 'is_video' in info:
        return not info['is_video']
    ext = (info.get('ext') or '').lower()
    if ext in IMAGE_EXTENSIONS:
        return True
    # No formats and no duration — likely a photo
    if not info.get('formats') and not info.get('duration'):
        url = info.get('url', '')
        return any(url.lower().endswith(f'.{e}') for e in IMAGE_EXTENSIONS)
    return False


def download_photo(url: str, output_path: str) -> str | None:
    """Downloads a photo from direct URL.

    Args:
        url: Direct image URL.
        output_path: File path without extension.

    Returns:
        Path to downloaded file, or None on error.
    """
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()

        content_type = resp.headers.get('content-type', '')
        if 'png' in content_type:
            ext = 'png'
        elif 'webp' in content_type:
            ext = 'webp'
        else:
            ext = 'jpg'

        file_path = f"{output_path}.{ext}"
        with open(file_path, 'wb') as f:
            f.write(resp.content)
        return file_path
    except Exception as e:
        logging.error("Error downloading photo: %s", e)
        return None


def download_thumbnail(info: dict, output_dir: str, embed: bool = False) -> str | None:
    """Downloads video thumbnail from yt-dlp info dict.

    For embed=True, converts to JPEG and scales to max 320x320
    (Telegram Bot API thumbnail requirement).
    For embed=False, downloads full-resolution image.

    Args:
        info: yt-dlp info dictionary containing 'thumbnail' or 'thumbnails'.
        output_dir: Directory to save the thumbnail file.
        embed: If True, resize for Telegram embed (max 320x320 JPEG).

    Returns:
        Path to downloaded thumbnail file, or None on error.
    """
    # Pick best thumbnail URL
    thumb_url = None
    thumbnails = info.get('thumbnails') or []
    if thumbnails:
        # yt-dlp lists thumbnails in ascending quality — last is best
        thumb_url = thumbnails[-1].get('url')
    if not thumb_url:
        thumb_url = info.get('thumbnail')
    if not thumb_url:
        return None

    try:
        from PIL import Image

        resp = requests.get(thumb_url, timeout=15)
        resp.raise_for_status()

        img = Image.open(BytesIO(resp.content))

        if embed:
            # Telegram requires JPEG, max 320x320
            img.thumbnail((320, 320), Image.LANCZOS)
            suffix = "_thumb_embed.jpg"
        else:
            suffix = "_thumb_full.jpg"

        # Convert to RGB (handles PNG with alpha, WebP, etc.)
        if img.mode != 'RGB':
            img = img.convert('RGB')

        safe_title = sanitize_filename(info.get('title', 'thumbnail'))
        thumb_path = os.path.join(output_dir, f"{safe_title}{suffix}")
        img.save(thumb_path, 'JPEG', quality=85)
        return thumb_path

    except Exception as e:
        logging.warning("Failed to download thumbnail: %s", e)
        return None


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
