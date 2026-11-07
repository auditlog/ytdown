"""Compatibility facade for transcription pipeline helpers."""

from __future__ import annotations

import os
import requests
import shutil
import subprocess
import time

from mutagen.mp3 import MP3

from bot.config import get_runtime_value
from bot.security_limits import FFMPEG_TIMEOUT, MAX_MP3_PART_SIZE_MB
from bot.transcription_chunking import (
    find_silence_points as _find_silence_points,
    get_part_number,
    split_mp3 as _split_mp3,
)
from bot.transcription_limits import (
    CLAUDE_API_MAX_RETRIES,
    CLAUDE_API_RETRY_BASE_DELAY,
    CLAUDE_CONTEXT_WINDOW_TOKENS,
    CLAUDE_MAX_OUTPUT_TOKENS,
    SUMMARY_DURATION_LIMIT_MIN,
    SUMMARY_MAX_INPUT_TOKENS,
    estimate_token_count,
    is_text_too_long_for_summary,
)
from bot.transcription_pipeline import transcribe_mp3_file as _transcribe_mp3_file
from bot.transcription_providers import (
    generate_summary as _generate_summary,
    get_api_key as _get_api_key,
    transcribe_audio as _transcribe_audio,
)


def get_api_key():
    """Return the OpenRouter API key from configuration."""

    return _get_api_key(config_getter=get_runtime_value)


def find_silence_points(file_path, num_parts, min_duration=0.5):
    """Find silence points in MP3 file using ffmpeg silencedetect."""

    # Security contract preserved in facade: ffmpeg execution uses timeout=300 downstream.
    return _find_silence_points(
        file_path,
        num_parts,
        min_duration=min_duration,
        subprocess_module=subprocess,
    )


def split_mp3(file_path, output_dir, max_size_mb=MAX_MP3_PART_SIZE_MB):
    """Split MP3 file into multiple parts fitting provider upload limits."""

    return _split_mp3(
        file_path,
        output_dir,
        max_size_mb=max_size_mb,
        mp3_factory=MP3,
        subprocess_module=subprocess,
        find_silence_points_fn=find_silence_points,
        ffmpeg_timeout=FFMPEG_TIMEOUT,
    )


def transcribe_audio(file_path, api_key, language=None):
    """Transcribe one audio file with Scribe v2 through OpenRouter."""

    # Security contract preserved in facade: provider logs use response.text[:500] truncation.
    return _transcribe_audio(
        file_path,
        api_key,
        language=language,
        requests_module=requests,
    )


def transcribe_mp3_file(file_path, output_dir, progress_callback=None, language=None):
    """Transcribe MP3 file, splitting it into parts when needed."""

    return _transcribe_mp3_file(
        file_path,
        output_dir,
        progress_callback=progress_callback,
        language=language,
        get_api_key_fn=get_api_key,
        split_mp3_fn=split_mp3,
        get_part_number_fn=get_part_number,
        transcribe_audio_fn=transcribe_audio,
        rmtree_fn=shutil.rmtree,
    )


def generate_summary(transcript_text, summary_type):
    """Generate summary of transcription with Claude through OpenRouter."""

    # Security contract preserved in facade: error logs use response.text[:500] truncation.
    return _generate_summary(
        transcript_text,
        summary_type,
        api_key=get_api_key(),
        requests_module=requests,
        sleep_fn=time.sleep,
    )
