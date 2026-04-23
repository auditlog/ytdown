"""LinkedIn platform config."""

from bot.platforms.base import PlatformConfig

CONFIG = PlatformConfig(
    name="linkedin",
    display_name="LinkedIn",
    domains=("linkedin.com",),
    hide_flac=False,
    hide_time_range=False,
    is_podcast=False,
    media_label="filmie",
    requires_cookies=True,
    cookies_hint=(
        "LinkedIn wymaga zalogowania do pobierania video z postów. "
        "Zaloguj się na linkedin.com i wyeksportuj cookies rozszerzeniem "
        "\"Get cookies.txt LOCALLY\"."
    ),
)
