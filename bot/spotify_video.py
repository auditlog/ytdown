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


def _dig(node, *keys, default=None):
    """Walk nested dicts, treating a present-but-null value as absent.

    Safely extracts deeply nested values from dicts, handling both missing keys
    and present-but-null values at any depth. Returns default if any level in
    the path is not a dict, is None, or doesn't contain the key.
    """
    for key in keys:
        if not isinstance(node, dict):
            return default
        node = node.get(key)
        if node is None:
            return default
    return node


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
    except (ValueError, KeyError, TypeError):
        return None

    # Check for structural integrity: state and state["data"] must both be
    # dicts. These are required elements; if either is missing, null, or the
    # wrong type, the blob is unrecognizable and we return None to signal
    # "API changed", not "no data". A key being present but null (e.g. a
    # well-formed `{"state": null}`) does not raise KeyError above, so this
    # isinstance check is what actually catches that case.
    if not isinstance(state, dict):
        return None
    data = state.get("data")
    if not isinstance(data, dict):
        return None

    # Use _dig to safely traverse nested dicts, handling null values at every level.
    entity = _dig(data, "entity", default={})
    if not isinstance(entity, dict):
        entity = {}

    access_token = _dig(state, "settings", "session", "accessToken", default="")
    video_entries = _dig(data, "defaultAudioFileObject", "video", default=[])
    if not isinstance(video_entries, list):
        video_entries = []

    # Handle potential null entries in the video array.
    first_video = None
    if video_entries:
        candidate = video_entries[0]
        if isinstance(candidate, dict):
            first_video = candidate
    if first_video is None:
        first_video = {}

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


MANIFEST_URL = (
    "https://spclient.wg.spotify.com/manifests/v6/json/sources/"
    "{manifest_id}/options/supports_drm"
)

# The manifest's max_bitrate is a peak, not an average. Measured against the
# live CDN on 2026-08-26: the 720p profile delivered 145 KB/s against a
# 325 KB/s peak, so real size lands near 45% of the naive max_bitrate figure.
BITRATE_TO_AVERAGE_RATIO = 0.45

# Telegram plays H.264 natively; VP9 inside MP4 often fails to render.
_H264_CODEC_PREFIX = "avc1"
_AAC_CODEC_PREFIX = "mp4a"


@dataclass(frozen=True)
class Profile:
    """One selectable video rendition from the manifest."""

    id: int
    width: int
    height: int
    codec: str
    max_bitrate: int
    mime_type: str


def _manifest_content(manifest: dict) -> dict:
    contents = manifest.get("contents") or []
    if not contents:
        raise SpotifyVideoError("Spotify manifest carries no contents entry")
    return contents[0]


def fetch_video_manifest(manifest_id: str, access_token: str) -> dict:
    """Fetch the segmented video manifest for one episode.

    Only API version v6 exists — v7 and v8 return 404 as of 2026-08-26.
    """

    response = requests.get(
        MANIFEST_URL.format(manifest_id=manifest_id),
        headers={
            "Authorization": f"Bearer {access_token}",
            "app-platform": "WebPlayer",
            "User-Agent": BROWSER_USER_AGENT,
            "Accept": "application/json",
        },
        timeout=25,
    )
    if response.status_code == 404:
        raise SpotifyVideoError(
            "Spotify manifest endpoint v6 returned 404 — the API shape changed"
        )
    response.raise_for_status()
    return response.json()


def list_profiles(manifest: dict) -> list[Profile]:
    """Return selectable H.264 video profiles, highest resolution first."""

    profiles = []
    for raw in _manifest_content(manifest).get("profiles", []):
        codec = raw.get("video_codec", "")
        if not codec.startswith(_H264_CODEC_PREFIX):
            continue
        profiles.append(
            Profile(
                id=int(raw["id"]),
                width=int(raw.get("video_width") or 0),
                height=int(raw.get("video_height") or 0),
                codec=codec,
                max_bitrate=int(raw.get("max_bitrate") or 0),
                mime_type=raw.get("mime_type", "video/mp4"),
            )
        )
    return sorted(profiles, key=lambda p: p.height, reverse=True)


def find_audio_profile_id(manifest: dict) -> int:
    """Return the AAC audio profile id from the video manifest.

    This track is DRM-free even though the episode's standalone audio file is
    MP4_128_CBCS (encrypted) — verified 2026-08-26.
    """

    for raw in _manifest_content(manifest).get("profiles", []):
        if raw.get("audio_codec", "").startswith(_AAC_CODEC_PREFIX):
            return int(raw["id"])
    raise SpotifyVideoError("Spotify manifest carries no AAC audio profile")


def manifest_duration_ms(manifest: dict) -> int:
    """Return the episode duration covered by the manifest, in milliseconds."""

    content = _manifest_content(manifest)
    return int(content.get("end_time_millis", 0)) - int(
        content.get("start_time_millis", 0)
    )


def estimate_size_mb(profile: Profile, duration_ms: int) -> float:
    """Estimate the download size in MiB for one profile.

    Reported in MiB to match how the rest of the bot computes file_size_mb.
    """

    average_bytes_per_second = profile.max_bitrate * BITRATE_TO_AVERAGE_RATIO / 8
    return average_bytes_per_second * (duration_ms / 1000) / (1024 * 1024)
