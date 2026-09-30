"""Spotify metadata and external audio-resolution helpers.

Podcast episodes prefer iTunes and fall back to YouTube. Music tracks use
Spotify metadata to find a high-confidence YouTube Music/official-audio match;
Spotify's protected music streams are never downloaded by this module.
"""

import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from urllib.parse import urlparse

import requests
import yt_dlp

from bot.config import YTDLP_JS_RUNTIMES, YTDLP_REMOTE_COMPONENTS, get_runtime_value
from bot.spotify_oauth import SpotifyOAuthError, get_spotify_user_access_token
from bot.spotify_video import fetch_embed_access_token, load_spotify_cookie

# Spotify API token cache
_spotify_token = None
_spotify_token_expires = 0

SPOTIFY_COLLECTION_MAX_ITEMS = 500
_SPOTIFY_RESOURCE_TYPES = {"track", "album", "playlist", "episode"}
_YOUTUBE_MATCH_THRESHOLD = 0.55


@dataclass(frozen=True)
class YouTubeTrackSearchOutcome:
    """A YouTube search match or an actionable reason why matching failed."""

    match: dict | None
    failure_code: str | None = None
    failure_detail: str | None = None


@dataclass(frozen=True)
class TrackResolutionOutcome:
    """A resolved Spotify track or an actionable reason for a failure report."""

    resolved: dict | None
    failure_code: str | None = None
    failure_detail: str | None = None


class SpotifyMetadataError(RuntimeError):
    """Normalized Spotify metadata failure safe to map to Polish UI text."""

    def __init__(self, reason: str, status_code: int | None = None):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


def parse_spotify_url(url: str) -> tuple[str, str] | None:
    """Return ``(resource_type, id)`` for supported open.spotify.com URLs."""

    try:
        parsed = urlparse(url)
        if parsed.netloc.lower() not in ("open.spotify.com", "www.open.spotify.com"):
            return None
        match = re.match(r"^/(track|album|playlist|episode)/([a-zA-Z0-9]+)(?:/|$)", parsed.path)
        if not match:
            return None
        return match.group(1), match.group(2)
    except Exception:
        return None


def parse_spotify_track_url(url: str) -> str | None:
    """Extract a track ID from a Spotify track URL."""

    parsed = parse_spotify_url(url)
    return parsed[1] if parsed and parsed[0] == "track" else None


def parse_spotify_collection_url(url: str) -> tuple[str, str] | None:
    """Extract an album or playlist type/ID pair from a Spotify URL."""

    parsed = parse_spotify_url(url)
    return parsed if parsed and parsed[0] in {"album", "playlist"} else None


def parse_spotify_episode_url(url: str) -> str | None:
    """Extracts episode ID from a Spotify episode URL.

    Accepts:
      - https://open.spotify.com/episode/{ID}
      - https://open.spotify.com/episode/{ID}?si=...

    Returns episode ID string, or None if URL is not a valid episode link.
    """
    parsed = parse_spotify_url(url)
    return parsed[1] if parsed and parsed[0] == "episode" else None


