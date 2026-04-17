"""Validation and parsing helpers for downloader flows."""

from __future__ import annotations

import re

FORMAT_ID_PATTERN = re.compile(
    r"^(?:best|worst|bestvideo|bestaudio|worstaudio|worstvideo|medium)$|^(?:\d+[pP]?)$|^(?:\d+(?:[+x]\d+){0,3})$|^(?:dash-[\da-zA-Z]+)$|^(?:[\da-zA-Z]+-\d+)$"
)
SUPPORTED_AUDIO_FORMATS = ("mp3", "m4a", "wav", "flac", "ogg", "opus")
AUDIO_FORMATS = set(SUPPORTED_AUDIO_FORMATS)

QUALITY_RANGE_BY_CODEC = {
    "mp3": (0, 330),
    "opus": (0, 9),
    "vorbis": (0, 9),
    "ogg": (0, 9),
}


def sanitize_filename(filename):
    """Return a filesystem-safe filename."""

    invalid_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
    for char in invalid_chars:
        filename = filename.replace(char, '-')
    filename = filename.replace('..', '')
    filename = ''.join(c for c in filename if c.isprintable())
    if len(filename) > 200:
        filename = filename[:200]
    if not filename.strip():
        filename = "download"
    return filename.strip()


def is_valid_ytdlp_format_id(format_id):
    """Return True if format ID is safe and supported by CLI/TG UI flows."""

    if not isinstance(format_id, str):
        return False
    normalized = format_id.strip().lower()
    return bool(FORMAT_ID_PATTERN.fullmatch(normalized))


def is_valid_audio_format(audio_format):
    """Return True for allowed audio conversion formats."""

    if not isinstance(audio_format, str):
        return False
    return audio_format.strip().lower() in AUDIO_FORMATS


def is_valid_audio_quality(audio_format, audio_quality):
    """Return True when audio quality is supported for selected codec."""

    if not isinstance(audio_format, str):
        return False

    normalized_format = audio_format.strip().lower()
    if normalized_format not in SUPPORTED_AUDIO_FORMATS:
        return False

    if isinstance(audio_quality, bool):
        return False
    try:
        normalized_quality = int(str(audio_quality).strip())
    except (TypeError, ValueError):
        return False

    if normalized_quality < 0:
        return False

    quality_range = QUALITY_RANGE_BY_CODEC.get(normalized_format)
    if quality_range is None:
        return True

    min_quality, max_quality = quality_range
    return min_quality <= normalized_quality <= max_quality


def normalize_format_id(format_id, *, default="best"):
    """Normalize shortcut or legacy format aliases."""

    if format_id is None:
        return None

    normalized = format_id.strip().lower()
    if normalized == "auto":
        return default
    return normalized


def parse_time_seconds(time_value):
    """Convert HH:MM:SS, MM:SS, or seconds input into integer seconds."""

    if time_value is None:
        return None

    if isinstance(time_value, bool):
        return None
    if isinstance(time_value, int):
        if time_value < 0:
            return None
        return time_value
    if isinstance(time_value, float):
        if time_value < 0:
            return None
        return int(time_value)

    if not isinstance(time_value, str):
        return None

    time_str = time_value.strip()
    if not time_str:
        return None

    parts = time_str.split(':')
    if len(parts) not in {1, 2, 3}:
        return None

    try:
        values = [int(part) for part in parts]
    except ValueError:
        return None

    if any(v < 0 for v in values):
        return None

    if len(parts) == 1:
        return values[0]
    if len(parts) == 2:
        return values[0] * 60 + values[1]
    return values[0] * 3600 + values[1] * 60 + values[2]
