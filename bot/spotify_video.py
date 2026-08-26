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
import math
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    except (OSError, UnicodeDecodeError) as exc:
        # UnicodeDecodeError (a ValueError, not an OSError) is what a jar
        # saved in another encoding raises on read. Either way the file is
        # unusable, which is exactly what returning None already means --
        # left uncaught it escaped every caller above and froze the user's
        # status message instead of showing the cookie-export instructions.
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


def _require_int(value, field: str) -> int:
    """Coerce a Spotify-supplied field to int, or fail as SpotifyVideoError.

    Every accessor in this module must leave Spotify's own drift as a
    SpotifyVideoError: a raw KeyError or ValueError escaping from here
    propagates past the callers' error mapping and freezes the user's status
    message with no error at all (design spec 10 rules that out explicitly).
    """

    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise SpotifyVideoError(
            f"Spotify data field {field} is not a number: {value!r}"
        ) from exc


def _require_template(manifest: dict, key: str) -> str:
    """Read a URL template from the manifest, or fail as SpotifyVideoError.

    See _require_int for why a bare ``manifest[key]`` subscript is not
    acceptable here.
    """

    template = manifest.get(key)
    if not isinstance(template, str) or not template:
        raise SpotifyVideoError(f"Spotify manifest carries no {key}")
    return template


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

    Returns None when the blob is absent or unrecognizable, which in practice
    means Spotify changed the page shape — callers surface that explicitly
    rather than treating it as "episode not found".

    Raises SpotifyVideoError when the blob is recognizable but a field holds
    something unusable (a non-numeric duration, say). That is the same "API
    changed" story, just discovered one level deeper, and it must not leave
    this function as a raw ValueError — see _require_int.
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
        duration_ms=_require_int(entity.get("duration") or 0, "entity.duration"),
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

# The video heights the bot exposes as buttons, highest first. Real manifests
# also carry 426x240 and 320x180 H.264 profiles; both are useless for a video
# podcast and only lengthen the keyboard, so they are deliberately not offered
# (design spec 6.1). Owned here rather than in the UI or the parser because
# both of those need it and this is the module they already sit above: the
# service builds the keyboard options from it and
# bot/handlers/callback_parsing.py validates incoming callbacks against it.
# One tuple, two importers -- a second literal copy would drift.
SPOTIFY_VIDEO_HEIGHTS = (1080, 720, 480, 320)


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
                id=_require_int(raw.get("id"), "profiles[].id"),
                width=_require_int(raw.get("video_width") or 0, "profiles[].video_width"),
                height=_require_int(raw.get("video_height") or 0, "profiles[].video_height"),
                codec=codec,
                max_bitrate=_require_int(raw.get("max_bitrate") or 0, "profiles[].max_bitrate"),
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
            return _require_int(raw.get("id"), "profiles[].id")
    raise SpotifyVideoError("Spotify manifest carries no AAC audio profile")


def manifest_duration_ms(manifest: dict) -> int:
    """Return the episode duration covered by the manifest, in milliseconds."""

    content = _manifest_content(manifest)
    return _require_int(
        content.get("end_time_millis", 0), "contents[].end_time_millis"
    ) - _require_int(
        content.get("start_time_millis", 0), "contents[].start_time_millis"
    )


def estimate_size_mb(profile: Profile, duration_ms: int) -> float:
    """Estimate the download size in MiB for one profile.

    Reported in MiB to match how the rest of the bot computes file_size_mb.
    """

    average_bytes_per_second = profile.max_bitrate * BITRATE_TO_AVERAGE_RATIO / 8
    return average_bytes_per_second * (duration_ms / 1000) / (1024 * 1024)


def _expand_template(template: str, base_urls: list[str], **placeholders) -> list[str]:
    """Fill a manifest URL template and prefix it with every CDN base URL."""

    path = template
    for name, value in placeholders.items():
        path = path.replace("{{%s}}" % name, str(value))
    return [base + path for base in base_urls]


