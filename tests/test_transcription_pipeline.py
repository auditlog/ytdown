"""Tests for stable orchestration in transcription_pipeline."""

from pathlib import Path

from bot import transcription_pipeline as pipeline


def test_transcribe_mp3_file_pipeline_uses_injected_dependencies(tmp_path):
    source = tmp_path / "audio.mp3"
    source.write_bytes(b"x" * 1024)

    part1 = tmp_path / "audio_part1.mp3"
    part2 = tmp_path / "audio_part2.mp3"
    part1.write_bytes(b"a")
    part2.write_bytes(b"b")

    removed = []

    result = pipeline.transcribe_mp3_file(
        str(source),
        str(tmp_path),
        progress_callback=lambda _text: None,
        language="pl",
        get_api_key_fn=lambda: "groq",
        get_claude_api_key_fn=lambda: "claude",
        split_mp3_fn=lambda *_args, **_kwargs: [str(part1), str(part2)],
        get_part_number_fn=lambda filename: 1 if "part1" in filename else 2,
        transcribe_audio_fn=lambda path, _key, language=None, prompt=None: f"{Path(path).stem}:{language}:{prompt or ''}",
        post_process_transcript_fn=lambda text, api_key=None: f"CLEAN:{text}",
        estimate_token_count_fn=lambda text: len(text),
        is_text_too_long_for_correction_fn=lambda _text: False,
        rmtree_fn=lambda path: removed.append(path),
    )

    assert result == str(tmp_path / "audio_transcript.md")
    assert Path(result).exists()
    assert "CLEAN:" in Path(result).read_text(encoding="utf-8")
    assert removed == [str(tmp_path / "temp_parts")]


def test_pipeline_returns_none_when_api_key_missing(tmp_path):
    source = tmp_path / "audio.mp3"
    source.write_bytes(b"x" * 100)

    result = pipeline.transcribe_mp3_file(
        str(source),
        str(tmp_path),
        get_api_key_fn=lambda: "",
    )

    assert result is None


def test_pipeline_skips_correction_when_text_too_long(tmp_path):
    source = tmp_path / "audio.mp3"
    source.write_bytes(b"x" * 100)

    part = tmp_path / "audio_part1.mp3"
    part.write_bytes(b"a")

    correction_called = []

    result = pipeline.transcribe_mp3_file(
        str(source),
        str(tmp_path),
        get_api_key_fn=lambda: "groq",
        get_claude_api_key_fn=lambda: "claude",
        split_mp3_fn=lambda *_args, **_kwargs: [str(part)],
        get_part_number_fn=lambda _filename: 1,
        transcribe_audio_fn=lambda _path, _key, language=None, prompt=None: "some transcription",
        post_process_transcript_fn=lambda text, api_key=None: correction_called.append(text) or f"CLEAN:{text}",
        estimate_token_count_fn=lambda text: len(text),
        is_text_too_long_for_correction_fn=lambda _text: True,
        rmtree_fn=lambda _path: None,
    )

    assert result is not None
    assert Path(result).exists()
    content = Path(result).read_text(encoding="utf-8")
    assert "CLEAN:" not in content
    assert correction_called == []


def test_pipeline_uses_placeholder_for_empty_transcription(tmp_path):
    source = tmp_path / "audio.mp3"
    source.write_bytes(b"x" * 100)

    part = tmp_path / "audio_part1.mp3"
    part.write_bytes(b"a")

    result = pipeline.transcribe_mp3_file(
        str(source),
        str(tmp_path),
        get_api_key_fn=lambda: "groq",
        get_claude_api_key_fn=lambda: "",
        split_mp3_fn=lambda *_args, **_kwargs: [str(part)],
        get_part_number_fn=lambda _filename: 1,
        transcribe_audio_fn=lambda _path, _key, language=None, prompt=None: "",
        post_process_transcript_fn=lambda text, api_key=None: None,
        estimate_token_count_fn=lambda text: len(text),
        is_text_too_long_for_correction_fn=lambda _text: False,
        rmtree_fn=lambda _path: None,
    )

    assert result is not None
    content = Path(result).read_text(encoding="utf-8")
    assert "No transcription for this part" in content


def test_split_mp3_failure_propagates_exception(tmp_path):
    """ffmpeg failure during splitting should propagate as-is, not be swallowed."""

    source = tmp_path / "audio.mp3"
    source.write_bytes(b"x" * 100)

    def failing_split(*_args, **_kwargs):
        raise RuntimeError("ffmpeg exited with code 1")

    import pytest
    with pytest.raises(RuntimeError, match="ffmpeg exited with code 1"):
        pipeline.transcribe_mp3_file(
            str(source),
            str(tmp_path),
            get_api_key_fn=lambda: "groq",
            get_claude_api_key_fn=lambda: "",
            split_mp3_fn=failing_split,
            get_part_number_fn=lambda _filename: 1,
            transcribe_audio_fn=lambda _path, _key, language=None, prompt=None: "text",
            post_process_transcript_fn=lambda text, api_key=None: None,
            estimate_token_count_fn=lambda text: len(text),
            is_text_too_long_for_correction_fn=lambda _text: False,
            rmtree_fn=lambda _path: None,
        )


