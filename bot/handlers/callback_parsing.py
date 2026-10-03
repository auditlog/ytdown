"""Pure parsing helpers for Telegram callback payloads."""

from __future__ import annotations

# Video heights the UI offers; anything else is a stale or forged callback.
# Imported rather than restated so the keyboard and this validator can
# never disagree about which buttons exist -- see bot/spotify_video.py.
from bot.spotify_video import SPOTIFY_VIDEO_HEIGHTS  # noqa: F401  (re-exported)


def parse_download_callback(data):
    """Parses download-related callback data.

    Expected formats:
      - dl_video_<format>
      - dl_audio_<codec>
      - dl_audio_format_<format_id>
    """
    if not isinstance(data, str):
        return None

    if not data.startswith("dl_"):
        return None

    parts = data.split("_")
    if len(parts) < 3:
        return None

    media_type = parts[1]
    if media_type not in {"audio", "video"}:
        return None

    if media_type == "audio":
        if len(parts) == 4 and parts[2] == "format":
            return {"media_type": "audio", "mode": "format_id", "format": parts[3]}
        if len(parts) == 3 and parts[2] != "format":
            return {"media_type": "audio", "mode": "codec", "format": parts[2]}
        return None

    if media_type == "video":
        if len(parts) == 3:
            return {"media_type": "video", "mode": "format_id", "format": parts[2]}
        return None

    return None


def parse_summary_option(option_data):
    """Parses summary option payloads.

    Expected format:
      - summary_option_<index>
      - audio_summary_option_<index>
    """
    if not isinstance(option_data, str):
        return None

    if (
        not option_data.startswith("summary_option_")
        and not option_data.startswith("audio_summary_option_")
    ):
        return None

    _, _, raw_value = option_data.rpartition("_")

    if not raw_value:
        return None

    try:
        summary_option = int(raw_value)
    except ValueError:
        return None

    if summary_option < 1 or summary_option > 4:
        return None

    return summary_option


def parse_spotify_video_callback(data):
    """Parses native Spotify video callback payloads.

    Expected formats:
      - spv_video_<height>p
      - spv_audio_m4a

    A dedicated prefix keeps the native Spotify path separate from the legacy
    dl_* flow, which routes through the iTunes/YouTube fallback.
    """
    if not isinstance(data, str) or not data.startswith("spv_"):
        return None

    if data == "spv_audio_m4a":
        return {"media_type": "audio", "height": None}

    parts = data.split("_")
    if len(parts) != 3 or parts[1] != "video":
        return None

    raw_height = parts[2]
    if not raw_height.endswith("p"):
        return None

    try:
        height = int(raw_height[:-1])
    except ValueError:
        return None

    if height not in SPOTIFY_VIDEO_HEIGHTS:
        return None

    return {"media_type": "video", "height": height}


# Callback prefixes whose handlers start real work (download, transcription,
# archive packing, ...). Only these are charged against the rate limit and,
# later, the per-chat concurrency guard. Navigation (menus, paging, back,
# cancel, stop_*, time_range*, formats, noop, ...) must never be blocked.
_WORK_CALLBACK_PREFIXES = (
    "dl_",
    "spv_",
    "thumbnail",
    "transcribe",  # also covers transcribe_summary
    "summary_option_",
    "audio_transcribe",  # also covers audio_transcribe_summary
    "audio_summary_option_",
    "sub_lang_",
    "sub_auto_",
    "sub_src_ai",
    "sub_sum_",
    "pl_dl_",
    "pl_zip_dl_",
    "spc_dl_",
    "arc_split_",
    "arc_resend_",
    "arc_pack_partial_",
)
_WORK_CALLBACK_EXACT = frozenset({"trim_dl"})
_SPOTIFY_PACK_SIZES = frozenset({"50", "100", "all"})
_SPOTIFY_PACK_FORMATS = frozenset({"mp3", "m4a"})


def is_work_callback(data) -> bool:
    """Return True when the callback payload starts work (not navigation).

    Pure helper shared by the rate limiter and the concurrency guard.
    Unknown or non-string payloads count as navigation.
    """
    if not isinstance(data, str):
        return False
    if data in _WORK_CALLBACK_EXACT or data.startswith(_WORK_CALLBACK_PREFIXES):
        return True
    # Spotify archive size choice: spc_pack_<mp3|m4a>_<50|100|all>.
    # The bare spc_pack_mp3 / spc_pack_m4a / spc_pack_back only open menus.
    parts = data.split("_")
    return (
        len(parts) == 4
        and parts[0] == "spc"
        and parts[1] == "pack"
        and parts[2] in _SPOTIFY_PACK_FORMATS
        and parts[3] in _SPOTIFY_PACK_SIZES
    )
