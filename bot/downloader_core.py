"""Core yt-dlp download execution for standalone (CLI) use.

This module contains the original download_youtube_video function and its
helpers (progress_hook, get_basic_ydl_opts).  These are used by the CLI
entry point and tests but are NOT used by Telegram handlers, which go
through bot.services.download_service instead.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

import yt_dlp

from bot.config import COOKIES_FILE, YTDLP_REMOTE_COMPONENTS
from bot.downloader_validation import (
    is_valid_audio_format,
    is_valid_audio_quality,
    is_valid_ytdlp_format_id,
    normalize_format_id,
    parse_time_seconds,
)


def progress_hook(d):
    """Progress hook called by yt-dlp to track download progress."""

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
    """Return basic yt-dlp configuration dict."""

    opts = {
        'quiet': True,
        'no_warnings': True,
        'remote_components': YTDLP_REMOTE_COMPONENTS,
    }
    if include_progress_hooks:
        opts['progress_hooks'] = [progress_hook]
    if os.path.exists(COOKIES_FILE):
        opts['cookiefile'] = COOKIES_FILE
    return opts


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
    """Download YouTube video or audio.

    Returns True on success, False on error.
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
            'remote_components': YTDLP_REMOTE_COMPONENTS,
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