def _get_spotify_token(
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> str | None:
    """Get a Spotify API token, preferring the user's web session.

    A real user OAuth token is required for playlist ownership checks. The
    cookie-derived embed token and client credentials remain fallbacks for
    public catalog metadata and legacy behavior.
    """
    global _spotify_token, _spotify_token_expires

    try:
        user_token = get_spotify_user_access_token()
    except SpotifyOAuthError as exc:
        logging.warning("Spotify user OAuth unavailable: reason=%s", exc.reason)
        user_token = None
    if user_token:
        return user_token

    if resource_type in _SPOTIFY_RESOURCE_TYPES and resource_id:
        cookie = load_spotify_cookie()
        if cookie:
            try:
                embed_token = fetch_embed_access_token(
                    resource_type,
                    resource_id,
                    cookie,
                )
                if embed_token:
                    return embed_token
            except requests.RequestException as exc:
                logging.warning("Spotify embed token request failed: %s", exc)

    client_id = get_runtime_value('SPOTIFY_CLIENT_ID', '')
    client_secret = get_runtime_value('SPOTIFY_CLIENT_SECRET', '')
    if not client_id or not client_secret:
        return None

    # Return cached token if still valid
    if _spotify_token and time.time() < _spotify_token_expires - 60:
        return _spotify_token

    try:
        resp = requests.post(
            'https://accounts.spotify.com/api/token',
            data={'grant_type': 'client_credentials'},
            auth=(client_id, client_secret),
            timeout=10,
        )
        if resp.status_code != 200:
            logging.error("Spotify token request failed: %s", resp.status_code)
            return None

        data = resp.json()
        _spotify_token = data['access_token']
        _spotify_token_expires = time.time() + data.get('expires_in', 3600)
        return _spotify_token

    except Exception as e:
        logging.error("Error getting Spotify token: %s", e)
        return None


def _spotify_api_get(path_or_url: str, token: str, *, params: dict | None = None) -> dict:
    """Call one Spotify Web API endpoint and normalize actionable failures."""

    url = (
        path_or_url
        if path_or_url.startswith("https://")
        else f"https://api.spotify.com/v1/{path_or_url.lstrip('/')}"
    )
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=15,
        )
    except requests.RequestException as exc:
        raise SpotifyMetadataError("network_error") from exc

    if response.status_code == 200:
        try:
            payload = response.json()
        except ValueError as exc:
            raise SpotifyMetadataError("api_changed", response.status_code) from exc
        if not isinstance(payload, dict):
            raise SpotifyMetadataError("api_changed", response.status_code)
        return payload

    reason = {
        401: "expired_session",
        403: "forbidden",
        404: "not_found",
        429: "rate_limited",
    }.get(response.status_code, "api_error")
    logging.warning(
        "Spotify metadata request failed: endpoint=%s status=%s",
        urlparse(url).path,
        response.status_code,
    )
    raise SpotifyMetadataError(reason, response.status_code)


def _normalize_track(track: dict, *, album_name: str = "") -> dict | None:
    """Normalize full or simplified Spotify track objects for UI/search use."""

    if not isinstance(track, dict) or track.get("type", "track") != "track":
        return None
    if track.get("is_local"):
        return None

    track_id = track.get("id")
    title = track.get("name")
    if not track_id or not title:
        return None

    artists = [
        artist.get("name", "").strip()
        for artist in track.get("artists") or []
        if isinstance(artist, dict) and artist.get("name")
    ]
    album = track.get("album") if isinstance(track.get("album"), dict) else {}
    spotify_url = (track.get("external_urls") or {}).get("spotify")
    if not spotify_url:
        spotify_url = f"https://open.spotify.com/track/{track_id}"

    return {
        "id": track_id,
        "title": str(title),
        "artists": artists,
        "artist": ", ".join(artists),
        "duration_ms": int(track.get("duration_ms") or 0),
        "album": album.get("name") or album_name,
        "spotify_url": spotify_url,
        "explicit": bool(track.get("explicit")),
    }


def get_spotify_track_info(track_id: str) -> dict | None:
    """Fetch normalized metadata for one Spotify catalog track."""

    token = _get_spotify_token("track", track_id)
    if not token:
        return None
    return _normalize_track(_spotify_api_get(f"tracks/{track_id}", token))


def _page_items(page: dict | None) -> list[dict]:
    if not isinstance(page, dict):
        return []
    return [item for item in page.get("items") or [] if isinstance(item, dict)]


def _unwrap_playlist_track(item: dict) -> dict | None:
    candidate = item.get("item") or item.get("track") or item
    return candidate if isinstance(candidate, dict) else None


