"""Direct Spotify video episode download.

Resolves a Spotify episode to its DRM-free segmented video manifest and
downloads the segments straight from Spotify's CDN.

Requires a Spotify session cookie (sp_dc) exported to SPOTIFY_COOKIES_FILE.
Does not require Spotify Web API credentials — the embed page carries every
piece of metadata this pipeline needs.

Episodes whose manifest reports requires_drm are refused outright; this module
never attempts to circumvent content protection.
"""

from __future__ import annotations

import logging
import os

from bot.config import SPOTIFY_COOKIES_FILE


class SpotifyVideoError(Exception):
    """Raised when the Spotify video pipeline cannot complete a step."""


class SpotifyVideoCancelled(Exception):
    """Raised when a download is aborted through a JobCancellation handle."""


def load_spotify_cookie(cookies_file: str = SPOTIFY_COOKIES_FILE) -> str | None:
    """Extract the sp_dc value from a Netscape-format cookie jar.

    Returns None when the file is absent, unreadable, or carries no sp_dc
    entry — callers turn that into a user-facing setup hint.
    """

    if not cookies_file or not os.path.exists(cookies_file):
        return None

    try:
        with open(cookies_file, encoding="utf-8") as file_obj:
            for line in file_obj:
                if line.startswith("#"):
                    continue
                fields = line.rstrip("\n").split("\t")
                # Netscape format: domain, flag, path, secure, expiry, name, value
                if len(fields) >= 7 and fields[5] == "sp_dc":
                    return fields[6] or None
    except OSError as exc:
        logging.error("Cannot read Spotify cookie jar %s: %s", cookies_file, exc)

    return None
