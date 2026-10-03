"""Reusable transcription and summarization helpers for application flows."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from bot.jobs import JobCancellation

from bot.services.download_service import send_progress_update
from bot.transcription_limits import is_text_too_long_for_summary
from bot.transcription_pipeline import transcribe_mp3_file
from bot.transcription_providers import (
    generate_custom_analysis,
    generate_summary,
    get_claude_api_key,
)


@dataclass
class TranscriptResult:
    """Materialized transcript file and derived display text."""

    transcript_path: str
    transcript_text: str
    display_text: str


@dataclass
class SummaryResult:
    """Generated summary text and the saved markdown artifact path."""

    summary_text: str
    summary_path: str
    summary_type_name: str


@dataclass
class CustomAnalysisResult:
    """User-directed transcript analysis and its persisted Markdown artifact."""

    analysis_text: str
    analysis_path: str


SUMMARY_TYPE_NAMES = {
    1: "Krótkie podsumowanie",
    2: "Szczegółowe podsumowanie",
    3: "Podsumowanie w punktach",
    4: "Podział zadań na osoby",
}


MISSING_GROQ_KEY_TEXT = (
    "Funkcja niedostępna — brak klucza API do transkrypcji. "
    "Skontaktuj się z administratorem."
)
MISSING_CLAUDE_KEY_TEXT = (
    "Podsumowanie jest niedostępne — brak klucza API Claude. "
    "Wybierz samą transkrypcję albo skontaktuj się z administratorem."
)
# Variants of MISSING_CLAUDE_KEY_TEXT for places where "pick transcript only"
# does not fit: the transcript is already on its way (summary skipped after
# transcription) or there is no summary choice at all (custom prompt).
MISSING_CLAUDE_KEY_KEEP_TRANSCRIPT_TEXT = (
    "Transkrypcja gotowa, ale podsumowanie jest niedostępne — brak klucza API Claude. "
    "Wysyłam samą transkrypcję."
)
MISSING_CLAUDE_KEY_ADMIN_TEXT = (
    "Funkcja niedostępna — brak klucza API Claude. "
    "Skontaktuj się z administratorem."
)
# Shown when the pipeline returns None (no part could be transcribed or the
# audio could not be cut into parts). See also: bot/transcription_pipeline.py.
TRANSCRIPTION_FAILED_TEXT = (
    "Nie udało się przepisać nagrania. Najczęstsze przyczyny: chwilowa awaria "
    "lub limit usługi rozpoznawania mowy, uszkodzony plik albo brak wyraźnej mowy "
    "w nagraniu. Spróbuj ponownie za kilka minut."
)
SUMMARY_FAILED_KEEP_TRANSCRIPT_TEXT = (
    "Transkrypcja gotowa, ale nie udało się wygenerować podsumowania. "
    "Wysyłam samą transkrypcję."
)


def missing_transcription_key_message(
    get_value: Callable[..., Any], *, summary: bool
) -> str | None:
    """Return the Polish error text when a required API key is missing.

    Called before any download or upload work starts so the user is not made
    to wait for a job that is bound to fail. ``get_value`` is the calling
    handler module's own ``get_runtime_value`` (kept injectable so the
    per-module test seams keep working).
    """
    if not get_value("GROQ_API_KEY", ""):
        return MISSING_GROQ_KEY_TEXT
    if summary and not get_value("CLAUDE_API_KEY", ""):
        return MISSING_CLAUDE_KEY_TEXT
    return None


async def run_transcription_with_progress(
    *,
    source_path: str,
    output_dir: str,
    executor: Any,
    status_callback: Callable[[str], Any],
    cancellation: "JobCancellation | None" = None,
) -> str | None:
    """Run the MP3 transcription pipeline and forward progress updates.

    When cancellation is provided, it propagates to transcribe_mp3_file
    so chunk-level polling can stop processing early.
    """

    current_status = {"text": ""}

    def progress_callback(status_text: str) -> None:
        current_status["text"] = status_text

    loop = asyncio.get_event_loop()
    future = loop.run_in_executor(
        executor,
        lambda: transcribe_mp3_file(
            source_path,
            output_dir,
            progress_callback,
            language=None,
            cancellation=cancellation,
        ),
    )

    last_status = ""
    while not future.done():
        if current_status["text"] and current_status["text"] != last_status:
            last_status = current_status["text"]
            await send_progress_update(status_callback, current_status["text"])
        await asyncio.sleep(2)

    return await future


def load_transcript_result(transcript_path: str) -> TranscriptResult:
    """Load transcript markdown and derive a headerless display text version."""

    with open(transcript_path, 'r', encoding='utf-8') as f:
        transcript_text = f.read()

    display_text = transcript_text
    if display_text.startswith('# '):
        lines = display_text.split('\n')
        for i in range(1, len(lines)):
            if lines[i].strip():
                display_text = '\n'.join(lines[i:])
                break

    return TranscriptResult(
        transcript_path=transcript_path,
        transcript_text=transcript_text,
        display_text=display_text,
    )


def transcript_too_long_for_summary(transcript_text: str) -> bool:
    """Check whether the transcript can be summarized by the configured AI model."""

    return is_text_too_long_for_summary(transcript_text)


async def generate_summary_artifact(
    *,
    transcript_text: str,
    summary_type: int,
    title: str,
    sanitized_title: str,
    output_dir: str,
    executor: Any,
) -> SummaryResult | None:
    """Generate an AI summary and persist it as a markdown file."""

    loop = asyncio.get_event_loop()
    summary_text = await loop.run_in_executor(
        executor,
        lambda: generate_summary(transcript_text, summary_type, api_key=get_claude_api_key()),
    )
    if not summary_text:
        return None

    summary_type_name = SUMMARY_TYPE_NAMES.get(summary_type, "Podsumowanie")
    summary_path = os.path.join(output_dir, f"{sanitized_title}_summary.md")
    with open(summary_path, 'w', encoding='utf-8') as f:
        f.write(f"# {title} - {summary_type_name}\n\n")
        f.write(summary_text)

    return SummaryResult(
        summary_text=summary_text,
        summary_path=summary_path,
        summary_type_name=summary_type_name,
    )


async def generate_custom_analysis_artifact(
    *,
    transcript_text: str,
    prompt: str,
    title: str,
    sanitized_title: str,
    output_dir: str,
    executor: Any,
    artifact_id: str | None = None,
    api_key: str | None = None,
) -> CustomAnalysisResult | None:
    """Apply a custom instruction and persist the complete result as Markdown."""

    loop = asyncio.get_event_loop()
    claude_api_key = api_key if api_key is not None else get_claude_api_key()
    analysis_text = await loop.run_in_executor(
        executor,
        lambda: generate_custom_analysis(
            transcript_text,
            prompt,
            api_key=claude_api_key,
        ),
    )
    if not analysis_text:
        return None

    suffix = artifact_id or datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_suffix = "".join(char for char in suffix if char.isalnum() or char in "-_")[:32]
    analysis_path = os.path.join(
        output_dir,
        f"{sanitized_title}_custom_analysis_{safe_suffix}.md",
    )
    with open(analysis_path, "w", encoding="utf-8") as file_obj:
        file_obj.write(f"# {title} - Własna analiza transkrypcji\n\n")
        file_obj.write(analysis_text)

    return CustomAnalysisResult(
        analysis_text=analysis_text,
        analysis_path=analysis_path,
    )


def cleanup_transcription_artifacts(
    *,
    source_media_path: str,
    output_dir: str,
    transcript_prefix: str,
) -> None:
    """Remove original media and per-part transcript chunks after final delivery."""

    try:
        os.remove(source_media_path)
    except OSError:
        pass
    for file_name in os.listdir(output_dir):
        if file_name.startswith(f"{transcript_prefix}_part") and file_name.endswith("_transcript.txt"):
            try:
                os.remove(os.path.join(output_dir, file_name))
            except OSError:
                pass


def save_transcript_markdown(
    *,
    title: str,
    transcript_text: str,
    sanitized_title: str,
    output_dir: str,
    dated: bool = False,
) -> str:
    """Persist transcript text as a markdown artifact and return its path."""

    file_name = f"{sanitized_title}_transcript.md"
    if dated:
        current_date = datetime.now().strftime("%Y-%m-%d")
        file_name = f"{current_date} {file_name}"

    transcript_path = os.path.join(output_dir, file_name)
    with open(transcript_path, 'w', encoding='utf-8') as f:
        f.write(f"# {title}\n\n")
        f.write(transcript_text)
    return transcript_path