def get_spotify_collection(
    url: str,
    *,
    max_items: int = SPOTIFY_COLLECTION_MAX_ITEMS,
) -> dict | None:
    """Load selectable tracks from a Spotify album or accessible playlist."""

    parsed = parse_spotify_collection_url(url)
    if not parsed:
        return None
    resource_type, resource_id = parsed
    token = _get_spotify_token(resource_type, resource_id)
    if not token:
        return {
            "kind": resource_type,
            "id": resource_id,
            "error": "no_credentials",
        }

    try:
        detail = _spotify_api_get(f"{resource_type}s/{resource_id}", token)
        title = str(detail.get("name") or ("Album" if resource_type == "album" else "Playlista"))
        if resource_type == "album":
            owner = ", ".join(
                artist.get("name", "")
                for artist in detail.get("artists") or []
                if isinstance(artist, dict) and artist.get("name")
            )
            page = detail.get("tracks")
        else:
            owner_data = detail.get("owner") if isinstance(detail.get("owner"), dict) else {}
            owner = str(owner_data.get("display_name") or owner_data.get("id") or "")
            page = detail.get("items") or detail.get("tracks")
            if not isinstance(page, dict):
                page = _spotify_api_get(
                    f"playlists/{resource_id}/items",
                    token,
                    params={"limit": min(max_items, 50)},
                )

        total = int(page.get("total") or 0) if isinstance(page, dict) else 0
        tracks = []
        while isinstance(page, dict) and len(tracks) < max_items:
            for wrapper in _page_items(page):
                raw_track = (
                    wrapper if resource_type == "album" else _unwrap_playlist_track(wrapper)
                )
                normalized = _normalize_track(raw_track or {}, album_name=title)
                if normalized:
                    tracks.append(normalized)
                if len(tracks) >= max_items:
                    break
            next_url = page.get("next")
            if not next_url or len(tracks) >= max_items:
                break
            page = _spotify_api_get(next_url, token)

        return {
            "kind": resource_type,
            "id": resource_id,
            "title": title,
            "owner": owner,
            "tracks": tracks,
            "total": total or len(tracks),
            "truncated": (total or len(tracks)) > len(tracks),
            "spotify_url": url,
        }
    except SpotifyMetadataError as exc:
        return {
            "kind": resource_type,
            "id": resource_id,
            "error": exc.reason,
            "status_code": exc.status_code,
        }


def _search_text(value: str) -> str:
    value = (value or "").translate(str.maketrans({"ł": "l", "Ł": "L"}))
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.casefold()
    value = re.sub(
        r"\b(official|audio|video|lyrics?|visuali[sz]er|hd|hq|remaster(?:ed)?)\b",
        " ",
        value,
    )
    # ``str.isalnum`` keeps non-Latin alphabets (for example Cyrillic) while
    # still turning punctuation and underscores into token separators.
    return " ".join(
        "".join(char if char.isalnum() else " " for char in value).split()
    )


def _artist_search_variants(artist: str) -> list[str]:
    """Return normalized full and individual artist names for candidate scoring."""

    raw_values = [artist]
    raw_values.extend(
        re.split(
            r"\s*(?:,|&|/|\bfeat\.?\b|\bft\.?\b|\bx\b)\s*",
            artist,
            flags=re.IGNORECASE,
        )
    )
    variants: list[str] = []
    for value in raw_values:
        normalized = _search_text(value)
        if len(normalized) >= 2 and normalized not in variants:
            variants.append(normalized)
    return variants


def _track_search_queries(title: str, artist: str) -> list[str]:
    """Build strict-first YouTube queries with progressively broader fallbacks."""

    primary_artist = re.split(
        r"\s*(?:,|&|/|\bfeat\.?\b|\bft\.?\b|\bx\b)\s*",
        artist,
        maxsplit=1,
    )[0]
    candidates = [
        f"{artist} - {title} official audio".strip(" -"),
        f"{artist} - {title}".strip(" -"),
        f"{primary_artist} - {title}".strip(" -"),
        f"{title} {primary_artist}".strip(),
    ]
    queries: list[str] = []
    seen: set[str] = set()
    for query in candidates:
        key = query.casefold()
        if query and key not in seen:
            seen.add(key)
            queries.append(query)
    return queries


