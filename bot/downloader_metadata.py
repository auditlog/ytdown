"""Video metadata helpers for yt-dlp-backed downloader flows."""

from __future__ import annotations

import logging
import os

import yt_dlp

from bot.config import COOKIES_FILE, YTDLP_JS_RUNTIMES, YTDLP_REMOTE_COMPONENTS


def get_video_info_with_error(
    url: str, *, cookies_file: str | None = COOKIES_FILE
) -> tuple[dict | None, str | None]:
    """Fetch video information, returning (info, raw yt-dlp error text).

    Callers that can surface a reason to the user should prefer this over
    get_video_info(); pass the error text through bot.download_errors to turn it
    into something actionable instead of a generic failure notice.
    """

    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'remote_components': YTDLP_REMOTE_COMPONENTS,
            'js_runtimes': YTDLP_JS_RUNTIMES,
        }
        if cookies_file and os.path.exists(cookies_file):
            ydl_opts['cookiefile'] = cookies_file

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False), None
    except Exception as e:
        logging.error("Error getting video info for %s: %s", url, e)
        return None, str(e)


def get_video_info(url: str, *, cookies_file: str | None = COOKIES_FILE) -> dict | None:
    """Fetch video information without downloading media."""

    info, _ = get_video_info_with_error(url, cookies_file=cookies_file)
    return info
