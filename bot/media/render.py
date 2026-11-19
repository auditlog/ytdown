"""Cancellable audio assembly and captioned 720p portrait clips."""

from __future__ import annotations

import logging
import math
from pathlib import Path

from bot.media.analysis import Selection
from bot.media.store import MediaError, MediaStore
from bot.media.timeline import caption_srt
from bot.services.audio_trim_service import _run, probe_duration

MAX_OUTPUT_BYTES = 49 * 1024**2


def _validate_pick(store, owner, pick, cancellation):
    if cancellation is not None and cancellation.event.is_set():
        raise MediaError("Operacja została zatrzymana.")
    source = store.get(owner, pick.source)
    if (
        not all(math.isfinite(value) for value in (pick.start, pick.end))
        or not 0 <= pick.start < pick.end <= source["duration"] + 0.05
    ):
        raise MediaError("Fragment wykracza poza nagranie źródłowe.")
    return source


async def assemble_audio(
    store: MediaStore, owner: int, picks: list[Selection], output: Path, *, cancellation=None
) -> Path:
    if not 1 <= len(picks) <= 12:
        raise MediaError("Montaż obsługuje od 1 do 12 fragmentów.")
    command = ["ffmpeg", "-v", "error", "-y"]
    filters = []
    for index, pick in enumerate(picks):
        _validate_pick(store, owner, pick, cancellation)
        path = store.media_path(owner, pick.source)
        command += ["-ss", str(pick.start), "-t", str(pick.end - pick.start), "-i", str(path)]
        filters.append(
            f"[{index}:a:0]aresample=44100,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS[a{index}]"
        )
    filters.append(
        "".join(f"[a{i}]" for i in range(len(picks))) + f"concat=n={len(picks)}:v=0:a=1[out]"
    )
    command += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[out]",
        "-vn",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "128k",
        "-threads",
        "2",
        str(output),
    ]
    code, _, stderr = await _run(command, cancellation=cancellation, timeout=900)
    if code or not output.exists() or output.stat().st_size > MAX_OUTPUT_BYTES:
        logging.warning(
            "Media audio render failed (%s): %s", code, stderr.decode(errors="replace")[-500:]
        )
        raise MediaError("Nie udało się zmontować audio w limicie Telegrama.")
    duration = await probe_duration(output)
    expected = sum(item.end - item.start for item in picks)
    if not math.isfinite(duration) or abs(duration - expected) > 1:
        raise MediaError("Długość montażu nie zgadza się z wybranymi fragmentami.")
    return output


async def render_short(
    store: MediaStore,
    owner: int,
    pick: Selection,
    output: Path,
    *,
    cancellation=None,
    captions=None,
) -> Path:
    if not 30 <= pick.end - pick.start <= 90:
        raise MediaError("Krótki film musi trwać od 30 do 90 sekund.")
    source = _validate_pick(store, owner, pick, cancellation)
    if source["kind"] != "video":
        raise MediaError("Do klipów potrzebny jest zapisany materiał wideo.")
    subtitle = output.with_suffix(".srt")
    subtitle.write_text(
        caption_srt(captions or list(pick.segments), pick.start, pick.end), encoding="utf-8"
    )
    # Generated filenames and numeric owner/source IDs only; never interpolate titles.
    escaped = str(subtitle.resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-ss",
        str(pick.start),
        "-t",
        str(pick.end - pick.start),
        "-i",
        str(store.media_path(owner, pick.source)),
        "-vf",
        "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1,"
        + f"subtitles=filename='{escaped}':force_style='FontSize=20,Outline=2,MarginV=70'",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-threads",
        "2",
        "-r",
        "25",
        "-b:v",
        "1500k",
        "-maxrate",
        "1800k",
        "-bufsize",
        "3600k",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-movflags",
        "+faststart",
        str(output),
    ]
    code, _, stderr = await _run(command, cancellation=cancellation, timeout=900)
    if code or not output.exists() or output.stat().st_size > MAX_OUTPUT_BYTES:
        logging.warning(
            "Media video render failed (%s): %s", code, stderr.decode(errors="replace")[-500:]
        )
        raise MediaError("Nie udało się wyrenderować klipu w limicie Telegrama.")
    duration = await probe_duration(output)
    if not math.isfinite(duration) or abs(duration - (pick.end - pick.start)) > 1:
        raise MediaError("Długość klipu nie zgadza się z wybranym fragmentem.")
    return output


def source_map(sources: list[dict], picks: list[Selection], *, separate=False) -> str:
    indexed = {source["id"]: source for source in sources}
    lines = ["# Mapa źródeł", ""]
    cursor = 0.0
    for index, pick in enumerate(picks, 1):
        source = indexed[pick.source]
        if separate:
            cursor = 0.0
        label = f"Klip {index}" if separate else "Montaż"
        lines += [
            f"## Fragment {index}: {source['title']}",
            f"{label}: {cursor:.1f}–{cursor + pick.end - pick.start:.1f} s",
            f"Źródło: {source['url'] or 'przesłany plik'}",
            f"Czas w źródle: {pick.start:.1f}–{pick.end:.1f} s",
            pick.reason,
            "",
            " ".join(segment.text for segment in pick.segments),
            "",
        ]
        cursor += pick.end - pick.start
    return "\n".join(lines)
