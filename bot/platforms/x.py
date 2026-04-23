"""X (Twitter) platform config."""

from bot.platforms.base import PlatformConfig

CONFIG = PlatformConfig(
    name="x",
    display_name="X",
    domains=("x.com", "twitter.com", "mobile.twitter.com"),
    hide_flac=True,
    hide_time_range=True,
    is_podcast=False,
    media_label="filmie",
    requires_cookies=True,
    cookies_hint=(
        "Wiele tweetów (szczególnie oznaczonych jako Sensitive) wymaga "
        "zalogowania. Zaloguj się na x.com w przeglądarce i wyeksportuj "
        "cookies rozszerzeniem \"Get cookies.txt LOCALLY\"."
    ),
)
