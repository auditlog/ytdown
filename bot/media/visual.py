"""Ordered gallery analysis and explicitly sampled video frames."""

from __future__ import annotations

import asyncio
from pathlib import Path

from bot.media.analysis import analyze_images
from bot.media.network import fetch_public
from bot.media.store import MediaError, MediaStore
from bot.services.audio_trim_service import _run


def visual_markdown(title: str, url: str, result: dict, timestamps=None) -> str:
    lines = [f"# {title}", "", f"Źródło: {url or 'przesłany plik'}", "", result["summary"], ""]
    if timestamps:
        lines += [
            "Analiza dotyczy 12 próbek obrazu z nagrania; krótkie zdarzenia mogą być pominięte.",
            "",
        ]
    for slide in result["slides"]:
        label = f"Strona {slide['page']}"
        if timestamps:
            label += f" — {timestamps[slide['page'] - 1]:.1f} s"
        lines += [f"## {label}", str(slide.get("text", "")), str(slide.get("description", "")), ""]
    lines += ["## Wnioski ze wskazaniem źródeł", ""]
    for finding in result["findings"]:
        lines.append(f"- {finding['text']} [strony: {', '.join(map(str, finding['pages']))}]")
    return "\n".join(lines)


async def analyze_gallery(
    store: MediaStore, owner: int, *, gallery: dict, url: str, query: str, cancellation=None
) -> tuple[dict, Path]:
    photos = gallery.get("photos") or []
    if not 1 <= len(photos) <= 20:
        raise MediaError(
            "Jedna analiza obsługuje maksymalnie 20 zdjęć. Nie pominę pozostałych stron."
        )
    identity, directory = store.reserve(owner)
    success = False
    try:
        paths = []
        for index, photo in enumerate(photos, 1):
            if cancellation is not None and cancellation.event.is_set():
                raise MediaError("Operacja została zatrzymana.")
            photo_url = photo.get("url")
            if not photo_url:
                raise MediaError("Brak adresu jednego ze zdjęć. Wyślij link ponownie.")
            store.ensure_space(20 * 1024**2)
            path = directory / f"page_{index:03d}.image"
            await asyncio.to_thread(
                fetch_public,
                photo_url,
                max_bytes=20 * 1024**2,
                destination=path,
                cancellation=cancellation,
            )
            paths.append(path)
        result = await asyncio.to_thread(analyze_images, paths, query)
        if cancellation is not None and cancellation.event.is_set():
            raise MediaError("Operacja została zatrzymana.")
        source = store.commit(
            owner,
            identity,
            title=gallery.get("title") or "Analiza zdjęć",
            url=url,
            kind="gallery",
            duration=0,
            segments=[],
            analysis=result,
        )
        output = directory / "analysis.md"
        output.write_text(visual_markdown(source["title"], url, result), encoding="utf-8")
        success = True
        return source, output
    finally:
        if success:
            for path in directory.glob("*.image"):
                path.unlink(missing_ok=True)
        else:
            store.delete(owner, identity)


async def analyze_video_frames(
    store: MediaStore, owner: int, source: dict, workspace: Path, query: str, *, cancellation=None
) -> Path:
    if source["kind"] != "video":
        raise MediaError("Wybierz zapisany materiał wideo albo galerię zdjęć.")
    duration = source["duration"]
    timestamps = [duration * (index + 0.5) / 12 for index in range(12)]
    paths = []
    for index, timestamp in enumerate(timestamps):
        path = workspace / f"frame_{index:02d}.jpg"
        code, _, _ = await _run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-ss",
                str(timestamp),
                "-i",
                str(store.media_path(owner, source["id"])),
                "-frames:v",
                "1",
                "-vf",
                "scale=1600:1600:force_original_aspect_ratio=decrease",
                "-threads",
                "2",
                str(path),
            ],
            cancellation=cancellation,
            timeout=60,
        )
        if code or not path.is_file():
            raise MediaError("Nie udało się odczytać próbki obrazu z nagrania.")
        paths.append(path)
    result = await asyncio.to_thread(analyze_images, paths, query)
    output = workspace / "analysis.md"
    output.write_text(
        visual_markdown(source["title"], source["url"], result, timestamps), encoding="utf-8"
    )
    return output
