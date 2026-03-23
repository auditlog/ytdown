"""Unit tests for bot.services.transcription_service."""

from pathlib import Path

from bot.services import transcription_service as ts


def test_load_transcript_result_strips_markdown_header(tmp_path):
    transcript = tmp_path / "sample_transcript.md"
    transcript.write_text("# Title\n\nLine one\nLine two\n", encoding="utf-8")

    result = ts.load_transcript_result(str(transcript))

    assert result.transcript_path == str(transcript)
    assert result.transcript_text.startswith("# Title")
    assert result.display_text == "Line one\nLine two\n"


def test_transcript_too_long_for_summary_delegates(monkeypatch):
    monkeypatch.setattr(ts, "is_text_too_long_for_summary", lambda text: text == "long")
    assert ts.transcript_too_long_for_summary("long") is True
    assert ts.transcript_too_long_for_summary("short") is False


async def _noop_status(_: str) -> None:
    return None


def test_run_transcription_with_progress_returns_transcript_path(monkeypatch, tmp_path):
    source = tmp_path / "audio.mp3"
    source.write_bytes(b"x")

    monkeypatch.setattr(
        ts,
        "transcribe_mp3_file",
        lambda source_path, output_dir, progress_callback, language=None: (
            progress_callback("step 1"),
            str(tmp_path / "audio_transcript.md"),
        )[1],
    )

    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    result = asyncio.run(
        ts.run_transcription_with_progress(
            source_path=str(source),
            output_dir=str(tmp_path),
            executor=ThreadPoolExecutor(max_workers=1),
            status_callback=_noop_status,
        )
    )

    assert result == str(tmp_path / "audio_transcript.md")


def test_generate_summary_artifact_creates_markdown_file(monkeypatch, tmp_path):
    monkeypatch.setattr(ts, "generate_summary", lambda text, summary_type, api_key=None: "summary body")
    monkeypatch.setattr(ts, "get_claude_api_key", lambda: "test-key")

    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    result = asyncio.run(
        ts.generate_summary_artifact(
            transcript_text="hello",
            summary_type=2,
            title="Sample",
            sanitized_title="Sample",
            output_dir=str(tmp_path),
            executor=ThreadPoolExecutor(max_workers=1),
        )
    )

    assert result is not None
    assert result.summary_text == "summary body"
    assert result.summary_type_name == "Szczegółowe podsumowanie"
    assert Path(result.summary_path).exists()
    assert "summary body" in Path(result.summary_path).read_text(encoding="utf-8")


def test_cleanup_transcription_artifacts_removes_source_and_chunks(tmp_path):
    source = tmp_path / "source.mp3"
    chunk = tmp_path / "Sample_part1_transcript.txt"
    keep = tmp_path / "Sample_transcript.md"
    source.write_bytes(b"x")
    chunk.write_text("chunk", encoding="utf-8")
    keep.write_text("keep", encoding="utf-8")

    ts.cleanup_transcription_artifacts(
        source_media_path=str(source),
        output_dir=str(tmp_path),
        transcript_prefix="Sample",
    )

    assert not source.exists()
    assert not chunk.exists()
    assert keep.exists()
