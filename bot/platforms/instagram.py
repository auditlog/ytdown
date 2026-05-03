"""Instagram platform config."""

from bot.platforms.base import PlatformConfig

CONFIG = PlatformConfig(
    name="instagram",
    display_name="Instagram",
    domains=("instagram.com",),
    hide_flac=False,
    hide_time_range=False,
    is_podcast=False,
    media_label="filmie",
    requires_cookies=True,
    cookies_hint=(
        "Instagram blokuje większość postów bez aktywnej sesji. "
        "Zaloguj się na instagram.com i wyeksportuj cookies rozszerzeniem "
        "\"Get cookies.txt LOCALLY\". Do zdjęć/karuzel potrzebny jest "
        "dodatkowo pakiet instaloader."
    ),
)
