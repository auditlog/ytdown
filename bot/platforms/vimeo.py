"""Vimeo platform config."""

from bot.platforms.base import PlatformConfig

CONFIG = PlatformConfig(
    name="vimeo",
    display_name="Vimeo",
    domains=("vimeo.com", "player.vimeo.com"),
    hide_flac=False,
    hide_time_range=False,
    is_podcast=False,
    media_label="filmie",
    requires_cookies=False,
    cookies_hint=None,
)
