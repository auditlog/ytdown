"""Input validation and resource policy independent of MCP transport."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, get_args
from urllib.parse import parse_qs, urlsplit

VideoQuality = Literal["best", "4320p", "2160p", "1440p", "1080p", "720p", "480p", "360p"]
VIDEO_QUALITIES = get_args(VideoQuality)


class UserError(ValueError):
    """A deliberately safe message that may be returned to the MCP caller."""


def youtube_url(url: str) -> str:
    """Accept single YouTube videos and rebuild the URL from a validated ID.

    Canonicalization drops playlist, redirect, tracking and other query arguments.
    No user-supplied host, port or path reaches an extractor.
    """
    if len(url) > 2048 or any(ord(char) < 33 for char in url):
        raise ValueError("Podaj poprawny adres HTTPS filmu YouTube.")
    try:
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.username is not None
            or parts.password is not None
            or parts.port not in (None, 443)
        ):
            raise ValueError
        if parts.hostname == "youtu.be":
            video_id = parts.path.removeprefix("/")
        elif parts.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
            if parts.path == "/watch":
                values = parse_qs(parts.query).get("v", [])
                video_id = values[0] if len(values) == 1 else ""
            else:
                match = re.fullmatch(r"/(?:shorts|embed|live)/([A-Za-z0-9_-]{11})", parts.path)
                video_id = match.group(1) if match else ""
        else:
            raise ValueError
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ValueError
    except ValueError:
        raise ValueError("Podaj adres HTTPS pojedynczego filmu YouTube.") from None
    return f"https://www.youtube.com/watch?v={video_id}"


@dataclass(frozen=True)
class Limits:
    """Operator-controlled limits; never accepted as tool arguments."""

    max_active: int = 2
    jobs_per_hour: int = 10
    max_duration_seconds: int = 7200
    max_media_bytes: int = 10 * 1024**3
    max_job_bytes: int = 22 * 1024**3
    archive_volume_mb: int = 1000
    min_free_bytes: int = 2 * 1024**3
    timeout_seconds: int = 7200
    retention_seconds: int = 86400

    def __post_init__(self):
        if any(value <= 0 for value in vars(self).values()):
            raise ValueError("MCP limits must be positive.")