def _candidate_url(entry: dict) -> str:
    candidate = entry.get("webpage_url") or entry.get("original_url") or entry.get("url")
    if isinstance(candidate, str) and candidate.startswith("http"):
        return candidate
    video_id = entry.get("id") or candidate
    return f"https://www.youtube.com/watch?v={video_id}" if video_id else ""


def search_youtube_track_detailed(
    title: str,
    artist: str,
    duration_sec: int | None = None,
) -> YouTubeTrackSearchOutcome:
    """Find a song using strict-first queries and return failure diagnostics."""

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "noplaylist": True,
        "remote_components": YTDLP_REMOTE_COMPONENTS,
        "js_runtimes": YTDLP_JS_RUNTIMES,
    }

    target_title = _search_text(title)
    artist_variants = _artist_search_variants(artist)
    best_match = None
    best_score = 0.0
    search_errors: list[str] = []
    successful_searches = 0

    for query in _track_search_queries(title, artist):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                results = ydl.extract_info(f"ytsearch10:{query}", download=False)
            successful_searches += 1
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:160]}"
            search_errors.append(error)
            logging.warning("YouTube track search failed for query %r: %s", query, error)
            continue

        for entry in (results or {}).get("entries", []):
            if not isinstance(entry, dict):
                continue
            candidate_title_raw = str(entry.get("title") or "")
            channel_raw = str(entry.get("channel") or entry.get("uploader") or "")
            candidate_title = _search_text(candidate_title_raw)
            candidate_channel = _search_text(channel_raw.removesuffix(" - Topic"))
            if not candidate_title:
                continue

            title_score = SequenceMatcher(None, target_title, candidate_title).ratio()
            if target_title and target_title in candidate_title:
                title_score = 1.0

            artist_score = 0.0
            for target_artist in artist_variants:
                similarity = SequenceMatcher(
                    None,
                    target_artist,
                    candidate_channel,
                ).ratio()
                if target_artist in candidate_title or target_artist in candidate_channel:
                    similarity = 1.0
                artist_score = max(artist_score, similarity)

            candidate_duration = int(entry.get("duration") or 0)
            duration_score = 0.0
            if duration_sec and candidate_duration:
                difference = abs(duration_sec - candidate_duration)
                if difference <= 3:
                    duration_score = 1.0
                elif difference <= 10:
                    duration_score = 0.8
                elif difference <= 20:
                    duration_score = 0.4

            is_topic = channel_raw.casefold().endswith(" - topic")
            is_official_audio = "official audio" in candidate_title_raw.casefold()
            official_bonus = 0.18 if is_topic else (0.1 if is_official_audio else 0.0)

            penalty = 0.0
            disfavored = ("karaoke", "cover", "nightcore", "slowed", "sped up", "live")
            target_raw = f"{title} {artist}".casefold()
            candidate_raw = f"{candidate_title_raw} {channel_raw}".casefold()
            if any(word in candidate_raw and word not in target_raw for word in disfavored):
                penalty = 0.3

            score = title_score * 0.48 + artist_score * 0.27 + duration_score * 0.2
            score += official_bonus - penalty
            url = _candidate_url(entry)
            if url and score > best_score:
                best_score = score
                best_match = {
                    "url": url,
                    "title": candidate_title_raw,
                    "channel": channel_raw,
                    "duration": candidate_duration or None,
                    "score": score,
                    "source": "youtube_music" if is_topic or is_official_audio else "youtube",
                    "search_query": query,
                }

        if best_match and best_score >= _YOUTUBE_MATCH_THRESHOLD:
            logging.info(
                "YouTube Music match: %r by %s (score %.2f, query=%r)",
                best_match["title"],
                best_match["channel"],
                best_score,
                query,
            )
            return YouTubeTrackSearchOutcome(match=best_match)

    if best_match:
        detail = (
            f"Najlepszy wynik „{best_match['title']}” ({best_match['channel']}) "
            f"uzyskał {best_score:.2f}; wymagane co najmniej "
            f"{_YOUTUBE_MATCH_THRESHOLD:.2f}."
        )
        failure_code = "low_confidence"
    elif successful_searches:
        detail = "YouTube nie zwrócił wyników dla żadnego wariantu wyszukiwania."
        failure_code = "no_search_results"
    else:
        suffix = f" Ostatni błąd: {search_errors[-1]}" if search_errors else ""
        detail = f"Wyszukiwanie YouTube nie powiodło się.{suffix}"
        failure_code = "search_error"

    logging.info(
        "No YouTube track match for %r by %s: code=%s detail=%s",
        title,
        artist,
        failure_code,
        detail,
    )
    return YouTubeTrackSearchOutcome(
        match=None,
        failure_code=failure_code,
        failure_detail=detail,
    )


