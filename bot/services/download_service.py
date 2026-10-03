"""Download service shared by Telegram handlers and future entry points.

Error signaling contract
------------------------
- **prepare_download_plan** returns ``None`` when video metadata cannot be
  fetched (normal for invalid/expired URLs).  Raises ``ValueError`` when
  caller-supplied parameters are invalid (audio quality, format).
- **execute_download / execute_download_plan** raise ``FileNotFoundError``
  when yt-dlp finishes without producing a file.  Any yt-dlp runtime error
  propagates as-is.
- **estimate_download_size** returns ``None`` when size cannot be determined
  (normal — caller should treat as "unknown, allow download").
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable

import yt_dlp

from bot.config import YTDLP_JS_RUNTIMES, YTDLP_REMOTE_COMPONENTS
from bot.downloader_metadata import COOKIES_FILE, get_video_info
from bot.downloader_validation import is_valid_audio_quality, sanitize_filename
from bot.download_budget import DownloadBudget
from bot.security_limits import MAX_FILE_SIZE_MB

if TYPE_CHECKING:
    from bot.jobs import JobCancellation


ArtifactSuffixes = ('_transcript.md', '_transcript.txt', '_summary.md')

# Select resolution before codec compatibility; otherwise 1080p H.264 can beat
# 4K/8K VP9 or AV1. Prefer H.264 and m4a only when resolution is equal.
VIDEO_FORMAT_SORT = ['res', 'vcodec:h264', 'acodec:m4a', 'br', 'size']

# Download progress changes every second, but editing one Telegram message that
# often runs into flood control (RetryAfter), so progress edits are spaced out.
PROGRESS_EDIT_MIN_INTERVAL_SEC = 3.0


async def send_progress_update(status_callback: Callable[[str], Any], text: str) -> None:
    """Deliver a progress text; a failed edit is logged and never fatal.

    The status message is informational only. Flood control, a deleted message
    or a blocked bot must not abandon work whose worker thread keeps running.
    asyncio.CancelledError is not an Exception, so stops still propagate.
    """

    try:
        await status_callback(text)
    except Exception as exc:
        logging.warning("Progress update failed, work continues: %s", exc)


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
    info: dict[str, Any] | None = None,
    cookies_file: str | None = COOKIES_FILE,
) -> DownloadPlan | None:
    """Fetch metadata and build yt-dlp options for a media download.

    Returns None when video metadata cannot be fetched.
    Raises ValueError for invalid caller-supplied parameters.
    """

    if info is None:
        info = (
            get_video_info(url)
            if cookies_file == COOKIES_FILE
            else get_video_info(url, cookies_file=cookies_file)
        )
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
        'remote_components': YTDLP_REMOTE_COMPONENTS,
        'js_runtimes': YTDLP_JS_RUNTIMES,
    }
    if cookies_file and os.path.exists(cookies_file):
        ydl_opts['cookiefile'] = cookies_file

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
            ydl_opts['format'] = 'bestvideo*+bestaudio/best'
            ydl_opts['format_sort'] = VIDEO_FORMAT_SORT
            ydl_opts['format_sort_force'] = True
            ydl_opts['merge_output_format'] = 'mp4'
        elif format_choice == "medium":
            # Prefer up to 720p HD for smaller downloads.
            # Trailing /best fallback handles portrait formats (TikTok, Reels, Shorts)
            # whose `height` is the longer side and would otherwise fail height<=N.
            ydl_opts['format'] = (
                'bestvideo*[height<=720]+bestaudio'
                '/best[height<=720]'
                '/best'
            )
            ydl_opts['format_sort'] = VIDEO_FORMAT_SORT
            ydl_opts['format_sort_force'] = True
            ydl_opts['merge_output_format'] = 'mp4'
        elif format_choice in ["4320p", "2160p", "1440p", "1080p", "720p", "480p", "360p"]:
            height = format_choice.replace('p', '')
            ydl_opts['format'] = (
                f'bestvideo*[height<={height}]+bestaudio'
                f'/best[height<={height}]'
                f'/best'
            )
            ydl_opts['format_sort'] = VIDEO_FORMAT_SORT
            ydl_opts['format_sort_force'] = True
            ydl_opts['merge_output_format'] = 'mp4'
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
    """Estimate final download size in MB, adjusted for time ranges when available.

    Returns None when size cannot be determined (caller should allow download).
    """

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


def _build_cancellable_progress_hook(
    base_hook: Callable[[dict[str, Any]], None],
    cancellation: "JobCancellation",
) -> Callable[[dict[str, Any]], None]:
    """Wrap a yt-dlp progress hook so it raises DownloadError on cancel.

    yt-dlp invokes the hook synchronously many times per second; raising
    DownloadError lets it clean up the .part file and propagate the error
    out of the threadpool worker into ``execute_download``'s caller.
    """

    def hook(d: dict[str, Any]) -> None:
        if cancellation.event.is_set():
            raise yt_dlp.utils.DownloadError("cancelled by user")
        base_hook(d)

    return hook


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
    cancellation: "JobCancellation | None" = None,
    max_file_bytes: int | None = None,
    min_free_bytes: int = 2 * 1024**3,
) -> DownloadResult:
    """Run yt-dlp download and stream progress updates through a callback.

    When ``cancellation`` is provided, the progress hook raises
    yt_dlp.utils.DownloadError("cancelled by user") whenever the event is
    set. yt-dlp catches that and cleans up the .part file automatically.

    Raises FileNotFoundError when yt-dlp finishes without producing a file.
    """

    ydl_opts = plan.ydl_opts.copy()
    budget = None
    if max_file_bytes is not None:
        budget = DownloadBudget(plan.chat_download_path, max_file_bytes, min_free_bytes)
        budget.check_space()
        ydl_opts['max_filesize'] = max_file_bytes
        ydl_opts['postprocessor_hooks'] = [
            *ydl_opts.get('postprocessor_hooks', []), budget.postprocess,
        ]
    base_hook = progress_hook_factory(chat_id)
    if cancellation is not None:
        base_hook = _build_cancellable_progress_hook(base_hook, cancellation)
    ydl_opts['progress_hooks'] = [
        *ydl_opts.get('progress_hooks', []),
        *([budget.progress] if budget else []), base_hook,
    ]
    progress_state[chat_id] = {'status': 'starting', 'updated': time.time()}

    loop = asyncio.get_event_loop()
    future = loop.run_in_executor(
        executor,
        lambda: yt_dlp.YoutubeDL(ydl_opts).download([plan.url]),
    )

    last_update = ""
    last_edit_at: float | None = None
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

                now = time.monotonic()
                edit_due = last_edit_at is None or now - last_edit_at >= PROGRESS_EDIT_MIN_INTERVAL_SEC
                if status_text != last_update and edit_due:
                    last_update = status_text
                    last_edit_at = now
                    await send_progress_update(status_callback, status_text)

            await asyncio.sleep(1)

        await future
    finally:
        progress_state.pop(chat_id, None)

    downloaded_file_path = find_downloaded_file(plan)
    if not downloaded_file_path:
        raise FileNotFoundError("downloaded file not found")

    if budget:
        budget.check_result(downloaded_file_path)
    file_size_mb = os.path.getsize(downloaded_file_path) / (1024 * 1024)
    return DownloadResult(file_path=downloaded_file_path, file_size_mb=file_size_mb)


def ensure_size_within_limit(size_mb: float | None, *, max_size_mb: int = MAX_FILE_SIZE_MB) -> bool:
    """Return True when estimated size fits within the configured limit."""

    return size_mb is None or size_mb <= max_size_mb


def execute_download_plan(plan: DownloadPlan) -> DownloadResult:
    """Run a prepared yt-dlp download plan synchronously.

    Raises FileNotFoundError when yt-dlp finishes without producing a file.
    """

    yt_dlp.YoutubeDL(plan.ydl_opts).download([plan.url])

    downloaded_file_path = find_downloaded_file(plan)
    if not downloaded_file_path:
        raise FileNotFoundError("downloaded file not found")

    file_size_mb = os.path.getsize(downloaded_file_path) / (1024 * 1024)
    return DownloadResult(file_path=downloaded_file_path, file_size_mb=file_size_mb)


def find_downloaded_file(plan: DownloadPlan) -> str | None:
    """Find the resulting downloaded media file for a finished plan."""

    for file_name in os.listdir(plan.chat_download_path):
        full_path = os.path.join(plan.chat_download_path, file_name)
        if plan.sanitized_title in file_name and full_path.startswith(plan.output_path):
            if any(file_name.endswith(suffix) for suffix in ArtifactSuffixes):
                continue
            return full_path
    return None
