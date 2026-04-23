"""TikTok platform config."""

from bot.platforms.base import PlatformConfig

CONFIG = PlatformConfig(
    name="tiktok",
    display_name="TikTok",
    domains=("tiktok.com", "m.tiktok.com", "vm.tiktok.com"),
    hide_flac=True,
    hide_time_range=True,
    is_podcast=False,
    media_label="filmie",
    requires_cookies=True,
    cookies_hint=(
        "TikTok często wymaga zalogowania. Zaloguj się na tiktok.com "
        "w przeglądarce i wyeksportuj cookies rozszerzeniem "
        "\"Get cookies.txt LOCALLY\"."
    ),
)