def search_youtube_track(
    title: str,
    artist: str,
    duration_sec: int | None = None,
) -> dict | None:
    """Find a matching song, preferring YouTube Music catalog uploads."""

    return search_youtube_track_detailed(title, artist, duration_sec).match


def resolve_spotify_track_info_detailed(track: dict) -> TrackResolutionOutcome:
    """Resolve track metadata and preserve an actionable matching failure."""

    search = search_youtube_track_detailed(
        track.get("title", ""),
        track.get("artist", ""),
        (track.get("duration_ms") or 0) // 1000 or None,
    )
    if not search.match:
        return TrackResolutionOutcome(
            resolved=None,
            failure_code=search.failure_code,
            failure_detail=search.failure_detail,
        )
    youtube = search.match
    return TrackResolutionOutcome(resolved={
        "source": youtube["source"],
        "youtube_url": youtube["url"],
        "title": track.get("title", youtube["title"]),
        "artist": track.get("artist", ""),
        "artists": track.get("artists", []),
        "album": track.get("album", ""),
        "duration": (track.get("duration_ms") or 0) // 1000 or youtube.get("duration"),
        "spotify_url": track.get("spotify_url", ""),
        "matched_title": youtube["title"],
        "matched_channel": youtube["channel"],
        "match_score": youtube["score"],
        "search_query": youtube.get("search_query", ""),
    })


def resolve_spotify_track_info(track: dict) -> dict | None:
    """Resolve normalized Spotify track metadata to a downloadable source."""

    return resolve_spotify_track_info_detailed(track).resolved


def resolve_spotify_track(url: str) -> dict | None:
    """Resolve one Spotify track URL through preferred YT Music matching."""

    track_id = parse_spotify_track_url(url)
    if not track_id:
        return None
    try:
        track = get_spotify_track_info(track_id)
    except SpotifyMetadataError as exc:
        return {"source": exc.reason, "track_id": track_id}
    if not track:
        return {"source": "no_credentials", "track_id": track_id}
    resolved = resolve_spotify_track_info(track)
    if resolved:
        return resolved
    return {
        "source": "not_found",
        "track_id": track_id,
        "title": track.get("title", ""),
        "artist": track.get("artist", ""),
    }


def get_spotify_episode_info(episode_id: str) -> dict | None:
    """Fetches episode metadata from Spotify Web API.

    Uses app credentials when configured, otherwise the Spotify cookie jar.
    Returns dict with title, show_name, duration_ms, description, or None.
    """
    token = _get_spotify_token("episode", episode_id)
    if not token:
        return None

    try:
        resp = requests.get(
            f'https://api.spotify.com/v1/episodes/{episode_id}',
            headers={'Authorization': f'Bearer {token}'},
            timeout=10,
        )
        if resp.status_code != 200:
            logging.error("Spotify API error: %s", resp.status_code)
            return None

        data = resp.json()
        return {
            'title': data.get('name', ''),
            'show_name': data.get('show', {}).get('name', ''),
            'duration_ms': data.get('duration_ms', 0),
            'description': data.get('description', ''),
            'release_date': data.get('release_date', ''),
            'language': data.get('language', ''),
        }

    except Exception as e:
        logging.error("Error fetching Spotify episode info: %s", e)
        return None


