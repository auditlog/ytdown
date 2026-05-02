"""Low-level wrapper around the system 7z binary.

This module knows nothing about Telegram, sessions, or downloads. It only
shells out to 7z, normalizes its output, and exposes a few naming/sizing
helpers used by bot.services.archive_service.
"""

from __future__ import annotations

import logging
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path

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
