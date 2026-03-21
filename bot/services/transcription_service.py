"""Reusable transcription and summarization helpers for application flows."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Callable

from bot.transcription import (
    generate_summary,
    is_text_too_long_for_summary,
    transcribe_mp3_file,
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


SUMMARY_TYPE_NAMES = {
    1: "Krótkie podsumowanie",
    2: "Szczegółowe podsumowanie",
    3: "Podsumowanie w punktach",
    4: "Podział zadań na osoby",
}


async def run_transcription_with_progress(
    *,
    source_path: str,
    output_dir: str,
    executor: Any,
    status_callback: Callable[[str], Any],
) -> str | None:
    """Run the MP3 transcription pipeline and forward progress updates."""

    current_status = {"text": ""}

    def progress_callback(status_text: str) -> None:
        current_status["text"] = status_text

    loop = asyncio.get_event_loop()
    future = loop.run_in_executor(
        executor,
        lambda: transcribe_mp3_file(source_path, output_dir, progress_callback, language=None),
    )

    last_status = ""
    while not future.done():
        if current_status["text"] and current_status["text"] != last_status:
            last_status = current_status["text"]
            await status_callback(current_status["text"])
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
        lambda: generate_summary(transcript_text, summary_type),
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


def cleanup_transcription_artifacts(
    *,
    source_media_path: str,
    output_dir: str,
    transcript_prefix: str,
) -> None:
    """Remove original media and per-part transcript chunks after final delivery."""

    os.remove(source_media_path)
    for file_name in os.listdir(output_dir):
        if file_name.startswith(f"{transcript_prefix}_part") and file_name.endswith("_transcript.txt"):
            os.remove(os.path.join(output_dir, file_name))
