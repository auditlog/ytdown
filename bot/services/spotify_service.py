"""Spotify episode, music-track, and collection application service."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from typing import Any

import yt_dlp

from bot.config import COOKIES_FILE, YTDLP_JS_RUNTIMES, YTDLP_REMOTE_COMPONENTS
from bot.downloader_validation import sanitize_filename
from bot.spotify import (
    download_direct_audio,
    get_spotify_collection,
    resolve_spotify_episode,
    resolve_spotify_track,
    resolve_spotify_track_info,
)


def get_resolution_error_message(resolved: dict | None) -> str | None:
    """Map Spotify resolution outcomes to user-facing error messages."""

    if not resolved:
        return (
            "Nie udało się znaleźć tego odcinka podcastu.\n\n"
            "Możliwe przyczyny:\n"
            "- Odcinek jest dostępny wyłącznie na Spotify\n"
            "- Nieprawidłowy link do odcinka\n\n"
            "Spróbuj wyszukać ten podcast na YouTube lub innej platformie."
        )

    if resolved.get('source') == 'no_credentials':
        return (
            "Spotify wymaga skonfigurowania kluczy API.\n\n"
            "Dodaj do konfiguracji:\n"
            "- SPOTIFY_CLIENT_ID\n"
            "- SPOTIFY_CLIENT_SECRET\n\n"
            "Klucze uzyskasz na developer.spotify.com (utwórz aplikację z Web API)."
        )

    return None


def get_track_resolution_error_message(resolved: dict | None) -> str | None:
    """Map track metadata/search failures to a user-facing message."""

    source = resolved.get("source") if resolved else "not_found"
    if source == "no_credentials":
        return (
            "Spotify: nie udało się odczytać metadanych utworu.\n\n"
            "Skonfiguruj SPOTIFY_CLIENT_ID i SPOTIFY_CLIENT_SECRET albo "
            "dodaj aktualny plik spotify_cookies.txt."
        )
    if source in {"expired_session", "forbidden"}:
        return (
            "Spotify odmówił dostępu do tego utworu. Odśwież ciasteczka Spotify "
            "albo sprawdź uprawnienia aplikacji."
        )
    if source == "rate_limited":
        return "Spotify ograniczył liczbę zapytań. Spróbuj ponownie za chwilę."
    if source in {"network_error", "api_error", "api_changed"}:
        return "Nie udało się pobrać metadanych ze Spotify. Spróbuj ponownie później."
    if source == "not_found":
        title = resolved.get("title") if resolved else None
        suffix = f" dla „{title}”" if title else ""
        return f"Nie udało się znaleźć wiarygodnego dopasowania w YT Music{suffix}."
    return None


def get_collection_error_message(collection: dict | None) -> str | None:
    """Map Spotify album/playlist access failures to Polish UI text."""

    if collection is None:
        return "Nieprawidłowy link do albumu lub playlisty Spotify."
    reason = collection.get("error")
    if not reason:
        return None
    if reason == "no_credentials":
        return (
            "Spotify: brak dostępu do metadanych kolekcji.\n\n"
            "Administrator powinien połączyć konto poleceniem /spotify_login."
        )
    if reason == "forbidden":
        return (
            "Spotify odrzucił dostęp do tej playlisty. Same cookies nie nadają "
            "uprawnień Web API. Użyj /spotify_login i połącz konto właściciela "
            "lub współpracownika playlisty."
        )
    if reason == "expired_session":
        return "Autoryzacja Spotify wygasła. Użyj /spotify_login i spróbuj ponownie."
    if reason == "not_found":
        return "Nie znaleziono tego albumu lub playlisty Spotify."
    if reason == "rate_limited":
        return "Spotify ograniczył liczbę zapytań. Spróbuj ponownie za chwilę."
    return "Nie udało się pobrać listy utworów ze Spotify."


def build_episode_caption_data(resolved: dict) -> dict[str, str]:
    """Prepare lightweight UI metadata for Spotify episode selection screens."""

    title = resolved.get('title', 'Nieznany odcinek')
    show_name = resolved.get('show_name') or resolved.get('channel', '')
    duration = int(resolved.get('duration') or 0)
    duration_str = f"{duration // 60}:{duration % 60:02d}" if duration else "?"
    source_label = "iTunes" if resolved.get('source') == 'itunes' else "YouTube"

    return {
        'title': title,
        'show_name': show_name,
        'duration_str': duration_str,
        'source_label': source_label,
    }


async def resolve_episode(url: str, *, executor: Any | None = None) -> dict | None:
    """Resolve Spotify episode asynchronously for Telegram/UI flows."""

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, lambda: resolve_spotify_episode(url))


async def resolve_track(url: str, *, executor: Any | None = None) -> dict | None:
    """Resolve one Spotify track through preferred YT Music matching."""

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, lambda: resolve_spotify_track(url))


async def resolve_track_info(track: dict, *, executor: Any | None = None) -> dict | None:
    """Resolve already-loaded collection track metadata to YT Music."""

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        executor,
        lambda: resolve_spotify_track_info(track),
    )


async def load_collection(
    url: str,
    *,
    max_items: int,
    executor: Any | None = None,
) -> dict | None:
    """Load an album/playlist asynchronously for Telegram selection UI."""

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        executor,
        lambda: get_spotify_collection(url, max_items=max_items),
    )


async def download_resolved_audio(
    *,
    resolved: dict,
    audio_format: str,
    output_dir: str,
    executor: Any,
) -> str | None:
    """Download audio for an already resolved Spotify episode."""

    title = resolved.get('title', 'Spotify audio')
    artist = resolved.get('artist', '')
    output_title = f"{artist} - {title}" if artist else title
    sanitized_title = sanitize_filename(output_title)
    output_path = os.path.join(output_dir, sanitized_title)
    source = resolved['source']
    loop = asyncio.get_event_loop()

    if source == 'itunes':
        downloaded_file_path = await loop.run_in_executor(
            executor,
            lambda: download_direct_audio(resolved['audio_url'], output_path),
        )
        if (
            downloaded_file_path
            and audio_format != 'mp3'
            and audio_format in ('m4a', 'flac', 'wav', 'ogg', 'opus')
        ):
            converted_path = os.path.splitext(downloaded_file_path)[0] + f'.{audio_format}'
            try:
                result = await loop.run_in_executor(
                    executor,
                    lambda: subprocess.run(
                        ['ffmpeg', '-i', downloaded_file_path, '-y', converted_path],
                        capture_output=True,
                        timeout=180,
                    ),
                )
                if result.returncode == 0:
                    try:
                        os.remove(downloaded_file_path)
                    except OSError:
                        pass
                    downloaded_file_path = converted_path
                elif os.path.exists(converted_path):
                    try:
                        os.remove(converted_path)
                    except OSError:
                        pass
            except Exception as e:
                logging.warning("Format conversion failed, using original MP3: %s", e)
                try:
                    os.remove(converted_path)
                except OSError:
                    pass
        return downloaded_file_path

    if source in ('youtube', 'youtube_music'):
        youtube_url = resolved['youtube_url']
        youtube_dl_cls = yt_dlp.YoutubeDL
        ydl_opts = {
            'outtmpl': f"{output_path}.%(ext)s",
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'socket_timeout': 30,
            'retries': 3,
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': audio_format,
                'preferredquality': '192',
            }],
            'remote_components': YTDLP_REMOTE_COMPONENTS,
            'js_runtimes': YTDLP_JS_RUNTIMES,
        }
        if os.path.exists(COOKIES_FILE):
            ydl_opts['cookiefile'] = COOKIES_FILE

        await loop.run_in_executor(
            executor,
            lambda: youtube_dl_cls(ydl_opts).download([youtube_url]),
        )

        for file_name in os.listdir(output_dir):
            full_path = os.path.join(output_dir, file_name)
            if sanitized_title in file_name and os.path.isfile(full_path):
                return full_path

    return None
