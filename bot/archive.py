"""Low-level wrapper around the system 7z binary.

This module knows nothing about Telegram, sessions, or downloads. It only
shells out to 7z, normalizes its output, and exposes a few naming/sizing
helpers used by bot.services.archive_service.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import unicodedata
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Awaitable

from bot.security_limits import BOTAPI_VOLUME_SIZE_MB, MTPROTO_VOLUME_SIZE_MB


def volume_size_for(use_mtproto: bool) -> int:
    """Volume size for 7z multi-volume archives, in MiB.

    MTProto allows ~2 GB per message, Bot API caps at 50 MB; we leave a
    margin in both cases so the resulting volume always slots in.
    """

    return MTPROTO_VOLUME_SIZE_MB if use_mtproto else BOTAPI_VOLUME_SIZE_MB


def transliterate_to_ascii(text: str) -> str:
    """Best-effort ASCII transliteration of Polish/diacritic characters.

    NFKD decomposes characters like 'ą' into 'a' + combining ogonek, and
    we drop the combining marks. The handful of Polish letters that NFKD
    does not decompose (notably 'ł' / 'Ł') are mapped explicitly.
    Used for naming 7z volumes shipped to a Windows host.
    """

    explicit_map = {
        "ł": "l",
        "Ł": "L",
    }
    translated = "".join(explicit_map.get(ch, ch) for ch in text)
    decomposed = unicodedata.normalize("NFKD", translated)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def compute_archive_basename(slug: str, ts: datetime) -> str:
    """Deterministic prefix for archive volumes: <slug>_<YYYYMMDD-HHMMSS>."""

    return f"{slug}_{ts.strftime('%Y%m%d-%H%M%S')}"


def is_7z_available() -> bool:
    """Return True when the 7z binary is present in PATH."""

    return shutil.which("7z") is not None


async def pack_to_volumes(
    sources: Sequence[Path],
    dest_basename: Path,
    volume_size_mb: int,
    *,
    progress_cb: Callable[[str], Awaitable[None]] | None = None,
) -> list[Path]:
    """Pack ``sources`` into a 7z multi-volume archive at ``dest_basename``.

    Resulting volumes are named ``<dest_basename>.7z.001``, ``.002`` etc.
    Returns the sorted list of created volume paths.

    The 7z process is invoked with ``-t7z -v<size>m -mx0 -mmt=on`` because:
    - the inputs are already-compressed media (mp3/mp4/m4a), so further
      compression buys nothing while costing significant CPU,
    - multi-threading speeds the I/O-bound packing on multi-core hosts.

    Raises:
        ValueError: when ``sources`` is empty.
        RuntimeError: when 7z exits with a non-zero status.
    """

    if not sources:
        raise ValueError("empty sources")

    archive_path = dest_basename.with_suffix(".7z")
    args = [
        "7z",
        "a",
        "-t7z",
        f"-v{volume_size_mb}m",
        "-mx0",
        "-mmt=on",
        str(archive_path),
        *[str(src) for src in sources],
    ]

    logging.info("Running 7z pack: %s", " ".join(args))
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    if progress_cb is not None:
        await _stream_7z_progress(process, progress_cb)

    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        err = stderr.decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"7z failed (exit {process.returncode}): {err}")

    parent = dest_basename.parent
    prefix = f"{archive_path.name}."
    volumes = sorted(
        p for p in parent.iterdir()
        if p.name.startswith(prefix) and p.name[len(prefix):].isdigit()
    )
    logging.info("7z packed %d volume(s) for %s", len(volumes), archive_path)
    return volumes


async def _stream_7z_progress(
    process: asyncio.subprocess.Process,
    progress_cb: Callable[[str], Awaitable[None]],
) -> None:
    """Throttle 7z stdout updates to one progress_cb call every 2 seconds."""

    if process.stdout is None:
        return

    last_update = 0.0
    loop = asyncio.get_event_loop()
    while True:
        line = await process.stdout.readline()
        if not line:
            break
        now = loop.time()
        if now - last_update < 2.0:
            continue
        decoded = line.decode("utf-8", errors="replace").strip()
        if decoded:
            try:
                await progress_cb(decoded)
            except Exception as exc:  # progress is best-effort
                logging.warning("Archive progress callback failed: %s", exc)
            last_update = now