def build_track_urls(
    manifest: dict, profile_id: int
) -> tuple[list[str], list[list[str]]]:
    """Build init and segment URL candidates for one profile.

    Each returned segment is a list of equivalent URLs, one per CDN in
    base_urls, so the downloader can fail over without rebuilding anything.
    """

    content = _manifest_content(manifest)
    base_urls = manifest.get("base_urls") or []
    if not base_urls:
        raise SpotifyVideoError("Spotify manifest carries no base_urls")

    segment_length = _require_int(
        content.get("segment_length") or 0, "contents[].segment_length"
    )
    if segment_length <= 0:
        raise SpotifyVideoError("Spotify manifest carries no segment_length")

    file_type = "mp4"

    init_urls = _expand_template(
        _require_template(manifest, "initialization_template"),
        base_urls,
        profile_id=profile_id,
        file_type=file_type,
    )

    # segment_timestamp is whole seconds counted from the episode start
    # (0, 4, 8, ...), not milliseconds — verified against the live CDN.
    duration_seconds = manifest_duration_ms(manifest) / 1000
    segment_count = math.ceil(duration_seconds / segment_length)

    segment_template = _require_template(manifest, "segment_template")
    segment_urls = [
        _expand_template(
            segment_template,
            base_urls,
            profile_id=profile_id,
            file_type=file_type,
            segment_timestamp=index * segment_length,
        )
        for index in range(segment_count)
    ]

    return init_urls, segment_urls


SEGMENT_WORKERS = 8
SEGMENT_BATCH_SIZE = 32
SEGMENT_ATTEMPTS = 3


def _fetch_bytes(url: str, timeout: int = 30) -> bytes:
    """Fetch one URL and return its body. Separated out so tests can stub it."""

    response = requests.get(
        url, headers={"User-Agent": BROWSER_USER_AGENT}, timeout=timeout
    )
    response.raise_for_status()
    return response.content


# Spotify signs every CDN URL with __token__/fauth (or token/fauth) query
# parameters that carry an expiry. They are capability URLs: whoever reads one
# can fetch the object until the signature expires, so they must never reach a
# log file or a chat message. Host and path stay -- those are the diagnostic.
_URL_QUERY_PATTERN = re.compile(r"""(https?://[^\s'"<>]*?)\?([^\s'"<>]*)""")

# Punctuation that ends a sentence rather than the URL. A URL running up
# against ": " swallows the colon into the query match, so it is put back
# after the redaction marker -- otherwise the message loses the separator
# between the URL and the error that followed it, and stops being readable
# for the operator it exists to inform.
_TRAILING_PUNCTUATION = ".,;:!?"

# Deliberately bracket-delimited rather than angle-bracketed: the pattern above
# stops at "<" and ">", so an angle-bracketed marker would survive a second
# pass as literal text and redact the same URL twice. With this marker the
# function is idempotent, which matters because a message can pass through
# more than one redaction point on its way to a log.
_REDACTED_MARKER = "[redacted]"


def _redact_url_query(text: str) -> str:
    """Strip query strings from every URL in text before it is logged or shown.

    Applied where the failure message is composed rather than at each log
    call: the chained requests error quotes the signed URL too, so redacting
    only the URL this module holds would leave the secret in the message
    anyway.
    """

    def _replace(match: "re.Match[str]") -> str:
        query = match.group(2)
        trailing = query[len(query.rstrip(_TRAILING_PUNCTUATION)):]
        return f"{match.group(1)}?{_REDACTED_MARKER}{trailing}"

    return _URL_QUERY_PATTERN.sub(_replace, text)


def _fetch_with_failover(url_candidates: list[str]) -> bytes:
    """Fetch one segment, trying every CDN before backing off and retrying."""

    if not url_candidates:
        raise SpotifyVideoError("Segment download failed: no CDN candidates supplied")

    last_error: Exception | None = None
    last_url: str | None = None
    for attempt in range(SEGMENT_ATTEMPTS):
        for url in url_candidates:
            try:
                return _fetch_bytes(url)
            except SpotifyVideoCancelled:
                # Not a fetch failure — let cancellation propagate immediately
                # instead of being absorbed into the retry loop.
                raise
            except Exception as exc:
                # Catch broadly, not just requests.RequestException: any
                # failure here must surface as SpotifyVideoError so callers
                # (see the Polish-message mapping in the download flow) can
                # rely on a single error type from this pipeline stage.
                last_error = exc
                last_url = url
        if attempt < SEGMENT_ATTEMPTS - 1:
            time.sleep(2 ** attempt)
    raise SpotifyVideoError(
        _redact_url_query(
            f"Segment download failed after {SEGMENT_ATTEMPTS} attempts; "
            f"last error from {last_url}: {last_error}"
        )
    )


