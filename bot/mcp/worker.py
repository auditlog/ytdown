"""One disposable process per MCP job, reusing the existing media services."""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

from bot.mcp.policy import UserError, youtube_url


def execute(request: dict, directory: Path) -> dict:
    import yt_dlp

    from bot.config import (
        COOKIES_FILE,
        YTDLP_JS_RUNTIMES,
        YTDLP_REMOTE_COMPONENTS,
        get_runtime_value,
    )
    from bot.services.download_service import execute_download_plan, prepare_download_plan
    from bot.transcription_limits import is_text_too_long_for_summary
    from bot.transcription_pipeline import transcribe_mp3_file
    from bot.transcription_providers import generate_summary

    url = youtube_url(request["url"])
    operation = request["operation"]
    cookies = COOKIES_FILE if request["use_cookies"] else None
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 2,
        "extractor_retries": 2,
        "js_runtimes": YTDLP_JS_RUNTIMES,
        "remote_components": YTDLP_REMOTE_COMPONENTS,
    }
    if cookies and Path(cookies).is_file():
        options["cookiefile"] = cookies
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info or info.get("_type", "video") != "video":
        raise UserError("Nie udało się odczytać informacji o filmie.")
    duration = info.get("duration") or 0
    if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming"}:
        raise UserError("Transmisje na żywo nie są obsługiwane.")
    result = {
        "title": str(info.get("title", ""))[:500],
        "duration_seconds": duration,
        "url": url,
        "artifacts": [],
    }
    if operation == "info":
        return result
    if duration <= 0 or duration > request["max_duration_seconds"]:
        raise UserError("Film przekracza limit długości lub nie ma znanego czasu trwania.")
    groq_key = os.environ.get("GROQ_API_KEY") or get_runtime_value("GROQ_API_KEY", "")
    claude_key = os.environ.get("CLAUDE_API_KEY") or get_runtime_value("CLAUDE_API_KEY", "")
    if operation in {"transcribe", "summarize"} and not groq_key:
        raise UserError("Brak GROQ_API_KEY w konfiguracji serwera MCP.")
    if operation == "summarize" and not claude_key:
        raise UserError("Brak CLAUDE_API_KEY w konfiguracji serwera MCP.")

    media_type = "video" if operation == "video" else "audio"
    plan = prepare_download_plan(
        url=url,
        media_type=media_type,
        format_choice=request.get("quality", "best") if media_type == "video" else "mp3",
        chat_download_path=str(directory),
        info=info,
        cookies_file=cookies,
    )
    # A fixed basename prevents upstream titles from becoming filesystem paths.
    plan.sanitized_title = "media"
    plan.output_path = str(directory / "media")
    plan.ydl_opts.update(options)
    plan.ydl_opts.update(
        {
            "outtmpl": str(directory / "media.%(ext)s"),
            "max_filesize": request["max_media_bytes"],
        }
    )

    def check_size(progress):
        if progress.get("downloaded_bytes", 0) > request["max_media_bytes"]:
            raise UserError("Przekroczono limit rozmiaru pliku.")

    plan.ydl_opts["progress_hooks"] = [check_size]
    media = Path(execute_download_plan(plan).file_path)
    if media.stat().st_size > request["max_media_bytes"]:
        raise UserError("Plik przekracza limit rozmiaru pojedynczego pliku MCP.")
    if media.suffix not in {".mp3", ".mp4", ".mkv", ".webm"}:
        raise UserError("Pobieranie nie utworzyło kompletnego pliku multimedialnego.")
    if operation in {"audio", "video"}:
        from bot.mcp.artifacts import media_artifacts

        result.update(media_artifacts(media, request))
        return result

    # Fail on missing audio/transcript chunks instead of reporting placeholder text as success.
    from bot.transcription_chunking import split_mp3
    from bot.transcription_providers import transcribe_audio

    def split_checked(*args):
        paths = split_mp3(*args)
        if not paths:
            raise UserError("Nie udało się przygotować audio do transkrypcji.")
        return paths

    def transcribe_checked(*args, **kwargs):
        text = transcribe_audio(*args, **kwargs)
        if not text:
            raise UserError("Nie udało się uzyskać kompletnej transkrypcji.")
        return text

    transcript = transcribe_mp3_file(
        str(media),
        str(directory),
        language=request.get("language"),
        get_api_key_fn=lambda: groq_key,
        # The caller can summarize the returned transcript without a second model bill.
        get_claude_api_key_fn=lambda: "",
        split_mp3_fn=split_checked,
        transcribe_audio_fn=transcribe_checked,
    )
    if not transcript:
        raise UserError("Nie udało się utworzyć transkrypcji.")
    transcript_path = Path(transcript)
    result["artifacts"].append(transcript_path.name)
    if operation == "summarize":
        text = transcript_path.read_text(encoding="utf-8")
        if is_text_too_long_for_summary(text):
            raise UserError("Transkrypcja przekracza limit podsumowania.")
        summary = generate_summary(text, request["summary_type"], api_key=claude_key)
        if not summary:
            raise UserError("Nie udało się wygenerować podsumowania.")
        (directory / "summary.md").write_text(summary, encoding="utf-8")
        result["artifacts"].append("summary.md")
    # Retain only complete, advertised outputs, not audio or partial transcripts.
    for path in directory.iterdir():
        if path.is_file() and path.name not in {*result["artifacts"], "job.json", "job.tmp"}:
            path.unlink()
    return result


def main():
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    parent_pid = int(sys.argv[2])

    def watch_parent():
        while os.getppid() == parent_pid:
            time.sleep(0.5)
        os.killpg(os.getpgrp(), signal.SIGKILL)

    threading.Thread(target=watch_parent, daemon=True).start()
    directory = Path(sys.argv[1]).resolve()
    request = json.load(sys.stdin)
    try:
        result = {"result": execute(request, directory)}
    except Exception as exc:
        logging.error("MCP worker failed: %s", type(exc).__name__)
        # Only our own validation errors are safe to send across the MCP boundary.
        safe_error = (
            str(exc)
            if isinstance(exc, UserError)
            else "Zadanie nie powiodło się. Sprawdź logi serwera."
        )
        result = {"error": safe_error}
    (directory / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
