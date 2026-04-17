"""
Spotify podcast episode resolution module.

Resolves Spotify episode URLs to downloadable audio by searching
iTunes API (direct MP3) or YouTube (via yt-dlp) as fallback.
Spotify Web API credentials are optional — used for richer metadata only.
"""

import logging
import os
import re
import time
from difflib import SequenceMatcher
from urllib.parse import urlparse, parse_qs

import requests
import yt_dlp

from bot.config import YTDLP_REMOTE_COMPONENTS, get_runtime_value


# Spotify API token cache
_spotify_token = None
_spotify_token_expires = 0


def parse_spotify_episode_url(url: str) -> str | None:
    """Extracts episode ID from a Spotify episode URL.

    Accepts:
      - https://open.spotify.com/episode/{ID}
      - https://open.spotify.com/episode/{ID}?si=...

    Returns episode ID string, or None if URL is not a valid episode link.
    """
    try:
        parsed = urlparse(url)
        if parsed.netloc.lower() not in ('open.spotify.com', 'www.open.spotify.com'):
            return None
        # Path: /episode/{ID}
        match = re.match(r'^/episode/([a-zA-Z0-9]+)', parsed.path)
        return match.group(1) if match else None
    except Exception:
        return None


def _get_spotify_token() -> str | None:
    """Gets Spotify API access token using client credentials flow.

    Returns access token string, or None if credentials are not configured.
    """
    global _spotify_token, _spotify_token_expires

    client_id = get_runtime_value('SPOTIFY_CLIENT_ID', '')
    client_secret = get_runtime_value('SPOTIFY_CLIENT_SECRET', '')
    if not client_id or not client_secret:
        return None

    # Return cached token if still valid
    if _spotify_token and time.time() < _spotify_token_expires - 60:
        return _spotify_token

    try:
        resp = requests.post(
            'https://accounts.spotify.com/api/token',
            data={'grant_type': 'client_credentials'},
            auth=(client_id, client_secret),
            timeout=10,
        )
        if resp.status_code != 200:
            logging.error("Spotify token request failed: %s", resp.status_code)
            return None

        data = resp.json()
        _spotify_token = data['access_token']
        _spotify_token_expires = time.time() + data.get('expires_in', 3600)
        return _spotify_token

    except Exception as e:
        logging.error("Error getting Spotify token: %s", e)
        return None


def get_spotify_episode_info(episode_id: str) -> dict | None:
    """Fetches episode metadata from Spotify Web API.

    Requires SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in config.
    Returns dict with title, show_name, duration_ms, description, or None.
    """
    token = _get_spotify_token()
    if not token:
        return None

    try:
        resp = requests.get(
            f'https://api.spotify.com/v1/episodes/{episode_id}',
            headers={'Authorization': f'Bearer {token}'},
            timeout=10,
        )
        if resp.status_code != 200:
            logging.error("Spotify API error: %s", resp.status_code)
            return None

        data = resp.json()
        return {
            'title': data.get('name', ''),
            'show_name': data.get('show', {}).get('name', ''),
            'duration_ms': data.get('duration_ms', 0),
            'description': data.get('description', ''),
            'release_date': data.get('release_date', ''),
            'language': data.get('language', ''),
        }

    except Exception as e:
        logging.error("Error fetching Spotify episode info: %s", e)
        return None


def _extract_title_from_url(url: str) -> str | None:
    """Extracts episode ID from Spotify URL for use in fallback messages.

    Spotify episode URLs use Base62 IDs (e.g. /episode/4rOoJ6Egrf8K2IrywzwOMk),
    not human-readable slugs — so this returns None (not useful as search query).
    The caller should use a generic fallback label instead.
    """
    # Spotify IDs are pure alphanumeric Base62, not readable titles
    # Return None to signal caller should use a generic label
    return None