def _raise_if_cancelled(cancellation) -> None:
    if cancellation is not None and cancellation.event.is_set():
        raise SpotifyVideoCancelled("Download cancelled by user")


def download_track(
    init_urls: list[str],
    segment_urls: list[list[str]],
    dest_path: str,
    *,
    progress_cb=None,
    cancellation=None,
    workers: int = SEGMENT_WORKERS,
    batch_size: int = SEGMENT_BATCH_SIZE,
) -> str:
    """Download one media track (video or audio) into a single file.

    Segments are fetched concurrently but written in order. Work is done in
    batches so at most batch_size segments are held in memory at once —
    a full episode would otherwise need hundreds of megabytes of RAM or a
    thousand temporary files.

    Writes to a ``.part`` sibling of dest_path and only renames it into place
    (atomically, via os.replace) once the whole track has downloaded
    successfully. A failed or cancelled download therefore never leaves a
    truncated file sitting at dest_path.
    """

    total = len(segment_urls)
    _raise_if_cancelled(cancellation)

    part_path = dest_path + ".part"
    try:
        with open(part_path, "wb") as out:
            out.write(_fetch_with_failover(init_urls))

            pool = ThreadPoolExecutor(max_workers=workers)
            try:
                for start in range(0, total, batch_size):
                    _raise_if_cancelled(cancellation)
                    batch = segment_urls[start:start + batch_size]

                    futures = {
                        pool.submit(_fetch_with_failover, candidates): offset
                        for offset, candidates in enumerate(batch)
                    }
                    chunks: dict[int, bytes] = {}
                    for future in as_completed(futures):
                        chunks[futures[future]] = future.result()

                    for offset in range(len(batch)):
                        out.write(chunks[offset])

                    if progress_cb is not None:
                        progress_cb(min(start + batch_size, total), total)
            finally:
                # cancel_futures drops segment fetches queued but not yet
                # started, instead of burning minutes retrying against a CDN
                # already known to be failing once one segment gives up.
                pool.shutdown(wait=True, cancel_futures=True)
    except BaseException:
        if os.path.exists(part_path):
            try:
                os.remove(part_path)
            except OSError:
                pass
        raise

    os.replace(part_path, dest_path)
    return dest_path


MUX_TIMEOUT_SECONDS = 600


def subtitle_languages(manifest: dict) -> list[str]:
    """Return subtitle language codes the manifest offers."""

    return list(manifest.get("subtitle_language_codes") or [])


def fetch_subtitles(manifest: dict, language_code: str, dest_path: str) -> str | None:
    """Download one WebVTT subtitle track. Returns None when unavailable."""

    if language_code not in subtitle_languages(manifest):
        return None

    base_urls = manifest.get("subtitle_base_urls") or []
    template = manifest.get("subtitle_template")
    if not base_urls or not template:
        return None

    candidates = _expand_template(template, base_urls, language_code=language_code)
    try:
        payload = _fetch_with_failover(candidates)
    except SpotifyVideoError as exc:
        # _fetch_with_failover already redacts the signed query string it
        # composes into this message; redact again here so a future caller
        # that raises its own message cannot reintroduce the leak.
        logging.warning("Spotify subtitle download failed: %s", _redact_url_query(str(exc)))
        return None

    with open(dest_path, "wb") as file_obj:
        file_obj.write(payload)
    return dest_path


def mux(video_path: str, audio_path: str, out_path: str) -> str:
    """Combine the video and audio tracks without re-encoding."""

    try:
        result = subprocess.run(
            [
                "ffmpeg", "-v", "error",
                "-i", video_path,
                "-i", audio_path,
                "-c", "copy",
                "-y", out_path,
            ],
            capture_output=True,
            timeout=MUX_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise SpotifyVideoError("ffmpeg_missing") from exc
    except subprocess.TimeoutExpired as exc:
        # Bare reason code, not a descriptive sentence: the service layer
        # (bot/services/spotify_video_service.py) maps this by exact string
        # match, the same as every other SpotifyVideoError this pipeline
        # raises. The original TimeoutExpired is still chained via `from exc`
        # so the MUX_TIMEOUT_SECONDS detail survives in logs/tracebacks.
        raise SpotifyVideoError("mux_timeout") from exc

    if result.returncode != 0:
        detail = (result.stderr or b"").decode("utf-8", "replace")[:200]
        raise SpotifyVideoError(f"ffmpeg mux failed: {detail}")
    return out_path
