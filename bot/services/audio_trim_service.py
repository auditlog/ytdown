"""Cut audio fragments with ffmpeg stream copy (no re-encoding, except FLAC).

Precision is one audio frame (~25 ms for MP3). Verified on 2026-10-01 against
a 20-minute VBR MP3 encoded like yt-dlp's output (libmp3lame -q:a 5): input
seeking (-ss before -i) landed within 25 ms of the requested time.

FLAC fragments are re-encoded with the lossless FLAC encoder (see cut_fragment).

See also: bot/handlers/trim_callbacks.py (caller), bot/handlers/time_range.py.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from bot.downloader_validation import sanitize_filename
from bot.handlers.time_range import ResolvedRange, format_timestamp
from bot.security_limits import FFMPEG_TIMEOUT

_MAX_STEM_BYTES = 200


class AudioTrimError(RuntimeError):
    """ffmpeg/ffprobe failure; the message is technical and meant for logs."""


def _tail(stderr: bytes) -> str:
    return stderr.decode(errors="replace")[-500:]


def _is_cancelled(cancellation) -> bool:
    return cancellation is not None and cancellation.event.is_set()


async def _run(
    cmd: list[str],
    *,
    cancellation=None,
    timeout: int = FFMPEG_TIMEOUT,
) -> tuple[int, bytes, bytes]:
    """Run a subprocess and expose it to /stop through ``cancellation.process``."""

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise AudioTrimError(f"{cmd[0]} unavailable: {exc}") from exc

    if cancellation is not None:
        cancellation.process = process
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.wait()
        raise AudioTrimError(f"{cmd[0]} timed out after {timeout}s") from exc
    finally:
        if cancellation is not None and cancellation.process is process:
            cancellation.process = None
    return process.returncode, stdout, stderr


async def probe_duration(path: Path) -> float:
    """Return the media duration in seconds as reported by ffprobe."""

    code, stdout, stderr = await _run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
    )
    name = Path(path).name
    if code != 0:
        raise AudioTrimError(f"ffprobe failed for {name}: {_tail(stderr)}")
    try:
        duration = float(stdout.decode().strip())
    except ValueError as exc:
        raise AudioTrimError(f"ffprobe returned no duration for {name}") from exc
    if duration <= 0:
        raise AudioTrimError(f"ffprobe returned a non-positive duration for {name}")
    return duration


def fragment_label(fragment: ResolvedRange) -> str:
    """Human label used in captions, tags and status messages, e.g. 12:00–15:30."""

    return f"{format_timestamp(fragment.start_sec)}–{format_timestamp(fragment.end_sec)}"


def fragment_filename(title: str, fragment: ResolvedRange, ext: str) -> str:
    """Filesystem-safe fragment name with a stem of at most 200 UTF-8 bytes.

    sanitize_filename caps the stem at 200 characters, but ext4 limits a name
    to 255 bytes, so a long Cyrillic, CJK or emoji title would make ffmpeg fail
    with ENAMETOOLONG. A long title may lose the range label here; the label
    stays in the audio title tag (see run_trim_job in bot/handlers/trim_callbacks.py).
    """

    stem = sanitize_filename(f"{title} [{fragment_label(fragment)}]")
    # errors="ignore" drops a character split by the byte cut.
    stem = stem.encode("utf-8")[:_MAX_STEM_BYTES].decode("utf-8", errors="ignore").strip()
    return stem + ext


async def cut_fragment(
    source: Path,
    fragment: ResolvedRange,
    dest: Path,
    *,
    title_tag: str,
    cancellation=None,
) -> Path:
    """Copy one fragment of ``source`` into ``dest`` with stream copy.

    MP3 and M4A are never re-encoded. FLAC is re-encoded losslessly so the
    fragment gets a correct duration header (see the module docstring).
    """

    head = ["ffmpeg", "-v", "error", "-y", "-ss", str(fragment.start_sec), "-i", str(source)]
    if not fragment.open_end:
        head += ["-t", str(fragment.end_sec - fragment.start_sec)]
    codec_args = ["-c", "copy"]
    if Path(dest).suffix.lower() == ".flac":
        # A stream-copied FLAC keeps the source STREAMINFO, so the cut file would
        # report the full source duration. Re-encoding FLAC is lossless and
        # rewrites a correct header.
        codec_args += ["-c:a", "flac"]
    tail = codec_args + ["-map_metadata", "0", "-metadata", f"title={title_tag}", str(dest)]

    # Keep embedded cover art when present. Some containers (M4A) reject a
    # copied cover stream, so retry once with the audio stream only.
    code, _, stderr = await _run(
        head + ["-map", "0:a:0", "-map", "0:v:0?"] + tail,
        cancellation=cancellation,
    )
    if code != 0 and not _is_cancelled(cancellation):
        logging.warning("ffmpeg cut with cover failed, retrying audio-only: %s", _tail(stderr))
        code, _, stderr = await _run(head + ["-map", "0:a:0"] + tail, cancellation=cancellation)
    if code != 0:
        raise AudioTrimError(f"ffmpeg cut failed: {_tail(stderr)}")
    return dest
