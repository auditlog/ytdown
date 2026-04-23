"""YouTube platform config."""

from bot.platforms.base import PlatformConfig

CONFIG = PlatformConfig(
    name="youtube",
    display_name="YouTube",
    domains=("youtube.com", "youtu.be", "m.youtube.com", "music.youtube.com"),
    hide_flac=False,
    hide_time_range=False,
    is_podcast=False,
    media_label="filmie",
    requires_cookies=False,
    cookies_hint=(
        "YouTube czasem blokuje pobieranie komunikatem "
        "\"Sign in to confirm you're not a bot\". Zaloguj się na YouTube "
        "w przeglądarce i wyeksportuj cookies rozszerzeniem "
        "\"Get cookies.txt LOCALLY\"."
    ),
)
