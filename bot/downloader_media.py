"""Media helper utilities for thumbnails, Instagram, and direct photo downloads."""

from __future__ import annotations

import logging
import os
import re
from io import BytesIO

import requests
import yt_dlp

from bot.config import COOKIES_FILE
from bot.downloader_validation import sanitize_filename

IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp'}


def _load_instagram_cookies(*, cookies_file: str | None = COOKIES_FILE):
    """Load Instagram session cookies from the cookies file."""

    cookies = {}
    if not cookies_file or not os.path.exists(cookies_file):
        return cookies
    try:
        with open(cookies_file) as file_obj:
            for line in file_obj:
                line = line.strip()
                if line and not line.startswith('#'):
                    parts = line.split('\t')
                    if len(parts) >= 7 and 'instagram' in parts[0]:
                        cookies[parts[5]] = parts[6]
    except Exception as e:
        logging.error("Error loading Instagram cookies: %s", e)
    return cookies


def _get_instaloader_context(*, cookies_file: str | None = COOKIES_FILE):
    """Create an instaloader context with session cookies."""

    try:
        import instaloader
    except ImportError:
        logging.error("instaloader not installed")
        return None

    cookies = _load_instagram_cookies(cookies_file=cookies_file)
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


def _get_instagram_post_info_ytdlp(url: str, *, cookies_file: str | None = COOKIES_FILE):
    """Fallback: fetch Instagram post info via yt-dlp."""

    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'ignore_no_formats_error': True,
        }
        if cookies_file and os.path.exists(cookies_file):
            ydl_opts['cookiefile'] = cookies_file
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        logging.error("Error getting Instagram post info for %s: %s", url, e)
        return None


def get_instagram_post_info(url: str, *, cookies_file: str | None = COOKIES_FILE):
    """Fetch full Instagram post info using instaloader with yt-dlp fallback."""

    match = re.search(r'/(?:p|reel)/([A-Za-z0-9_-]+)', url)
    if not match:
        return None
    shortcode = match.group(1)

    loader = _get_instaloader_context(cookies_file=cookies_file)
    if not loader:
        return _get_instagram_post_info_ytdlp(url, cookies_file=cookies_file)

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

        return None
    except Exception as e:
        logging.error("instaloader error for %s: %s", url, e)
        info = _get_instagram_post_info_ytdlp(url, cookies_file=cookies_file)
        if info and (info.get('formats') or info.get('duration')):
            return None
        return info


def is_photo_entry(info: dict) -> bool:
    """Return True if an info dict represents a photo instead of video."""

    if not info:
        return False
    if 'is_video' in info:
        return not info['is_video']
    ext = (info.get('ext') or '').lower()
    if ext in IMAGE_EXTENSIONS:
        return True
    if not info.get('formats') and not info.get('duration'):
        url = info.get('url', '')
        return any(url.lower().endswith(f'.{ext}') for ext in IMAGE_EXTENSIONS)
    return False


def download_photo(url: str, output_path: str) -> str | None:
    """Download a photo from direct URL."""

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
        with open(file_path, 'wb') as file_obj:
            file_obj.write(resp.content)
        return file_path
    except Exception as e:
        logging.error("Error downloading photo: %s", e)
        return None


def download_thumbnail(info: dict, output_dir: str, embed: bool = False) -> str | None:
    """Download video thumbnail from yt-dlp info dict."""

    thumb_url = None
    thumbnails = info.get('thumbnails') or []
    if thumbnails:
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
            img.thumbnail((320, 320), Image.LANCZOS)
            suffix = "_thumb_embed.jpg"
        else:
            suffix = "_thumb_full.jpg"

        if img.mode != 'RGB':
            img = img.convert('RGB')

        safe_title = sanitize_filename(info.get('title', 'thumbnail'))
        thumb_path = os.path.join(output_dir, f"{safe_title}{suffix}")
        img.save(thumb_path, 'JPEG', quality=85)
        return thumb_path
    except Exception as e:
        logging.warning("Failed to download thumbnail: %s", e)
        return None
