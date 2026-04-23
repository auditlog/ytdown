"""Spotify platform config."""

from bot.platforms.base import PlatformConfig

CONFIG = PlatformConfig(
    name="spotify",
    display_name="Spotify",
    domains=("open.spotify.com",),
    hide_flac=True,
    hide_time_range=True,
    is_podcast=True,
    media_label="odcinku",
    requires_cookies=False,
    cookies_hint=None,
)