def search_itunes_episode(title: str, show_name: str = "",
                          duration_sec: int | None = None) -> dict | None:
    """Searches iTunes API for a podcast episode with direct audio URL.

    Args:
        title: Episode title to search for.
        show_name: Podcast/show name (improves matching accuracy).
        duration_sec: Expected duration in seconds (for validation).

    Returns dict with audio_url, title, duration, show_name, or None.
    """
    try:
        query = f"{show_name} {title}".strip() if show_name else title
        resp = requests.get(
            'https://itunes.apple.com/search',
            params={
                'term': query,
                'entity': 'podcastEpisode',
                'limit': 10,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            logging.error("iTunes API error: %s", resp.status_code)
            return None

        results = resp.json().get('results', [])
        if not results:
            return None

        # Score each result for best match
        best_match = None
        best_score = 0.0

        for result in results:
            ep_title = result.get('trackName', '')
            ep_show = result.get('collectionName', '')
            ep_duration_ms = result.get('trackTimeMillis', 0)
            ep_audio_url = result.get('episodeUrl', '')

            if not ep_audio_url:
                continue

            # Title similarity (most important)
            title_score = SequenceMatcher(None, title.lower(), ep_title.lower()).ratio()

            # Show name similarity
            show_score = 0.0
            if show_name:
                show_score = SequenceMatcher(None, show_name.lower(), ep_show.lower()).ratio()

            # Duration match (bonus/penalty)
            duration_score = 0.0
            if duration_sec and ep_duration_ms:
                diff = abs(duration_sec - ep_duration_ms / 1000)
                if diff < 30:
                    duration_score = 1.0
                elif diff < 120:
                    duration_score = 0.5
                else:
                    duration_score = -0.3

            # Weighted score
            score = title_score * 0.5 + show_score * 0.3 + duration_score * 0.2

            if score > best_score:
                best_score = score
                best_match = {
                    'audio_url': ep_audio_url,
                    'title': ep_title,
                    'show_name': ep_show,
                    'duration': ep_duration_ms // 1000 if ep_duration_ms else None,
                    'score': score,
                }

        # Require minimum confidence
        if best_match and best_score >= 0.3:
            logging.info("iTunes match: '%s' (score: %.2f)", best_match['title'], best_score)
            return best_match

        return None

    except Exception as e:
        logging.error("Error searching iTunes: %s", e)
        return None


def search_youtube_episode(title: str, show_name: str = "",
                           duration_sec: int | None = None) -> dict | None:
    """Searches YouTube for a podcast episode via yt-dlp.

    Args:
        title: Episode title to search for.
        show_name: Podcast/show name.
        duration_sec: Expected duration in seconds.

    Returns dict with url, title, duration, channel, or None.
    """
    try:
        query = f"{show_name} {title}".strip() if show_name else title

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,
            'default_search': 'ytsearch5',
            'remote_components': YTDLP_REMOTE_COMPONENTS,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            results = ydl.extract_info(query, download=False)

        entries = results.get('entries', []) if results else []
        if not entries:
            return None

        best_match = None
        best_score = 0.0

        for entry in entries:
            if not entry:
                continue
            yt_title = entry.get('title', '')
            yt_channel = entry.get('channel', '') or entry.get('uploader', '')
            yt_duration = entry.get('duration')
            yt_url = entry.get('url') or f"https://www.youtube.com/watch?v={entry.get('id', '')}"

            # Title similarity
            title_score = SequenceMatcher(None, title.lower(), yt_title.lower()).ratio()

            # Channel/show match
            show_score = 0.0
            if show_name:
                show_score = SequenceMatcher(None, show_name.lower(), yt_channel.lower()).ratio()

            # Duration match
            duration_score = 0.0
            if duration_sec and yt_duration:
                diff = abs(duration_sec - yt_duration)
                if diff < 60:
                    duration_score = 1.0
                elif diff < 300:
                    duration_score = 0.5

            score = title_score * 0.5 + show_score * 0.3 + duration_score * 0.2

            if score > best_score:
                best_score = score
                best_match = {
                    'url': yt_url,
                    'title': yt_title,
                    'channel': yt_channel,
                    'duration': yt_duration,
                    'score': score,
                }

        if best_match and best_score >= 0.3:
            logging.info("YouTube match: '%s' by %s (score: %.2f)",
                        best_match['title'], best_match['channel'], best_score)
            return best_match

        return None

    except Exception as e:
        logging.error("Error searching YouTube: %s", e)
        return None


def resolve_spotify_episode(url: str) -> dict | None:
    """Resolves a Spotify episode URL to downloadable audio info.

    Pipeline:
    1. Parse episode ID from URL
    2. Fetch metadata from Spotify API (optional, needs credentials)
    3. Search iTunes API for direct MP3 (priority)
    4. Fallback: search YouTube via yt-dlp

    Returns dict with source, audio_url/youtube_url, title, duration, show_name,
    or None if episode cannot be resolved.
    """
    episode_id = parse_spotify_episode_url(url)
    if not episode_id:
        return None

    # Phase 1: Get metadata (optional)
    spotify_info = get_spotify_episode_info(episode_id)

    if spotify_info:
        title = spotify_info['title']
        show_name = spotify_info['show_name']
        duration_sec = spotify_info['duration_ms'] // 1000 if spotify_info['duration_ms'] else None
    else:
        # Without Spotify API credentials we cannot get episode metadata
        # (Spotify pages are SPA, no server-side rendering of titles).
        # Return a special marker so the caller can show a clear error.
        logging.warning("Cannot resolve Spotify episode without API credentials")
        return {
            'source': 'no_credentials',
            'episode_id': episode_id,
        }

    # Phase 2a: Search iTunes (priority — direct MP3 URL)
    itunes = search_itunes_episode(title, show_name, duration_sec)
    if itunes:
        return {
            'source': 'itunes',
            'audio_url': itunes['audio_url'],
            'title': itunes['title'],
            'show_name': itunes['show_name'],
            'duration': itunes['duration'],
            'spotify_title': title,
            'spotify_show': show_name,
        }

    # Phase 2b: Fallback to YouTube search
    youtube = search_youtube_episode(title, show_name, duration_sec)
    if youtube:
        return {
            'source': 'youtube',
            'youtube_url': youtube['url'],
            'title': youtube['title'],
            'channel': youtube['channel'],
            'duration': youtube['duration'],
            'spotify_title': title,
            'spotify_show': show_name,
        }

    return None


def download_direct_audio(audio_url: str, output_path: str) -> str | None:
    """Downloads audio file directly from URL (e.g. iTunes MP3).

    Args:
        audio_url: Direct URL to audio file.
        output_path: Path to save the file (without extension).

    Returns path to downloaded file, or None on error.
    """
    try:
        # Timeout: 15s for connection, no limit for reading (large podcast files)
        resp = requests.get(audio_url, stream=True, timeout=(15, None))
        resp.raise_for_status()

        # Determine extension from content type or URL
        content_type = resp.headers.get('Content-Type', '')
        if 'mpeg' in content_type or audio_url.endswith('.mp3'):
            ext = '.mp3'
        elif 'mp4' in content_type or 'm4a' in content_type:
            ext = '.m4a'
        else:
            ext = '.mp3'

        file_path = f"{output_path}{ext}"

        with open(file_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        logging.info("Downloaded %s (%.1f MB)", file_path, file_size_mb)
        return file_path

    except Exception as e:
        logging.error("Error downloading audio from %s: %s", audio_url, e)
        return None
