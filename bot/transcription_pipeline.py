"""Pipeline orchestration for MP3 transcription workflows."""

from __future__ import annotations

import logging
import os
import shutil
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.jobs import JobCancellation

from bot.speakers import SpeakerPart, save_speaker_parts
from bot.transcription_chunking import get_part_number, split_mp3
from bot.transcription_providers import SpeakerTurn, get_api_key, transcribe_audio


# User-visible placeholder for a part the API returned no text for; written
# both into the merged transcript and into the per-part file.
EMPTY_PART_MARKER = "[brak transkrypcji tego fragmentu]"


def transcribe_mp3_file(
    file_path,
    output_dir,
    progress_callback=None,
    language=None,
    *,
    cancellation: "JobCancellation | None" = None,
    get_api_key_fn=get_api_key,
    split_mp3_fn=split_mp3,
    get_part_number_fn=get_part_number,
    transcribe_audio_fn=transcribe_audio,
    rmtree_fn=shutil.rmtree,
):
    """Transcribe an MP3 file part by part and save the merged transcript.

    Speaker turns of every part go to a data file next to the transcript
    (bot/speakers.py) for the optional speaker naming step. There is no AI
    correction: Scribe's text is used as returned, minus sound tags.

    When ``cancellation.event`` becomes set between chunks, processing
    stops and the function returns None (no transcription text).
    """

    api_key = get_api_key_fn()
    if not api_key:
        logging.error("Cannot read OPENROUTER_API_KEY from configuration.")
        return None

    temp_dir = os.path.join(output_dir, "temp_parts")
    os.makedirs(temp_dir, exist_ok=True)

    original_size_mb = os.path.getsize(file_path) / (1024 * 1024)
    if progress_callback:
        progress_callback(f"Dzielenie pliku ({original_size_mb:.1f} MB) na części...")

    part_files = split_mp3_fn(file_path, temp_dir)
    part_files.sort(key=lambda path: get_part_number_fn(os.path.basename(path)))
    if not part_files:
        logging.error("No audio parts could be produced from %s", file_path)
        _remove_temp_dir(rmtree_fn, temp_dir)
        return None

    transcriptions = []
    speaker_parts = []
    total_parts = len(part_files)
    failed_parts = 0
    total_characters = 0
    start_time = time.time()
    base_name = os.path.splitext(os.path.basename(file_path))[0]

    logging.info("Found %s part files to transcribe.", total_parts)

    for index, part_path in enumerate(part_files):
        if cancellation is not None and cancellation.event.is_set():
            logging.info("Transcription cancelled before part %s", index + 1)
            return None

        part_num = index + 1
        part_size_mb = os.path.getsize(part_path) / (1024 * 1024)
        logging.info("Transcribing file %s/%s: %s", part_num, total_parts, part_path)

        elapsed = time.time() - start_time
        if index > 0:
            avg_time_per_part = elapsed / index
            remaining_parts = total_parts - index
            eta_seconds = int(avg_time_per_part * remaining_parts)
            eta_str = f"{eta_seconds // 60}m {eta_seconds % 60}s" if eta_seconds >= 60 else f"{eta_seconds}s"
        else:
            eta_str = "obliczanie..."

        if progress_callback:
            progress_callback(
                f"Transkrypcja części {part_num}/{total_parts}\n"
                f"Rozmiar części: {part_size_mb:.1f} MB\n"
                f"Przetworzone znaki: {total_characters:,}\n"
                f"Pozostały czas: ~{eta_str}"
            )

        # No context from the previous part: OpenRouter ignores a prompt.
        result = transcribe_audio_fn(part_path, api_key, language=language)
        transcription = result.text.strip() if result else ""
        output_part_num = get_part_number_fn(os.path.basename(part_path)) or (index + 1)

        if transcription:
            logging.info("Part %s: transcription has %s characters", index + 1, len(transcription))
            transcriptions.append(transcription)
            total_characters += len(transcription)
            # A part without speaker numbers keeps its text as one unnamed turn.
            turns = tuple(result.turns) or (SpeakerTurn(None, 0.0, 0.0, transcription),)
            speaker_parts.append(SpeakerPart(part=output_part_num, turns=turns))
        else:
            logging.warning("Part %s: transcription is empty!", index + 1)
            transcriptions.append(EMPTY_PART_MARKER)
            failed_parts += 1
            # The labelled transcript shows the same gap as the plain one.
            speaker_parts.append(SpeakerPart(
                part=output_part_num, turns=(SpeakerTurn(None, 0.0, 0.0, EMPTY_PART_MARKER),),
            ))

        transcript_path = os.path.join(output_dir, f"{base_name}_part{output_part_num}_transcript.txt")
        with open(transcript_path, "w", encoding="utf-8") as file_obj:
            file_obj.write(transcription if transcription else EMPTY_PART_MARKER)

        logging.info(
            "Saved transcription for part %s (%s characters)",
            output_part_num,
            len(transcription) if transcription else 0,
        )

    if progress_callback:
        elapsed_total = time.time() - start_time
        elapsed_str = f"{int(elapsed_total // 60)}m {int(elapsed_total % 60)}s" if elapsed_total >= 60 else f"{int(elapsed_total)}s"
        failed_line = f"Części bez transkrypcji: {failed_parts} z {total_parts}\n" if failed_parts else ""
        progress_callback(
            f"Łączenie transkrypcji...\n"
            f"Części: {total_parts}\n"
            f"{failed_line}"
            f"Łącznie znaków: {total_characters:,}\n"
            f"Czas transkrypcji: {elapsed_str}"
        )

    # Placeholders keep their place in the merged text so a partial result
    # shows where audio is missing, but they are not content on their own.
    valid_transcriptions = [text for text in transcriptions if text and text.strip()]
    combined_text = "\n\n".join(valid_transcriptions)

    logging.info("Combined transcriptions: %s non-empty out of %s parts", len(valid_transcriptions), len(transcriptions))
    logging.info("Final text length: %s characters", len(combined_text))

    if not any(text != EMPTY_PART_MARKER for text in valid_transcriptions):
        # Nothing was transcribed (e.g. every API call failed). Returning a file
        # of placeholders would be shown as a finished transcript and could be
        # sent on to paid summaries; callers report TRANSCRIPTION_FAILED_TEXT
        # instead (bot/services/transcription_service.py).
        logging.error(
            "No transcription content to save: %s of %s parts failed", failed_parts, total_parts
        )
        _remove_temp_dir(rmtree_fn, temp_dir)
        return None

    transcript_md_path = os.path.join(output_dir, f"{base_name}_transcript.md")
    with open(transcript_md_path, "w", encoding="utf-8") as file_obj:
        file_obj.write(f"# {base_name} Transcript\n\n")
        file_obj.write(combined_text)

    logging.info("All transcriptions combined and saved to %s", transcript_md_path)

    try:
        save_speaker_parts(transcript_md_path, speaker_parts)
    except OSError as exc:
        # Speaker naming is optional; the transcript itself is complete.
        logging.error("Could not save speaker data: %s", exc)

    _remove_temp_dir(rmtree_fn, temp_dir)
    return transcript_md_path


def _remove_temp_dir(rmtree_fn, temp_dir):
    """Remove the chunk directory; a leftover is only disk clutter, so just log."""

    try:
        rmtree_fn(temp_dir)
    except Exception as e:
        logging.error("Error removing temporary directory: %s", e)
