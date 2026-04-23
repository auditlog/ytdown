"""PlatformConfig dataclass — data contract for each supported platform."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class PlatformConfig:
    """Declarative per-platform configuration.

    Platform modules under bot/platforms/ export exactly one of these as CONFIG.
    Integration sites read fields via bot.platforms.get_platform(name).
    """

    name: str
    display_name: str
    domains: tuple[str, ...]
    hide_flac: bool
    hide_time_range: bool
    is_podcast: bool
    media_label: str
    requires_cookies: bool
    cookies_hint: str | None = None
