"""
Transcription module for YouTube Downloader Telegram Bot.

Handles audio transcription via Groq API and summarization via Claude API.
"""

import os
import re
import math
import shutil
import logging
import subprocess
import requests

from mutagen.mp3 import MP3

from bot.config import CONFIG
from bot.security import MAX_MP3_PART_SIZE_MB


def get_api_key():
    """Returns Groq API key from configuration."""
    return CONFIG["GROQ_API_KEY"]


def get_claude_api_key():
    """Returns Claude API key from configuration."""
    return CONFIG["CLAUDE_API_KEY"]


def find_silence_points(file_path, num_parts, min_duration=0.5):
    """
    Finds silence points in MP3 file using ffmpeg silencedetect filter.

    Args:
        file_path: Path to audio file
        num_parts: Expected number of parts
        min_duration: Minimum silence duration

    Returns:
        list: List of timestamps (in seconds) where silence was detected
    """
    silence_points = []

    try:
        cmd = [
            "ffmpeg", "-i", file_path,
            "-af", f"silencedetect=noise=-30dB:d={min_duration}",
            "-f", "null", "-"
        ]

        result = subprocess.run(cmd, stderr=subprocess.PIPE, text=True)
        output = result.stderr

        for line in output.splitlines():
            if "silence_end" in line:
                parts = line.split()
                for i, part in enumerate(parts):
                    if part == "silence_end:":
                        timestamp = float(parts[i+1])
                        silence_points.append(timestamp)

        silence_points.sort()

    except (subprocess.SubprocessError, ValueError, IndexError) as e:
        logging.error(f"Error finding silence points: {e}")

    return silence_points


def split_mp3(file_path, output_dir, max_size_mb=MAX_MP3_PART_SIZE_MB):
    """
    Splits MP3 file into multiple parts, each not exceeding max_size_mb.
    Tries to split at silence points when possible.

    Args:
        file_path: Path to MP3 file
        output_dir: Output directory for parts
        max_size_mb: Maximum size per part in MB

    Returns:
        list: List of paths to created part files
    """
    file_size = os.path.getsize(file_path) / (1024 * 1024)

    if file_size <= max_size_mb:
        logging.info(f"{file_path} is already smaller than {max_size_mb}MB. No splitting required.")
        output_path = os.path.join(output_dir, os.path.basename(file_path))
        shutil.copy(file_path, output_path)
        return [output_path]

    num_parts = math.ceil(file_size / max_size_mb)
    logging.info(f"File size: {file_size:.2f}MB. Splitting into {num_parts} parts...")

    # Get MP3 duration using mutagen
    try:
        audio = MP3(file_path)
        total_duration = audio.info.length
    except Exception as e:
        logging.error(f"Error getting duration from mutagen: {e}")
        total_duration = (file_size * 8 * 1024) / 128  # Assuming 128 kbps
        logging.info(f"Using estimated duration: {total_duration:.2f} seconds")

    ideal_part_duration = total_duration / num_parts

    # Try to find silence points
    silence_points = []
    try:
        logging.info("Analyzing audio for optimal split points...")
        silence_points = find_silence_points(file_path, num_parts)
    except Exception as e:
        logging.error(f"Error finding silence points: {e}")

    # Choose good split points based on silence points
    split_points = []

    if silence_points:
        ideal_splits = [ideal_part_duration * i for i in range(1, num_parts)]

        for ideal_time in ideal_splits:
            closest = min(silence_points, key=lambda x: abs(x - ideal_time))

            if abs(closest - ideal_time) < (ideal_part_duration * 0.2):
                split_points.append(closest)
            else:
                split_points.append(ideal_time)
    else:
        split_points = [ideal_part_duration * i for i in range(1, num_parts)]

    all_points = [0] + split_points + [total_duration]
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    output_files = []

    for i in range(len(all_points) - 1):
        start_time = all_points[i]
        end_time = all_points[i+1]
        duration = end_time - start_time

        output_path = os.path.join(output_dir, f"{base_name}_part{i+1}.mp3")
        output_files.append(output_path)

        try:
            cmd = [
                "ffmpeg", "-y", "-i", file_path,
                "-ss", str(start_time), "-t", str(duration),
                "-acodec", "copy", output_path
            ]

            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            part_size = os.path.getsize(output_path) / (1024 * 1024)
            logging.info(f"Created {output_path} ({part_size:.2f}MB, {duration:.2f} seconds)")

            # Warn if part is still too large
            if part_size > max_size_mb:
                logging.warning(f"Part {i+1} is larger than {max_size_mb}MB: {part_size:.2f}MB")

        except subprocess.SubprocessError as e:
            logging.error(f"Error creating part {i+1}: {e}")

    # Filter out any parts that don't exist or are empty
    output_files = [f for f in output_files if os.path.exists(f) and os.path.getsize(f) > 0]

    return output_files


