"""TOK FM platform config.

Episodes are extracted by the yt-dlp plugin in yt_dlp_plugins/extractor/tokfm.py.
Premium episodes need the owner's TOK FM session (PHPSESSID on .tokfm.pl) in
cookies.txt; without it the plugin refuses instead of downloading.
"""

from bot.platforms.base import PlatformConfig

CONFIG = PlatformConfig(
    name="tokfm",
    display_name="TOK FM",
    domains=("audycje.tokfm.pl",),
    hide_flac=True,
    hide_time_range=True,
    is_podcast=True,
    media_label="odcinku",
    requires_cookies=True,
    cookies_hint=(
        "TOK FM: sesja wygasła albo brak cookies. Zaloguj się na audycje.tokfm.pl "
        "w przeglądarce, wyeksportuj cookies rozszerzeniem \"Get cookies.txt LOCALLY\" "
        "i dopisz linie z domeny .tokfm.pl (wystarczy PHPSESSID) do cookies.txt na serwerze."
    ),
)
