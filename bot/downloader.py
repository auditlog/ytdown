"""
Downloader module for YouTube Downloader Telegram Bot.

Handles video/audio downloading via yt-dlp.
"""

import logging
from datetime import datetime

import yt_dlp


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
            print(f"[DEBUG] Configuring audio-only download ({audio_format})")
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': audio_format,
                    'preferredquality': audio_quality,
                }],
            })
        elif format_id:
            ydl_opts['format'] = format_id
            print(f"[DEBUG] Set format: {format_id}")
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
    if not url.startswith(('https://www.youtube.com/', 'https://youtu.be/')):
        print("Error: Invalid URL. Provide a YouTube video link.")
        return False
    return True
