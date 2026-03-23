"""Pure parsing helpers for Telegram callback payloads."""

from __future__ import annotations


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
