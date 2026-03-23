"""Video metadata helpers for yt-dlp-backed downloader flows."""

from __future__ import annotations

import logging
import os

import yt_dlp

from bot.config import COOKIES_FILE


def get_video_info(url: str, *, cookies_file: str | None = COOKIES_FILE) -> dict | None:
    """Fetch video information without downloading media."""

    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
        }
        if cookies_file and os.path.exists(cookies_file):
            ydl_opts['cookiefile'] = cookies_file

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        logging.error("Error getting video info for %s: %s", url, e)
        return None
