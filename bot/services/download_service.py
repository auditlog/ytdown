"""Download service shared by Telegram handlers and future entry points."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import yt_dlp

from bot.downloader import (
    COOKIES_FILE,
    get_video_info,
    is_valid_audio_quality,
    sanitize_filename,
)
from bot.security import MAX_FILE_SIZE_MB


ArtifactSuffixes = ('_transcript.md', '_transcript.txt', '_summary.md')


@dataclass
class DownloadPlan:
    """Prepared download configuration detached from Telegram handlers."""

    url: str
    media_type: str
    format_choice: str
    transcribe: bool
    use_format_id: bool
    audio_quality: str
    info: dict[str, Any]
    title: str
    duration: int
    duration_str: str
    sanitized_title: str
    output_path: str
    chat_download_path: str
    ydl_opts: dict[str, Any]
    time_range: dict[str, Any] | None


@dataclass
class DownloadResult:
    """Result of a completed yt-dlp download."""

    file_path: str
    file_size_mb: float


def prepare_download_plan(
    *,
    url: str,
    media_type: str,
    format_choice: str,
    chat_download_path: str,
    time_range: dict[str, Any] | None = None,
    transcribe: bool = False,
    use_format_id: bool = False,
    audio_quality: str = "192",
) -> DownloadPlan | None:
    """Fetch metadata and build yt-dlp options for a media download."""

    info = get_video_info(url)
    if not info:
        return None

    title = info.get('title', 'Nieznany tytuł')
    duration = int(info.get('duration') or 0)
    duration_str = f"{duration // 60}:{duration % 60:02d}" if duration else "?"
    sanitized_title = sanitize_filename(title)
    current_date = datetime.now().strftime("%Y-%m-%d")
    output_path = os.path.join(chat_download_path, f"{current_date} {sanitized_title}")

    ydl_opts: dict[str, Any] = {
        'outtmpl': f"{output_path}.%(ext)s",
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 30,
        'retries': 3,
        'fragment_retries': 3,
        'ignoreerrors': False,
        'concurrent_fragment_downloads': 4,
        'throttled_rate': '100K',
        'buffer_size': 1024 * 16,
        'http_chunk_size': 10485760,
    }
    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE

    if time_range:
        start = time_range.get('start', '0:00')
        end = time_range.get('end', duration_str)
        ydl_opts['download_ranges'] = lambda info, ydl: [{
            'start_time': time_range.get('start_sec', 0),
            'end_time': time_range.get('end_sec', duration),
        }]
        ydl_opts['force_keyframes_at_cuts'] = True
        logging.info("Applying time range: %s - %s", start, end)

    if media_type == "audio" or transcribe:
        if use_format_id and not transcribe:
            ydl_opts['format'] = format_choice
            ydl_opts['postprocessors'] = []
        else:
            audio_format_to_use = "mp3" if transcribe else format_choice
            normalized_quality = str(audio_quality).strip()
            if not is_valid_audio_quality(audio_format_to_use, normalized_quality):
                raise ValueError("invalid_audio_quality")

            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': audio_format_to_use,
                    'preferredquality': normalized_quality,
                }],
            })
    elif media_type == "video":
        if format_choice == "best":
            ydl_opts['format'] = 'best'
        elif format_choice in ["1080p", "720p", "480p", "360p"]:
            height = format_choice.replace('p', '')
            ydl_opts['format'] = (
                f'best[height<={height}]/bestvideo[height<={height}]'
                f'+bestaudio/best[height<={height}]'
            )
        else:
            ydl_opts['format'] = format_choice

    return DownloadPlan(
        url=url,
        media_type=media_type,
        format_choice=format_choice,
        transcribe=transcribe,
        use_format_id=use_format_id,
        audio_quality=str(audio_quality).strip(),
        info=info,
        title=title,
        duration=duration,
        duration_str=duration_str,
        sanitized_title=sanitized_title,
        output_path=output_path,
        chat_download_path=chat_download_path,
        ydl_opts=ydl_opts,
        time_range=time_range,
    )


def estimate_download_size(plan: DownloadPlan) -> float | None:
    """Estimate final download size in MB, adjusted for time ranges when available."""

    check_opts = plan.ydl_opts.copy()
    check_opts['simulate'] = True

    with yt_dlp.YoutubeDL(check_opts) as ydl:
        format_info = ydl.extract_info(plan.url, download=False)

    selected_format = None
    if 'requested_formats' in format_info:
        total_size = 0
        for fmt in format_info['requested_formats']:
            if fmt.get('filesize'):
                total_size += fmt['filesize']
        if total_size > 0:
            selected_format = {'filesize': total_size}
    elif 'filesize' in format_info:
        selected_format = format_info

    if not selected_format or not selected_format.get('filesize'):
        return None

    size_mb = selected_format['filesize'] / (1024 * 1024)
    if plan.time_range and plan.duration > 0:
        start_sec = plan.time_range.get('start_sec', 0)
        end_sec = plan.time_range.get('end_sec', plan.duration)
        range_duration = end_sec - start_sec
        if range_duration > 0:
            original_size_mb = size_mb
            size_mb = size_mb * (range_duration / plan.duration)
            logging.info(
                "Adjusted size estimate for time range: %.1f MB (original: %.1f MB)",
                size_mb,
                original_size_mb,
            )

    return size_mb


async def execute_download(
    plan: DownloadPlan,
    *,
    chat_id: int,
    executor: Any,
    progress_hook_factory: Callable[[int], Callable[[dict[str, Any]], None]],
    progress_state: dict[int, dict[str, Any]],
    status_callback: Callable[[str], Any],
    format_bytes: Callable[[int | float | None], str],
    format_eta: Callable[[int | float | None], str],
) -> DownloadResult:
    """Run yt-dlp download and stream progress updates through a callback."""

    ydl_opts = plan.ydl_opts.copy()
    ydl_opts['progress_hooks'] = [progress_hook_factory(chat_id)]
    progress_state[chat_id] = {'status': 'starting', 'updated': time.time()}

    loop = asyncio.get_event_loop()
    future = loop.run_in_executor(
        executor,
        lambda: yt_dlp.YoutubeDL(ydl_opts).download([plan.url]),
    )

    last_update = ""
    try:
        while not future.done():
            progress = progress_state.get(chat_id, {})
            if progress.get('status') == 'downloading':
                percent = progress.get('percent', '?%')
                downloaded = format_bytes(progress.get('downloaded', 0))
                total = format_bytes(progress.get('total', 0))
                speed = (
                    format_bytes(progress.get('speed', 0)) + "/s"
                    if progress.get('speed') else "?"
                )
                eta = format_eta(progress.get('eta'))

                status_text = (
                    f"Pobieranie: {percent}\n\n"
                    f"Pobrano: {downloaded} / {total}\n"
                    f"Prędkość: {speed}\n"
                    f"Pozostało: {eta}\n\n"
                    f"Czas trwania: {plan.duration_str}"
                )

                if status_text != last_update:
                    last_update = status_text
                    await status_callback(status_text)

            await asyncio.sleep(1)

        await future
    finally:
        progress_state.pop(chat_id, None)

    downloaded_file_path = find_downloaded_file(plan)
    if not downloaded_file_path:
        raise FileNotFoundError("downloaded file not found")

    file_size_mb = os.path.getsize(downloaded_file_path) / (1024 * 1024)
    return DownloadResult(file_path=downloaded_file_path, file_size_mb=file_size_mb)


def ensure_size_within_limit(size_mb: float | None, *, max_size_mb: int = MAX_FILE_SIZE_MB) -> bool:
    """Return True when estimated size fits within the configured limit."""

    return size_mb is None or size_mb <= max_size_mb


def find_downloaded_file(plan: DownloadPlan) -> str | None:
    """Find the resulting downloaded media file for a finished plan."""

    for file_name in os.listdir(plan.chat_download_path):
        full_path = os.path.join(plan.chat_download_path, file_name)
        if plan.sanitized_title in file_name and full_path.startswith(plan.output_path):
            if any(file_name.endswith(suffix) for suffix in ArtifactSuffixes):
                continue
            return full_path
    return None
