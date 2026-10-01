"""Prepare bounded media artifacts and reusable 7z volumes inside a job directory."""

from __future__ import annotations

import asyncio
from pathlib import Path

from bot.archive import is_7z_available, pack_to_volumes
from bot.mcp.policy import UserError


def media_artifacts(media: Path, request: dict) -> dict:
    """Keep small media intact; replace large media with complete 7z volumes."""
    size = media.stat().st_size
    if size > request["max_media_bytes"]:
        raise UserError("Plik przekracza limit rozmiaru pojedynczego pliku MCP.")
    volume_bytes = request["archive_volume_mb"] * 1024**2
    if size <= volume_bytes:
        return {"artifacts": [media.name]}
    if not is_7z_available():
        raise UserError("Duży plik wymaga 7z. Administrator musi zainstalować 7z na serwerze.")
    try:
        volumes = asyncio.run(pack_to_volumes(
            [media], media.parent / "media", request["archive_volume_mb"],
            min_free_bytes=request["min_free_bytes"],
        ))
    except OSError as exc:
        raise UserError("Nie udało się uruchomić pakowania 7z.") from exc
    except ValueError as exc:
        raise UserError("Za mało miejsca na przygotowanie archiwum 7z.") from exc
    if not volumes or any(
        p.parent != media.parent or p.is_symlink() or not p.is_file()
        or not 0 < p.stat().st_size <= volume_bytes
        for p in volumes
    ):
        raise UserError("Pakowanie nie utworzyło kompletnych części archiwum.")
    instructions = media.parent / "rozpakowanie.txt"
    instructions.write_text(
        "Pobierz wszystkie części .7z.001, .7z.002 itd. do jednego folderu.\n"
        "Otwórz część .7z.001 w programie 7-Zip i wybierz Wypakuj.\n"
        "Wymagane są wszystkie części; nie rozpakowuj każdej oddzielnie.\n"
        f"Oryginalny plik: {media.name} ({size} bajtów).\n"
        "Pakowanie nie zmienia jakości filmu ani audio.\n",
        encoding="utf-8",
    )
    result = {
        "artifacts": [p.name for p in volumes] + [instructions.name],
        "archive": {
            "format": "7z", "original_name": media.name, "original_size_bytes": size,
            "volume_size_bytes": volume_bytes, "parts": [p.name for p in volumes],
        },
    }
    media.unlink()
    return result
