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

import json
import logging
import os
import re
from dataclasses import dataclass

import requests

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


EMBED_URL = "https://open.spotify.com/embed/episode/{episode_id}"

# Spotify serves different payloads to non-browser agents; a realistic UA keeps
# the embed page rendering the __NEXT_DATA__ blob we parse.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_NEXT_DATA_PATTERN = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)


@dataclass(frozen=True)
class EmbedData:
    """Episode metadata and web-player token read from the embed page."""

    access_token: str
    manifest_id: str | None
    title: str
    show_name: str
    duration_ms: int
    has_video: bool
    requires_drm: bool


def parse_embed_html(html: str) -> EmbedData | None:
    """Parse the embed page's __NEXT_DATA__ blob into an EmbedData.

    Returns None when the blob is absent or malformed, which in practice means
    Spotify changed the page shape — callers surface that explicitly rather
    than treating it as "episode not found".
    """

    match = _NEXT_DATA_PATTERN.search(html)
    if not match:
        return None

    try:
        payload = json.loads(match.group(1))
        state = payload["props"]["pageProps"]["state"]
        entity = state["data"]["entity"]
    except (ValueError, KeyError, TypeError):
        return None

    access_token = (
        state.get("settings", {}).get("session", {}).get("accessToken", "")
    )
    video_entries = (
        state["data"].get("defaultAudioFileObject", {}).get("video") or []
    )
    first_video = video_entries[0] if video_entries else {}

    return EmbedData(
        access_token=access_token,
        manifest_id=first_video.get("manifestId"),
        title=entity.get("title", ""),
        show_name=entity.get("subtitle", ""),
        duration_ms=int(entity.get("duration") or 0),
        has_video=bool(entity.get("hasVideo")),
        requires_drm=bool(first_video.get("requiresDRM")),
    )


def fetch_embed_data(episode_id: str, sp_dc: str) -> EmbedData:
    """Fetch and parse the embed page for one episode.

    The sp_dc cookie is what makes the returned token non-anonymous; without it
    Spotify still renders the page but the token cannot reach video manifests.
    """

    response = requests.get(
        EMBED_URL.format(episode_id=episode_id),
        headers={"User-Agent": BROWSER_USER_AGENT},
        cookies={"sp_dc": sp_dc},
        timeout=25,
    )
    response.raise_for_status()

    data = parse_embed_html(response.text)
    if data is None:
        raise SpotifyVideoError(
            "Spotify embed page did not contain the expected __NEXT_DATA__ blob"
        )
    return data