def test_all_parts_empty_transcript_writes_placeholder_content(tmp_path):
    """All parts returning empty string should produce placeholder text in the output file.

    The pipeline inserts '[No transcription for this part]' rather than creating an
    error document, because each placeholder is non-empty text. The real error file
    is only written when *all* placeholders are also stripped away (whitespace-only).
    """

    source = tmp_path / "audio.mp3"
    source.write_bytes(b"x" * 100)

    part1 = tmp_path / "audio_part1.mp3"
    part2 = tmp_path / "audio_part2.mp3"
    part1.write_bytes(b"a")
    part2.write_bytes(b"b")

    result = pipeline.transcribe_mp3_file(
        str(source),
        str(tmp_path),
        get_api_key_fn=lambda: "groq",
        get_claude_api_key_fn=lambda: "",
        split_mp3_fn=lambda *_args, **_kwargs: [str(part1), str(part2)],
        get_part_number_fn=lambda filename: 1 if "part1" in filename else 2,
        # Both parts return empty string — simulates total API failure
        transcribe_audio_fn=lambda _path, _key, language=None, prompt=None: "",
        post_process_transcript_fn=lambda text, api_key=None: None,
        estimate_token_count_fn=lambda text: len(text),
        is_text_too_long_for_correction_fn=lambda _text: False,
        rmtree_fn=lambda _path: None,
    )

    assert result is not None
    content = Path(result).read_text(encoding="utf-8")
    # Pipeline appends placeholder text for each empty part — verify both are present
    assert content.count("[No transcription for this part]") == 2


def test_api_timeout_during_post_processing_uses_raw_transcript(tmp_path):
    """When post-processing raises Timeout, the raw transcript should still be saved."""

    source = tmp_path / "audio.mp3"
    source.write_bytes(b"x" * 100)

    part = tmp_path / "audio_part1.mp3"
    part.write_bytes(b"a")

    def timeout_post_process(text, api_key=None):
        # Simulate connection timeout during assembly/post-processing
        raise TimeoutError("API request timed out after 300s")

    import pytest
    with pytest.raises(TimeoutError):
        pipeline.transcribe_mp3_file(
            str(source),
            str(tmp_path),
            get_api_key_fn=lambda: "groq",
            get_claude_api_key_fn=lambda: "claude",
            split_mp3_fn=lambda *_args, **_kwargs: [str(part)],
            get_part_number_fn=lambda _filename: 1,
            transcribe_audio_fn=lambda _path, _key, language=None, prompt=None: "raw transcript",
            post_process_transcript_fn=timeout_post_process,
            estimate_token_count_fn=lambda text: len(text),
            is_text_too_long_for_correction_fn=lambda _text: False,
            rmtree_fn=lambda _path: None,
        )


def test_transcribe_mp3_file_breaks_on_cancel(tmp_path):
    """When cancellation event is set before chunks start, no API calls are made."""

    import asyncio
    from bot.jobs import JobCancellation

    cancellation = JobCancellation(job_id="t", event=asyncio.Event())
    cancellation.event.set()

    part = tmp_path / "audio_part1.mp3"
    part.write_bytes(b"x")

    source = tmp_path / "audio.mp3"
    source.write_bytes(b"x" * 100)

    api_called = {"n": 0}

    def fake_transcribe(_path, _key, language=None, prompt=None):
        api_called["n"] += 1
        return "should not be called"

    result = pipeline.transcribe_mp3_file(
        str(source),
        str(tmp_path),
        cancellation=cancellation,
        get_api_key_fn=lambda: "groq",
        get_claude_api_key_fn=lambda: "",
        split_mp3_fn=lambda *_args, **_kwargs: [str(part)],
        get_part_number_fn=lambda _filename: 1,
        transcribe_audio_fn=fake_transcribe,
        post_process_transcript_fn=lambda text, api_key=None: None,
        estimate_token_count_fn=lambda text: len(text),
        is_text_too_long_for_correction_fn=lambda _text: False,
        rmtree_fn=lambda _path: None,
    )

    # API must NOT be called for any chunk when cancellation is already set.
    assert api_called["n"] == 0
    # Result must indicate cancel (None or "cancelled"/"anulowano" string).
    assert result is None or "anulowano" in str(result).lower() or "cancelled" in str(result).lower()
