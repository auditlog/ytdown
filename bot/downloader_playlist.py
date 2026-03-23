"""Playlist-specific downloader helpers."""

from __future__ import annotations

import logging
import os
from urllib.parse import parse_qs, urlparse

import yt_dlp

COOKIES_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "cookies.txt",
)


def is_playlist_url(url: str) -> bool:
    """Detect if URL contains a YouTube playlist parameter."""

    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        hostname = (parsed.hostname or '').lower()
        youtube_hosts = {
            'youtube.com',
            'www.youtube.com',
            'm.youtube.com',
            'music.youtube.com',
            'youtu.be',
        }
        if hostname not in youtube_hosts:
            return False

        return 'list' in params
    except Exception:
        return False


def is_pure_playlist_url(url: str) -> bool:
    """Return True if URL is a pure playlist without a selected video."""

    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        return 'list' in params and 'v' not in params and parsed.path in ('/', '/playlist')
    except Exception:
        return False


def strip_playlist_params(url: str) -> str:
    """Remove playlist parameters from URL while keeping the video selection."""

    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query, keep_blank_values=True)
        params.pop('list', None)
        params.pop('index', None)
        new_query = '&'.join(f'{k}={v[0]}' for k, v in params.items() if v)
        return parsed._replace(query=new_query).geturl()
    except Exception:
        return url


def get_playlist_info(url: str, max_items: int = 10) -> dict | None:
    """Fetch playlist metadata using flat yt-dlp extraction."""

    try:
        ydl_opts = {
            'extract_flat': 'in_playlist',
            'quiet': True,
            'no_warnings': True,
            'playlistend': max_items,
        }
        if os.path.exists(COOKIES_FILE):
            ydl_opts['cookiefile'] = COOKIES_FILE

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info or info.get('_type') != 'playlist':
            return None

        entries = []
        for entry in (info.get('entries') or []):
            if entry is None:
                continue
            video_id = entry.get('id', '')
            entries.append({
                'url': f"https://www.youtube.com/watch?v={video_id}" if video_id else entry.get('url', ''),
                'title': entry.get('title', 'Nieznany tytuł'),
                'duration': entry.get('duration'),
                'id': video_id,
            })

        return {
            'title': info.get('title', 'Playlista'),
            'playlist_count': info.get('playlist_count') or len(entries),
            'entries': entries,
        }
    except Exception as e:
        logging.error("Error getting playlist info: %s", e)
        return None
