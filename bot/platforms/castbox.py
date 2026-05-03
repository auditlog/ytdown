"""Castbox platform config."""

from bot.platforms.base import PlatformConfig

CONFIG = PlatformConfig(
    name="castbox",
    display_name="Castbox",
    domains=("castbox.fm",),
    hide_flac=True,
    hide_time_range=True,
    is_podcast=True,
    media_label="odcinku",
    requires_cookies=False,
    cookies_hint=None,
)
