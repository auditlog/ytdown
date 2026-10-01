"""Shared parsing helpers for chat-provided time ranges.

Used by the pre-download range flow (bot/handlers/inbound_media.py) and the
audio trimming flow (bot/handlers/trim_callbacks.py). Messages carried by
TimeRangeError are user-facing Polish text sent without parse_mode, because
they echo user input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bot.security_limits import TRIM_MAX_RANGES

# S, M:SS (minutes unbounded, e.g. 102:30) or H:MM:SS.
_TIMESTAMP_RE = re.compile(r"^\d+(?::\d{2}){0,2}$")
# Phones often autocorrect "-" into an en or em dash.
_DASH_RE = re.compile(r"\s*[-–—]\s*")
_SEPARATOR_RE = re.compile(r"[,;\n]")
_RANGE_LIKE_RE = re.compile(r"^[\d:\s,;\-–—]+$")

EXAMPLES = "Przykłady: 1:30-4:45 · 2:15- · -5:00 · 1:00-2:00, 5:30-7:00"


@dataclass(frozen=True)
class RangeSpec:
    """One parsed range; None marks an open end."""

    start_sec: int | None  # None = from the beginning
    end_sec: int | None  # None = to the end


@dataclass(frozen=True)
class ResolvedRange:
    """A range validated against a concrete duration."""

    start_sec: int
    end_sec: int  # resolved against the duration; used for labels and checks
    open_end: bool  # True for "2:15-": ffmpeg cuts to EOF without -t


class TimeRangeError(ValueError):
    """Invalid range input; str(exc) is a Polish, user-facing message."""


def format_timestamp(seconds: int) -> str:
    """Format seconds as M:SS below one hour and H:MM:SS above."""

    if seconds >= 3600:
        return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


def range_to_session_dict(start_sec: int, end_sec: int) -> dict:
    """Build the session shape consumed by the downloader (yt-dlp sections)."""

    return {
        "start": format_timestamp(start_sec),
        "end": format_timestamp(end_sec),
        "start_sec": start_sec,
        "end_sec": end_sec,
    }


def looks_like_time_ranges(text: str) -> bool:
    """Cheap pre-check so plain chat text and 8-digit PINs keep their old handling."""

    stripped = (text or "").strip()
    return (
        bool(_RANGE_LIKE_RE.match(stripped))
        and any(dash in stripped for dash in "-–—")
        and any(char.isdigit() for char in stripped)
    )


def _parse_timestamp(value: str, fragment: str) -> int:
    if not _TIMESTAMP_RE.match(value):
        raise TimeRangeError(f"Nie rozumiem zakresu „{fragment}”. {EXAMPLES}")
    parts = [int(part) for part in value.split(":")]
    # The first component is unbounded ("90" seconds, "102:30" minutes);
    # every later one is minutes or seconds and must stay below 60.
    if any(part >= 60 for part in parts[1:]):
        raise TimeRangeError(
            f"„{value}” nie jest poprawnym czasem — minuty i sekundy muszą być mniejsze niż 60."
        )
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def parse_time_ranges(text: str, *, max_ranges: int = TRIM_MAX_RANGES) -> list[RangeSpec]:
    """Parse comma/semicolon/newline separated ranges; raise TimeRangeError on bad input."""

    fragments = [part.strip() for part in _SEPARATOR_RE.split(text or "")]
    fragments = [part for part in fragments if part]
    if not fragments:
        raise TimeRangeError(f"Podaj zakres. {EXAMPLES}")
    if len(fragments) > max_ranges:
        raise TimeRangeError(
            f"Możesz podać maksymalnie {max_ranges} fragmentów naraz (podano {len(fragments)})."
        )

    specs: list[RangeSpec] = []
    for fragment in fragments:
        bounds = _DASH_RE.split(fragment)
        if len(bounds) != 2:
            raise TimeRangeError(f"Nie rozumiem zakresu „{fragment}”. {EXAMPLES}")
        raw_start, raw_end = (bound.strip() for bound in bounds)
        if not raw_start and not raw_end:
            raise TimeRangeError(f"Zakres „{fragment}” musi mieć początek albo koniec.")
        start = _parse_timestamp(raw_start, fragment) if raw_start else None
        end = _parse_timestamp(raw_end, fragment) if raw_end else None
        if end is not None and (start or 0) >= end:
            raise TimeRangeError(
                f"W zakresie {format_timestamp(start or 0)}-{format_timestamp(end)} "
                "początek musi być wcześniej niż koniec."
            )
        specs.append(RangeSpec(start, end))
    return specs


def resolve_ranges(specs: list[RangeSpec], duration_sec: int) -> list[ResolvedRange]:
    """Validate parsed ranges against a file duration (whole seconds)."""

    length = format_timestamp(duration_sec)
    resolved: list[ResolvedRange] = []
    for spec in specs:
        start = spec.start_sec or 0
        open_end = spec.end_sec is None
        end = duration_sec if open_end else spec.end_sec
        if start >= duration_sec:
            raise TimeRangeError(
                f"Początek {format_timestamp(start)} jest poza plikiem (długość {length})."
            )
        if end > duration_sec:
            raise TimeRangeError(
                f"Koniec {format_timestamp(end)} jest poza plikiem (długość {length}). "
                f"Wpisz „{format_timestamp(start)}-”, żeby ciąć do końca."
            )
        if start == 0 and end == duration_sec:
            raise TimeRangeError(
                f"Zakres {format_timestamp(start)}-{format_timestamp(end)} "
                "obejmuje cały plik — nie ma czego ciąć."
            )
        resolved.append(ResolvedRange(start, end, open_end))
    return resolved


def parse_time_range(text: str) -> dict | None:
    """Parse one closed range; legacy contract kept for existing callers and tests."""

    try:
        specs = parse_time_ranges(text, max_ranges=1)
    except TimeRangeError:
        return None
    spec = specs[0]
    if spec.start_sec is None or spec.end_sec is None:
        return None
    return range_to_session_dict(spec.start_sec, spec.end_sec)
