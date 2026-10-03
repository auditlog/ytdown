"""Audio chunking helpers for transcription pipelines."""

from __future__ import annotations

import logging
import math
import os
import re
import shutil
import subprocess

from mutagen.mp3 import MP3

from bot.security_limits import FFMPEG_TIMEOUT, FFMPEG_TRANSCODE_TIMEOUT, MAX_MP3_PART_SIZE_MB


def find_silence_points(file_path, num_parts, min_duration=0.5, *, subprocess_module=subprocess):
    """Find silence points in an MP3 file using ffmpeg silencedetect."""

    silence_points = []
    try:
        cmd = [
            "ffmpeg", "-i", file_path,
            "-af", f"silencedetect=noise=-30dB:d={min_duration}",
            "-f", "null", "-",
        ]
        result = subprocess_module.run(cmd, stderr=subprocess.PIPE, text=True, timeout=300)
        output = result.stderr

        for line in output.splitlines():
            if "silence_end" in line:
                parts = line.split()
                for index, part in enumerate(parts):
                    if part == "silence_end:":
                        silence_points.append(float(parts[index + 1]))

        silence_points.sort()
    except (subprocess.SubprocessError, ValueError, IndexError) as e:
        logging.error("Error finding silence points: %s", e)

    return silence_points


def get_part_number(filename):
    """Extract the numeric part suffix from a chunk filename."""

    match = re.search(r'part(\d+)', filename)
    if match:
        return int(match.group(1))
    return 0


def transcode_to_mp3(file_path, output_dir, *, subprocess_module=subprocess, timeout=FFMPEG_TRANSCODE_TIMEOUT):
    """Re-encode any audio to constant-bitrate MP3 in output_dir; None on failure.

    128 kbps CBR keeps speech intelligible, gives part sizes that follow the
    duration, and lets mutagen read an exact length for the split points.
    """

    base_name = os.path.splitext(os.path.basename(file_path))[0]
    output_path = os.path.join(output_dir, f"{base_name}.mp3")
    cmd = [
        "ffmpeg", "-y", "-i", file_path,
        "-vn", "-c:a", "libmp3lame", "-b:a", "128k",
        output_path,
    ]
    logging.info("Converting %s to MP3 before splitting", file_path)
    try:
        result = subprocess_module.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.SubprocessError as e:
        logging.error("Error converting %s to MP3: %s", file_path, e)
        return None
    if result.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        stderr = result.stderr.decode("utf-8", errors="replace")[-300:] if result.stderr else ""
        logging.error("ffmpeg could not convert %s to MP3 (exit %s): %s", file_path, result.returncode, stderr)
        return None
    return output_path


def split_mp3(
    file_path,
    output_dir,
    max_size_mb=MAX_MP3_PART_SIZE_MB,
    *,
    mp3_factory=MP3,
    subprocess_module=subprocess,
    find_silence_points_fn=find_silence_points,
    ffmpeg_timeout=FFMPEG_TIMEOUT,
):
    """Split an MP3 into chunks that fit provider upload limits."""

    file_size = os.path.getsize(file_path) / (1024 * 1024)
    if file_size <= max_size_mb:
        logging.info("%s is already smaller than %sMB. No splitting required.", file_path, max_size_mb)
        output_path = os.path.join(output_dir, os.path.basename(file_path))
        shutil.copy(file_path, output_path)
        return [output_path]

    if not file_path.lower().endswith(".mp3"):
        # Parts below are cut with stream copy into .mp3 files and timed with
        # mutagen's MP3 reader; both need MP3 input. ffmpeg refuses to copy
        # e.g. AAC (Spotify .m4a) into an .mp3 container and leaves 0-byte parts.
        file_path = transcode_to_mp3(
            file_path,
            output_dir,
            subprocess_module=subprocess_module,
            timeout=FFMPEG_TRANSCODE_TIMEOUT,
        )
        if file_path is None:
            return []
        file_size = os.path.getsize(file_path) / (1024 * 1024)
        if file_size <= max_size_mb:
            return [file_path]

    num_parts = math.ceil(file_size / max_size_mb)
    logging.info("File size: %.2fMB. Splitting into %s parts...", file_size, num_parts)

    try:
        audio = mp3_factory(file_path)
        total_duration = audio.info.length
    except Exception as e:
        logging.error("Error getting duration from mutagen: %s", e)
        total_duration = (file_size * 8 * 1024) / 128
        logging.info("Using estimated duration: %.2f seconds", total_duration)

    ideal_part_duration = total_duration / num_parts

    silence_points = []
    try:
        logging.info("Analyzing audio for optimal split points...")
        silence_points = find_silence_points_fn(file_path, num_parts)
    except Exception as e:
        logging.error("Error finding silence points: %s", e)

    split_points = []
    if silence_points:
        ideal_splits = [ideal_part_duration * i for i in range(1, num_parts)]
        for ideal_time in ideal_splits:
            closest = min(silence_points, key=lambda value: abs(value - ideal_time))
            if abs(closest - ideal_time) < (ideal_part_duration * 0.2):
                split_points.append(closest)
            else:
                split_points.append(ideal_time)
    else:
        split_points = [ideal_part_duration * i for i in range(1, num_parts)]

    all_points = [0] + split_points + [total_duration]
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    output_files = []

    for index in range(len(all_points) - 1):
        start_time = all_points[index]
        end_time = all_points[index + 1]
        duration = end_time - start_time

        output_path = os.path.join(output_dir, f"{base_name}_part{index + 1}.mp3")
        output_files.append(output_path)

        try:
            cmd = [
                "ffmpeg", "-y", "-i", file_path,
                "-ss", str(start_time), "-t", str(duration),
                "-acodec", "copy", output_path,
            ]
            subprocess_module.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=ffmpeg_timeout)

            part_size = os.path.getsize(output_path) / (1024 * 1024)
            logging.info("Created %s (%.2fMB, %.2f seconds)", output_path, part_size, duration)
            if part_size > max_size_mb:
                logging.warning("Part %s is larger than %sMB: %.2fMB", index + 1, max_size_mb, part_size)
        except subprocess.SubprocessError as e:
            logging.error("Error creating part %s: %s", index + 1, e)

    return [path for path in output_files if os.path.exists(path) and os.path.getsize(path) > 0]
