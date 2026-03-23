"""Spotify application service built on top of low-level spotify helpers."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from typing import Any

import yt_dlp

from bot.downloader_validation import sanitize_filename
from bot.spotify import download_direct_audio, resolve_spotify_episode


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


async def download_resolved_audio(
    *,
    resolved: dict,
    audio_format: str,
    output_dir: str,
    executor: Any,
) -> str | None:
    """Download audio for an already resolved Spotify episode."""

    title = resolved.get('title', 'Podcast episode')
    sanitized_title = sanitize_filename(title)
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

    if source == 'youtube':
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
        }

        await loop.run_in_executor(
            executor,
            lambda: youtube_dl_cls(ydl_opts).download([youtube_url]),
        )

        for file_name in os.listdir(output_dir):
            full_path = os.path.join(output_dir, file_name)
            if sanitized_title in file_name and os.path.isfile(full_path):
                return full_path

    return None
