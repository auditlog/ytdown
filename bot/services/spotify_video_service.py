"""Spotify video application service built on bot.spotify_video helpers."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

import requests

from bot.config import SPOTIFY_COOKIES_FILE
from bot.downloader_validation import sanitize_filename
from bot.spotify import parse_spotify_episode_url
from bot.spotify_video import (
    Profile,
    SpotifyVideoError,
    build_track_urls,
    download_track,
    estimate_size_mb,
    fetch_embed_data,
    fetch_video_manifest,
    find_audio_profile_id,
    list_profiles,
    load_spotify_cookie,
    manifest_duration_ms,
    mux,
    subtitle_languages,
)


@dataclass(frozen=True)
class VideoEpisode:
    """A Spotify episode resolved to its downloadable video manifest."""

    episode_id: str
    title: str
    show_name: str
    duration_ms: int
    manifest: dict
    profiles: list[Profile]
    subtitle_languages: list[str]


_ERROR_MESSAGES = {
    "no_cookie": (
        "Brak pliku z ciasteczkami Spotify.\n\n"
        "Aby pobierać wideo, wyeksportuj ciasteczka z zalogowanej sesji "
        "open.spotify.com do pliku:\n"
        "spotify_cookies.txt (format Netscape)\n\n"
        "Potrzebne jest ciasteczko sp_dc."
    ),
    "expired_session": (
        "Sesja Spotify wygasła.\n\n"
        "Zaloguj się ponownie na open.spotify.com i wyeksportuj ciasteczka "
        "jeszcze raz do pliku spotify_cookies.txt."
    ),
    "api_changed": (
        "Spotify zmieniło swoje API — pobieranie wideo chwilowo nie działa.\n\n"
        "To nie jest problem z siecią ani z Twoim kontem. "
        "Zgłoś to administratorowi bota."
    ),
    "drm_protected": (
        "Ten odcinek jest chroniony DRM — pobranie wideo nie jest możliwe.\n\n"
        "Możesz pobrać sam dźwięk lub transkrypcję."
    ),
    "ffmpeg_missing": (
        "Brak programu ffmpeg na serwerze — nie mogę połączyć obrazu z dźwiękiem.\n\n"
        "Zgłoś to administratorowi bota."
    ),
    "mux_timeout": (
        "Łączenie obrazu z dźwiękiem (ffmpeg) przekroczyło limit czasu.\n\n"
        "Odcinek może być zbyt długi lub serwer jest chwilowo przeciążony. "
        "Spróbuj ponownie lub pobierz sam dźwięk zamiast wideo."
    ),
}


def get_video_error_message(reason: str) -> str:
    """Map a resolution failure code to a user-facing Polish message.

    ``reason`` is an exact-match lookup only: every SpotifyVideoError this
    pipeline raises for an actionable failure (including mux()'s own
    "mux_timeout") carries a bare reason code, never a descriptive sentence.
    A prior keyword/substring match here collided with unrelated failures
    (e.g. a requests read-timeout message from a stalled segment download
    also containing the word "timeout") and was removed for that reason.
    """

    return _ERROR_MESSAGES.get(
        reason,
        "Nie udało się przygotować wideo z tego odcinka Spotify.",
    )


def resolve_video_episode(
    url: str, *, cookies_file: str | None = None
) -> VideoEpisode | None:
    """Resolve a Spotify episode URL to its video manifest.

    Returns None when the URL is not an episode link or the episode simply has
    no video — both are ordinary outcomes that fall back to the audio flow.
    Raises SpotifyVideoError carrying a reason code when the user needs to act.
    """

    episode_id = parse_spotify_episode_url(url)
    if not episode_id:
        return None

    cookie = load_spotify_cookie(cookies_file or SPOTIFY_COOKIES_FILE)
    if not cookie:
        raise SpotifyVideoError("no_cookie")

    try:
        embed = fetch_embed_data(episode_id, cookie)
    except requests.RequestException as exc:
        logging.error("Spotify embed request failed: %s", exc)
        raise SpotifyVideoError("api_changed") from exc
    except SpotifyVideoError as exc:
        # fetch_embed_data raises SpotifyVideoError directly (not
        # requests.RequestException) when the __NEXT_DATA__ blob is missing
        # or malformed, i.e. Spotify changed the embed page shape. Normalize
        # to the bare reason code so get_video_error_message maps it to the
        # specific "API changed" text instead of silently falling back to
        # the generic message.
        logging.error("Spotify embed page shape changed: %s", exc)
        raise SpotifyVideoError("api_changed") from exc

    if not embed.has_video or not embed.manifest_id:
        return None

    # Enforced boundary: protected content is refused, never circumvented.
    if embed.requires_drm:
        raise SpotifyVideoError("drm_protected")

    if not embed.access_token:
        raise SpotifyVideoError("expired_session")

    try:
        manifest = fetch_video_manifest(embed.manifest_id, embed.access_token)
        # list_profiles / manifest_duration_ms / subtitle_languages all read
        # from the same manifest and can raise the same class of error (see
        # the SpotifyVideoError branch below), so they are built inside this
        # try as well rather than after it.
        video_episode = VideoEpisode(
            episode_id=episode_id,
            title=embed.title,
            show_name=embed.show_name,
            duration_ms=embed.duration_ms or manifest_duration_ms(manifest),
            manifest=manifest,
            profiles=list_profiles(manifest),
            subtitle_languages=subtitle_languages(manifest),
        )
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        raise SpotifyVideoError(
            "expired_session" if status in (401, 403) else "api_changed"
        ) from exc
    except requests.RequestException as exc:
        raise SpotifyVideoError("api_changed") from exc
    except SpotifyVideoError as exc:
        # fetch_video_manifest raises SpotifyVideoError directly (not
        # requests.HTTPError) when the v6 manifest endpoint 404s. list_profiles,
        # manifest_duration_ms and subtitle_languages all route through
        # _manifest_content, which raises the same way when the manifest is
        # missing a "contents" entry. Every one of these means the same
        # thing -- Spotify changed the manifest shape -- so all are
        # normalized to the bare reason code here.
        logging.error("Spotify manifest shape changed: %s", exc)
        raise SpotifyVideoError("api_changed") from exc

    return video_episode


def build_quality_options(episode: VideoEpisode) -> list[dict[str, Any]]:
    """Describe the selectable video qualities with estimated sizes."""

    return [
        {
            "height": profile.height,
            "profile_id": profile.id,
            "size_mb": estimate_size_mb(profile, episode.duration_ms),
        }
        for profile in episode.profiles
    ]


async def download_episode_media(
    *,
    episode: VideoEpisode,
    height: int | None,
    output_dir: str,
    executor: Any,
    progress_cb=None,
    cancellation=None,
) -> str:
    """Download an episode as muxed video, or as audio only when height is None.

    Returns the path to the finished file.
    """

    # download_track writes to output_dir before renaming into place; a
    # missing directory would otherwise surface as a raw, unwrapped OSError.
    os.makedirs(output_dir, exist_ok=True)

    loop = asyncio.get_event_loop()
    base_name = sanitize_filename(episode.title or "spotify_episode")
    audio_path = os.path.join(output_dir, f"{base_name}.audio.mp4")

    try:
        audio_profile_id = find_audio_profile_id(episode.manifest)
        audio_init, audio_segments = build_track_urls(episode.manifest, audio_profile_id)
    except SpotifyVideoError as exc:
        # find_audio_profile_id / build_track_urls raise a bare
        # SpotifyVideoError with a full sentence when the manifest is
        # missing an expected field (no AAC profile, no base_urls, no
        # segment_length) -- i.e. Spotify changed the manifest shape, the
        # same "api_changed" scenario handled in resolve_video_episode.
        # download_track and mux below are deliberately left outside this
        # try: their own SpotifyVideoErrors (ffmpeg_missing, timeouts,
        # segment-fetch failures) are a different category and must pass
        # through unchanged.
        logging.error("Spotify manifest shape changed during download: %s", exc)
        raise SpotifyVideoError("api_changed") from exc

    if height is None:
        final_audio = os.path.join(output_dir, f"{base_name}.m4a")
        await loop.run_in_executor(
            executor,
            lambda: download_track(
                audio_init, audio_segments, final_audio,
                progress_cb=progress_cb, cancellation=cancellation,
            ),
        )
        return final_audio

    profile = next((p for p in episode.profiles if p.height == height), None)
    if profile is None:
        raise SpotifyVideoError("api_changed")

    video_path = os.path.join(output_dir, f"{base_name}.video.mp4")
    try:
        video_init, video_segments = build_track_urls(episode.manifest, profile.id)
    except SpotifyVideoError as exc:
        logging.error("Spotify manifest shape changed during download: %s", exc)
        raise SpotifyVideoError("api_changed") from exc

    try:
        await loop.run_in_executor(
            executor,
            lambda: download_track(
                video_init, video_segments, video_path,
                progress_cb=progress_cb, cancellation=cancellation,
            ),
        )
        await loop.run_in_executor(
            executor,
            lambda: download_track(
                audio_init, audio_segments, audio_path, cancellation=cancellation
            ),
        )
        out_path = os.path.join(output_dir, f"{base_name}.mp4")
        try:
            await loop.run_in_executor(executor, lambda: mux(video_path, audio_path, out_path))
        except BaseException:
            # ffmpeg runs with -y and writes out_path incrementally, so a
            # timeout or crash mid-mux can leave a truncated file behind.
            # Same discipline as download_track's own failure path: remove
            # the half-written artefact, and never let a failed removal
            # mask the original SpotifyVideoError.
            if os.path.exists(out_path):
                try:
                    os.remove(out_path)
                except OSError:
                    pass
            raise
        return out_path
    finally:
        for temp_path in (video_path, audio_path):
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