def _extract_title_from_url(url: str) -> str | None:
    """Extracts episode ID from Spotify URL for use in fallback messages.

    Spotify episode URLs use Base62 IDs (e.g. /episode/4rOoJ6Egrf8K2IrywzwOMk),
    not human-readable slugs — so this returns None (not useful as search query).
    The caller should use a generic fallback label instead.
    """
    # Spotify IDs are pure alphanumeric Base62, not readable titles
    # Return None to signal caller should use a generic label
    return None


def search_itunes_episode(title: str, show_name: str = "",
                          duration_sec: int | None = None) -> dict | None:
    """Searches iTunes API for a podcast episode with direct audio URL.

    Args:
        title: Episode title to search for.
        show_name: Podcast/show name (improves matching accuracy).
        duration_sec: Expected duration in seconds (for validation).

    Returns dict with audio_url, title, duration, show_name, or None.
    """
    try:
        query = f"{show_name} {title}".strip() if show_name else title
        resp = requests.get(
            'https://itunes.apple.com/search',
            params={
                'term': query,
                'entity': 'podcastEpisode',
                'limit': 10,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            logging.error("iTunes API error: %s", resp.status_code)
            return None

        results = resp.json().get('results', [])
        if not results:
            return None

        # Score each result for best match
        best_match = None
        best_score = 0.0

        for result in results:
            ep_title = result.get('trackName', '')
            ep_show = result.get('collectionName', '')
            ep_duration_ms = result.get('trackTimeMillis', 0)
            ep_audio_url = result.get('episodeUrl', '')

            if not ep_audio_url:
                continue

            # Title similarity (most important)
            title_score = SequenceMatcher(None, title.lower(), ep_title.lower()).ratio()

            # Show name similarity
            show_score = 0.0
            if show_name:
                show_score = SequenceMatcher(None, show_name.lower(), ep_show.lower()).ratio()

            # Duration match (bonus/penalty)
            duration_score = 0.0
            if duration_sec and ep_duration_ms:
                diff = abs(duration_sec - ep_duration_ms / 1000)
                if diff < 30:
                    duration_score = 1.0
                elif diff < 120:
                    duration_score = 0.5
                else:
                    duration_score = -0.3

            # Weighted score
            score = title_score * 0.5 + show_score * 0.3 + duration_score * 0.2

            if score > best_score:
                best_score = score
                best_match = {
                    'audio_url': ep_audio_url,
                    'title': ep_title,
                    'show_name': ep_show,
                    'duration': ep_duration_ms // 1000 if ep_duration_ms else None,
                    'score': score,
                }

        # Require minimum confidence
        if best_match and best_score >= 0.3:
            logging.info("iTunes match: '%s' (score: %.2f)", best_match['title'], best_score)
            return best_match

        return None

    except Exception as e:
        logging.error("Error searching iTunes: %s", e)
        return None


def search_youtube_episode(title: str, show_name: str = "",
                           duration_sec: int | None = None) -> dict | None:
    """Searches YouTube for a podcast episode via yt-dlp.

    Args:
        title: Episode title to search for.
        show_name: Podcast/show name.
        duration_sec: Expected duration in seconds.

    Returns dict with url, title, duration, channel, or None.
    """
    try:
        query = f"{show_name} {title}".strip() if show_name else title

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,
            'default_search': 'ytsearch5',
            'remote_components': YTDLP_REMOTE_COMPONENTS,
            'js_runtimes': YTDLP_JS_RUNTIMES,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            results = ydl.extract_info(query, download=False)

        entries = results.get('entries', []) if results else []
        if not entries:
            return None

        best_match = None
        best_score = 0.0

        for entry in entries:
            if not entry:
                continue
            yt_title = entry.get('title', '')
            yt_channel = entry.get('channel', '') or entry.get('uploader', '')
            yt_duration = entry.get('duration')
            yt_url = entry.get('url') or f"https://www.youtube.com/watch?v={entry.get('id', '')}"

            # Title similarity
            title_score = SequenceMatcher(None, title.lower(), yt_title.lower()).ratio()

            # Channel/show match
            show_score = 0.0
            if show_name:
                show_score = SequenceMatcher(None, show_name.lower(), yt_channel.lower()).ratio()

            # Duration match
            duration_score = 0.0
            if duration_sec and yt_duration:
                diff = abs(duration_sec - yt_duration)
                if diff < 60:
                    duration_score = 1.0
                elif diff < 300:
                    duration_score = 0.5

            score = title_score * 0.5 + show_score * 0.3 + duration_score * 0.2

            if score > best_score:
                best_score = score
                best_match = {
                    'url': yt_url,
                    'title': yt_title,
                    'channel': yt_channel,
                    'duration': yt_duration,
                    'score': score,
                }

        if best_match and best_score >= 0.3:
            logging.info("YouTube match: '%s' by %s (score: %.2f)",
                        best_match['title'], best_match['channel'], best_score)
            return best_match

        return None

    except Exception as e:
        logging.error("Error searching YouTube: %s", e)
        return None


def resolve_spotify_episode(url: str) -> dict | None:
    """Resolves a Spotify episode URL to downloadable audio info.

    Pipeline:
    1. Parse episode ID from URL
    2. Fetch metadata from Spotify API (optional, needs credentials)
    3. Search iTunes API for direct MP3 (priority)
    4. Fallback: search YouTube via yt-dlp

    Returns dict with source, audio_url/youtube_url, title, duration, show_name,
    or None if episode cannot be resolved.
    """
    episode_id = parse_spotify_episode_url(url)
    if not episode_id:
        return None

    # Phase 1: Get metadata (optional)
    spotify_info = get_spotify_episode_info(episode_id)

    if spotify_info:
        title = spotify_info['title']
        show_name = spotify_info['show_name']
        duration_sec = spotify_info['duration_ms'] // 1000 if spotify_info['duration_ms'] else None
    else:
        # Without Spotify API credentials we cannot get episode metadata
        # (Spotify pages are SPA, no server-side rendering of titles).
        # Return a special marker so the caller can show a clear error.
        logging.warning("Cannot resolve Spotify episode without API credentials")
        return {
            'source': 'no_credentials',
            'episode_id': episode_id,
        }

    # Phase 2a: Search iTunes (priority — direct MP3 URL)
    itunes = search_itunes_episode(title, show_name, duration_sec)
    if itunes:
        return {
            'source': 'itunes',
            'audio_url': itunes['audio_url'],
            'title': itunes['title'],
            'show_name': itunes['show_name'],
            'duration': itunes['duration'],
            'spotify_title': title,
            'spotify_show': show_name,
        }

    # Phase 2b: Fallback to YouTube search
    youtube = search_youtube_episode(title, show_name, duration_sec)
    if youtube:
        return {
            'source': 'youtube',
            'youtube_url': youtube['url'],
            'title': youtube['title'],
            'channel': youtube['channel'],
            'duration': youtube['duration'],
            'spotify_title': title,
            'spotify_show': show_name,
        }

    return None


def download_direct_audio(audio_url: str, output_path: str) -> str | None:
    """Downloads audio file directly from URL (e.g. iTunes MP3).

    Args:
        audio_url: Direct URL to audio file.
        output_path: Path to save the file (without extension).

    Returns path to downloaded file, or None on error.
    """
    try:
        # Timeout: 15s for connection, no limit for reading (large podcast files)
        resp = requests.get(audio_url, stream=True, timeout=(15, None))
        resp.raise_for_status()

        # Determine extension from content type or URL
        content_type = resp.headers.get('Content-Type', '')
        if 'mpeg' in content_type or audio_url.endswith('.mp3'):
            ext = '.mp3'
        elif 'mp4' in content_type or 'm4a' in content_type:
            ext = '.m4a'
        else:
            ext = '.mp3'

        file_path = f"{output_path}{ext}"

        with open(file_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        logging.info("Downloaded %s (%.1f MB)", file_path, file_size_mb)
        return file_path

    except Exception as e:
        logging.error("Error downloading audio from %s: %s", audio_url, e)
        return None
