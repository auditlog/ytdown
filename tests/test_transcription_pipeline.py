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
        post_process_transcript_fn=lambda text: f"CLEAN:{text}",
        estimate_token_count_fn=lambda text: len(text),
        is_text_too_long_for_correction_fn=lambda _text: False,
        rmtree_fn=lambda path: removed.append(path),
    )

    assert result == str(tmp_path / "audio_transcript.md")
    assert Path(result).exists()
    assert "CLEAN:" in Path(result).read_text(encoding="utf-8")
    assert removed == [str(tmp_path / "temp_parts")]