def transcribe_audio(file_path, api_key):
    """
    Transcribes audio file using Groq API.

    Args:
        file_path: Path to audio file
        api_key: Groq API key

    Returns:
        str: Transcription text or empty string on error
    """
    # Check file exists and size
    if not os.path.exists(file_path):
        logging.error(f"File does not exist: {file_path}")
        return ""

    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
    logging.info(f"Transcribing file: {file_path} ({file_size_mb:.2f} MB)")

    if file_size_mb > 25:
        logging.error(f"File too large for Groq API: {file_size_mb:.2f} MB (max 25 MB)")
        return ""

    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    headers = {
        "Authorization": f"Bearer {api_key}"
    }

    try:
        with open(file_path, "rb") as audio_file:
            # Use filename with .mp3 extension to ensure correct MIME type
            filename = os.path.basename(file_path)
            if not filename.lower().endswith('.mp3'):
                filename = filename.rsplit('.', 1)[0] + '.mp3'

            files = {
                "file": (filename, audio_file.read(), "audio/mpeg")
            }
            data = {
                "model": "whisper-large-v3",
                "response_format": "text"
            }

            response = requests.post(url, headers=headers, files=files, data=data, timeout=300)

            if response.status_code == 200:
                result = response.text.strip()
                if result:
                    logging.debug(f"Transcription received: {len(result)} characters")
                    return result
                else:
                    logging.warning("API returned empty transcription")
                    return ""
            else:
                logging.error(f"Groq API error: {response.status_code}")
                logging.error(f"Response: {response.text}")
                return ""
    except Exception as e:
        logging.error(f"Error during transcription: {e}")
        return ""


def get_part_number(filename):
    """
    Extracts part number from filename.

    Args:
        filename: Filename to parse

    Returns:
        int: Part number or 0 if not found
    """
    match = re.search(r'part(\d+)', filename)
    if match:
        return int(match.group(1))
    return 0


