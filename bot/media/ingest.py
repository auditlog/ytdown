"""Import existing supported media into a durable editing source."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import yt_dlp

from bot.config import COOKIES_FILE, YTDLP_JS_RUNTIMES, YTDLP_REMOTE_COMPONENTS
from bot.download_budget import DownloadBudget
from bot.media.network import fetch_public
from bot.media.store import MAX_SOURCE_BYTES, MediaError, MediaStore
from bot.media.timeline import Segment, load_timeline, subtitle_segments
from bot.security_policy import detect_platform, validate_url
from bot.services.audio_trim_service import _run, probe_duration
from bot.services.download_service import execute_download_plan, prepare_download_plan
from bot.services.spotify_service import (
    get_resolution_error_message,
    resolve_episode,
    resolve_track,
)
from bot.services.transcription_service import run_transcription_with_progress
from bot.spotify import parse_spotify_episode_url, parse_spotify_track_url


def _check_cancel(cancellation):
    if cancellation is not None and cancellation.event.is_set():
        raise MediaError("Operacja została zatrzymana.")


def _metadata(url: str) -> dict:
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 2,
        "remote_components": YTDLP_REMOTE_COMPONENTS,
        "js_runtimes": YTDLP_JS_RUNTIMES,
    }
    if Path(COOKIES_FILE).exists():
        options["cookiefile"] = COOKIES_FILE
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=False)
    if not info or info.get("_type", "video") != "video" or info.get("is_live"):
        raise MediaError("Zapisz pojedyncze zakończone nagranie.")
    if not 0 < float(info.get("duration") or 0) <= 7200:
        raise MediaError("Nagranie musi mieć znany czas trwania, do dwóch godzin.")
    return info


def _download(url, info, directory, video, cancellation):
    plan = prepare_download_plan(
        url=url,
        media_type="video" if video else "audio",
        format_choice="720p" if video else "mp3",
        chat_download_path=str(directory),
        info=info,
    )
    if plan is None:
        raise MediaError("Nie udało się przygotować pobierania.")
    plan.sanitized_title = "source"
    plan.output_path = str(directory / "source")
    plan.ydl_opts.update(outtmpl=str(directory / "source.%(ext)s"), max_filesize=MAX_SOURCE_BYTES)
    budget = DownloadBudget(str(directory), MAX_SOURCE_BYTES, 2 * 1024**3)

    def progress(event):
        _check_cancel(cancellation)
        budget.progress(event)

    plan.ydl_opts["progress_hooks"] = [progress]
    plan.ydl_opts["postprocessor_hooks"] = [budget.postprocess]
    result = execute_download_plan(plan)
    budget.check_result(result.file_path)
    return Path(result.file_path)


def _captions(url, info, directory) -> list:
    """Keep VTT/SRT cue timing; never turn subtitles into untimed plain text."""
    manual, automatic = info.get("subtitles") or {}, info.get("automatic_captions") or {}
    candidates = [
        (lang, auto)
        for auto, catalog in ((False, manual), (True, automatic))
        for lang in (info.get("language"), "pl", "en")
        if lang and lang in catalog
    ]
    if not candidates:
        return []
    language, auto = candidates[0]
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "writesubtitles": not auto,
        "writeautomaticsub": auto,
        "subtitleslangs": [language],
        "subtitlesformat": "vtt/srt",
        "outtmpl": str(directory / "captions.%(ext)s"),
        "socket_timeout": 20,
        "remote_components": YTDLP_REMOTE_COMPONENTS,
        "js_runtimes": YTDLP_JS_RUNTIMES,
    }
    if Path(COOKIES_FILE).exists():
        options["cookiefile"] = COOKIES_FILE
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            downloader.download([url])
        for path in directory.glob("captions.*"):
            if path.suffix in {".vtt", ".srt"} and path.stat().st_size <= 8 * 1024**2:
                segments = subtitle_segments(path.read_text(encoding="utf-8"))
                if segments:
                    return segments
    except Exception:
        return []
    return []


async def import_source(
    store: MediaStore,
    owner: int,
    *,
    url: str = "",
    local_path=None,
    title="Nagranie",
    video=False,
    cancellation=None,
    status=None,
    direct_audio=False,
    max_duration=7200,
) -> dict:
    async def report(text):
        if status:
            await status(text)
        _check_cancel(cancellation)

    if url and not direct_audio and not validate_url(url):
        raise MediaError("Wyślij link z obsługiwanej platformy.")
    if url:
        for source in await asyncio.to_thread(store.list, owner):
            if (
                source["url"] == url
                and source["segments"]
                and (not video or source["kind"] == "video")
            ):
                try:
                    store.media_path(owner, source["id"])
                    return source
                except MediaError:
                    pass
    identity, directory = await asyncio.to_thread(store.reserve, owner)
    committed = False
    try:
        effective_url, info, media = url, None, None
        await report("Przygotowywanie materiału i znaczników czasu...")
        if url and detect_platform(url) == "spotify":
            if parse_spotify_episode_url(url):
                resolved = await resolve_episode(url)
            elif parse_spotify_track_url(url):
                resolved = await resolve_track(url)
            else:
                raise MediaError("Zapisz pojedynczy odcinek lub utwór Spotify.")
            if get_resolution_error_message(resolved) or not resolved:
                raise MediaError("Nie udało się znaleźć nagrania Spotify.")
            if resolved.get("source") in {"youtube", "youtube_music"}:
                effective_url = resolved["youtube_url"]
                if not validate_url(effective_url):
                    raise MediaError("Nieprawidłowy adres nagrania Spotify.")
            elif resolved.get("source") == "itunes" and not video:
                media = directory / "source.mp3"
                await asyncio.to_thread(
                    fetch_public,
                    resolved["audio_url"],
                    max_bytes=MAX_SOURCE_BYTES,
                    destination=media,
                    cancellation=cancellation,
                )
                title = resolved.get("title") or title
            else:
                raise MediaError("Ten materiał Spotify nie udostępnia obsługiwanego pliku wideo.")
        if direct_audio:
            media = directory / "source.mp3"
            await asyncio.to_thread(
                fetch_public,
                url,
                max_bytes=MAX_SOURCE_BYTES,
                destination=media,
                cancellation=cancellation,
            )
        elif local_path:
            original = Path(local_path)
            if (
                original.is_symlink()
                or not original.is_file()
                or original.stat().st_size > MAX_SOURCE_BYTES
            ):
                raise MediaError("Przesłany plik wygasł lub przekracza limit 512 MB.")
            store.ensure_space(original.stat().st_size)
            media = directory / ("source" + original.suffix.lower())
            await asyncio.to_thread(shutil.copyfile, original, media)
        elif media is None:
            info = await asyncio.to_thread(_metadata, effective_url)
            if info["duration"] > max_duration:
                raise MediaError("Nagranie przekracza limit długości dla tej operacji.")
            title = info.get("title") or title
            media = await asyncio.to_thread(
                _download, effective_url, info, directory, video, cancellation
            )
        duration = await probe_duration(media)
        if not 0 < duration <= min(7200, max_duration):
            raise MediaError("Nagranie przekracza limit długości dla tej operacji.")
        segments = []
        if info:
            await report("Sprawdzanie napisów źródłowych...")
            segments = await asyncio.to_thread(_captions, effective_url, info, directory)
        provenance = "captions" if segments else "scribe"
        if not segments:
            await report("Transkrypcja AI ze znacznikami czasu...")
            audio_path = media
            if media.suffix.lower() != ".mp3":
                audio_path = directory / "transcription.mp3"
                code, _, _ = await _run(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-y",
                        "-i",
                        str(media),
                        "-vn",
                        "-c:a",
                        "libmp3lame",
                        "-b:a",
                        "128k",
                        str(audio_path),
                    ],
                    cancellation=cancellation,
                    timeout=900,
                )
                if code:
                    raise MediaError("Nie udało się przygotować audio do transkrypcji.")
            transcript = await run_transcription_with_progress(
                source_path=str(audio_path),
                output_dir=str(directory),
                executor=None,
                status_callback=report,
                cancellation=cancellation,
            )
            if not transcript:
                raise MediaError("Nie udało się przepisać nagrania.")
            segments = load_timeline(transcript)
            if transcript and Path(transcript).with_suffix(".timeline.json").exists():
                data = json.loads(Path(transcript).with_suffix(".timeline.json").read_text())
                if not data.get("complete"):
                    raise MediaError(
                        "Transkrypcja jest niepełna. Zapisz materiał ponownie po ustąpieniu błędu API."
                    )
        if not segments:
            raise MediaError("Źródło nie udostępniło znaczników czasu potrzebnych do montażu.")
        segments = [
            Segment(item.id, item.start, min(item.end, duration), item.text, item.speaker)
            for item in segments
            if item.start < duration
        ]
        await report("Zapisywanie materiału do montażu...")
        source = await asyncio.to_thread(
            store.commit,
            owner,
            identity,
            title=title,
            url=url,
            kind="video" if video else "audio",
            duration=duration,
            segments=segments,
            media_path=media,
            provenance=provenance,
        )
        committed = True
        # Raw parts and full word sidecars are not needed once sentence cues are retained.
        for path in directory.iterdir():
            if path.name not in {"source.json", source["media_file"]}:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
        return source
    finally:
        if not committed:
            store.delete(owner, identity)
