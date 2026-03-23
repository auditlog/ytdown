"""Shared parsing helpers for chat-provided time ranges."""

from __future__ import annotations

import re


def parse_time_range(text: str) -> dict | None:
    """Parse time range input in SS, MM:SS, or HH:MM:SS forms."""

    match = re.match(r"^(\d{1,2}(?::\d{2}){0,2})\s*-\s*(\d{1,2}(?::\d{2}){0,2})$", text.strip())
    if not match:
        return None

    def time_to_seconds(time_str: str) -> int:
        parts = time_str.split(":")
        if len(parts) == 1:
            return int(parts[0])
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        return 0

    def format_time(seconds: int) -> str:
        if seconds >= 3600:
            return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"
        return f"{seconds // 60}:{seconds % 60:02d}"

    try:
        start_sec = time_to_seconds(match.group(1))
        end_sec = time_to_seconds(match.group(2))
        if start_sec >= end_sec:
            return None
        return {
            "start": format_time(start_sec),
            "end": format_time(end_sec),
            "start_sec": start_sec,
            "end_sec": end_sec,
        }
    except (ValueError, IndexError):
        return None