def transcribe_mp3_file(file_path, output_dir, progress_callback=None):
    """
    Transcribes MP3 file, splitting into smaller parts if necessary.

    Args:
        file_path: Path to MP3 file
        output_dir: Output directory for transcription
        progress_callback: Optional async callback function(status_text) for progress updates

    Returns:
        str or None: Path to transcription file or None on error
    """
    import time as time_module

    api_key = get_api_key()
    if not api_key:
        logging.error("Cannot read API key from api_key.md.")
        return None

    temp_dir = os.path.join(output_dir, "temp_parts")
    os.makedirs(temp_dir, exist_ok=True)

    # Get original file size
    original_size_mb = os.path.getsize(file_path) / (1024 * 1024)

    if progress_callback:
        progress_callback(f"Dzielenie pliku ({original_size_mb:.1f} MB) na części...")

    part_files = split_mp3(file_path, temp_dir)
    part_files.sort(key=lambda x: get_part_number(os.path.basename(x)))

    transcriptions = []
    total_parts = len(part_files)
    total_characters = 0
    start_time = time_module.time()
    logging.info(f"Found {total_parts} part files to transcribe.")

    base_name = os.path.splitext(os.path.basename(file_path))[0]

    for i, part_path in enumerate(part_files):
        part_num = i + 1
        part_size_mb = os.path.getsize(part_path) / (1024 * 1024)
        logging.info(f"Transcribing file {part_num}/{total_parts}: {part_path}")

        # Calculate estimated time remaining
        elapsed = time_module.time() - start_time
        if i > 0:
            avg_time_per_part = elapsed / i
            remaining_parts = total_parts - i
            eta_seconds = int(avg_time_per_part * remaining_parts)
            eta_str = f"{eta_seconds // 60}m {eta_seconds % 60}s" if eta_seconds >= 60 else f"{eta_seconds}s"
        else:
            eta_str = "obliczanie..."

        # Report progress via callback with details
        if progress_callback:
            progress_callback(
                f"Transkrypcja części {part_num}/{total_parts}\n"
                f"Rozmiar części: {part_size_mb:.1f} MB\n"
                f"Przetworzone znaki: {total_characters:,}\n"
                f"Pozostały czas: ~{eta_str}"
            )

        transcription = transcribe_audio(part_path, api_key)

        if transcription:
            logging.info(f"Part {i+1}: transcription has {len(transcription)} characters")
            transcriptions.append(transcription)
            total_characters += len(transcription)
        else:
            logging.warning(f"Part {i+1}: transcription is empty!")
            transcriptions.append("[No transcription for this part]")

        part_num = get_part_number(os.path.basename(part_path)) or (i + 1)
        transcript_path = os.path.join(output_dir, f"{base_name}_part{part_num}_transcript.txt")

        with open(transcript_path, "w", encoding="utf-8") as f:
            f.write(transcription if transcription else "[Transcription error]")

        logging.info(f"Saved transcription for part {part_num} ({len(transcription) if transcription else 0} characters)")

    # Final progress update
    if progress_callback:
        elapsed_total = time_module.time() - start_time
        elapsed_str = f"{int(elapsed_total // 60)}m {int(elapsed_total % 60)}s" if elapsed_total >= 60 else f"{int(elapsed_total)}s"
        progress_callback(
            f"Łączenie transkrypcji...\n"
            f"Części: {total_parts}\n"
            f"Łącznie znaków: {total_characters:,}\n"
            f"Czas transkrypcji: {elapsed_str}"
        )

    valid_transcriptions = [t for t in transcriptions if t and t.strip()]
    combined_text = "\n\n".join(valid_transcriptions)

    logging.info(f"Combined transcriptions: {len(valid_transcriptions)} non-empty out of {len(transcriptions)} parts")
    logging.info(f"Final text length: {len(combined_text)} characters")

    if not combined_text or not combined_text.strip():
        logging.error("ERROR: No transcription content to save!")
        logging.error(f"All part transcriptions: {transcriptions}")

        transcript_md_path = os.path.join(output_dir, f"{base_name}_transcript.md")
        with open(transcript_md_path, "w", encoding="utf-8") as f:
            f.write(f"# {base_name} Transcript\n\n")
            f.write("**Error during transcription**\n\n")
            f.write("Could not generate transcription for this audio file.\n")
            f.write("Possible reasons:\n")
            f.write("- Audio file is corrupted or incompatible\n")
            f.write("- Groq API (Whisper) error\n")
            f.write("- No clear speech in recording\n\n")
            f.write("Try again with a different file or contact administrator.")

        return transcript_md_path

    transcript_md_path = os.path.join(output_dir, f"{base_name}_transcript.md")
    with open(transcript_md_path, "w", encoding="utf-8") as f:
        f.write(f"# {base_name} Transcript\n\n")
        f.write(combined_text)

    logging.info(f"All transcriptions combined and saved to {transcript_md_path}")

    try:
        shutil.rmtree(temp_dir)
    except Exception as e:
        logging.error(f"Error removing temporary directory: {e}")

    return transcript_md_path


def generate_summary(transcript_text, summary_type):
    """
    Generates summary of transcription using Claude API (Haiku).

    Args:
        transcript_text: Transcription text
        summary_type: Type of summary (1-4)

    Returns:
        str or None: Summary text or None on error
    """
    api_key = get_claude_api_key()
    if not api_key:
        logging.error("Cannot read Claude API key from api_key.md.")
        return None

    prompts = {
        1: "Napisz krótkie podsumowanie następującego tekstu:",
        2: "Napisz szczegółowe i rozbudowane podsumowanie następującego tekstu:",
        3: "Przygotuj podsumowanie w formie punktów (bullet points) następującego tekstu:",
        4: "Przygotuj podział zadań na osoby na podstawie następującego tekstu:"
    }

    selected_prompt = prompts.get(summary_type, prompts[1])

    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }

    data = {
        "model": "claude-haiku-4-5",
        "max_tokens": 16384,
        "messages": [
            {
                "role": "user",
                "content": f"{selected_prompt}\n\n{transcript_text}"
            }
        ]
    }

    try:
        response = requests.post(url, headers=headers, json=data, timeout=120)

        if response.status_code == 200:
            result = response.json()

            summary = ""
            if "content" in result:
                for content_item in result["content"]:
                    if content_item.get("type") == "text":
                        summary += content_item.get("text", "")

            return summary
        else:
            logging.error(f"Claude API error: {response.status_code}")
            logging.error(response.text)
            return None
    except Exception as e:
        logging.error(f"Error generating summary: {e}")
        return None
