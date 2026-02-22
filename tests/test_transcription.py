"""
Unit tests for transcription pipeline helpers.
"""

import os
from types import SimpleNamespace
from pathlib import Path

from bot import transcription as tr


def test_get_api_key_reads_from_config(monkeypatch):
    monkeypatch.setattr(tr, "CONFIG", {"GROQ_API_KEY": "groq", "CLAUDE_API_KEY": "claude"})
    assert tr.get_api_key() == "groq"
    assert tr.get_claude_api_key() == "claude"


def test_get_api_keys_from_config():
    assert tr.get_api_key() is not None


def test_find_silence_points(monkeypatch):
    class Completed:
        stderr = "frame=1\nsilence_end: 10.5 [silencedetect] blah\nsilence_end: 42.2 [silencedetect] blah\n"

    monkeypatch.setattr(tr.subprocess, "run", lambda *args, **kwargs: Completed())
    points = tr.find_silence_points("abc.mp3", 2, min_duration=0.5)
    assert points == [10.5, 42.2]


def test_split_mp3_small_file_returns_copy(tmp_path):
    source = tmp_path / "small.mp3"
    source.write_bytes(b"x" * 1024)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    parts = tr.split_mp3(str(source), str(out_dir), max_size_mb=20)
    assert parts == [str(out_dir / "small.mp3")]
    assert (out_dir / "small.mp3").exists()


def test_split_mp3_large_file_uses_ffmpeg(monkeypatch, tmp_path):
    source = tmp_path / "large.mp3"
    source.write_bytes(b"x" * 3)
    outputs = []

    def fake_getsize(path):
        if path == str(source):
            return 30 * 1024 * 1024
        if "part1" in path or "part2" in path:
            return 10 * 1024 * 1024
        return 0

    monkeypatch.setattr(tr.os.path, "getsize", fake_getsize)
    monkeypatch.setattr(tr, "MP3", lambda path: SimpleNamespace(info=SimpleNamespace(length=1200)))

    def fake_run(cmd, *args, **kwargs):
        output_file = cmd[-1]
        outputs.append(output_file)
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        Path(output_file).write_bytes(b"x" * (10 * 1024 * 1024))
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        return completed

    monkeypatch.setattr(tr.subprocess, "run", fake_run)
    monkeypatch.setattr(tr, "find_silence_points", lambda *args, **kwargs: [])

    parts = tr.split_mp3(str(source), str(tmp_path), max_size_mb=20)
    assert len(parts) == 2
    assert all("part" in Path(path).name for path in parts)
    assert sorted(Path(path).name for path in parts) == ["large_part1.mp3", "large_part2.mp3"]
    assert outputs, "ffmpeg subprocess mock was not used"


def test_transcribe_audio_returns_empty_for_large_file(tmp_path):
    file_path = tmp_path / "audio.mp3"
    file_path.write_bytes(b"x" * (26 * 1024 * 1024))
    assert tr.transcribe_audio(str(file_path), "test-key") == ""


def test_transcribe_audio_success(monkeypatch, tmp_path):
    file_path = tmp_path / "audio.mp3"
    file_path.write_bytes(b"x" * (10 * 1024))

    class Resp:
        status_code = 200
        text = "transcribed text"

        def json(self):
            return {"text": self.text}

    monkeypatch.setattr(tr.requests, "post", lambda *args, **kwargs: Resp())

    assert tr.transcribe_audio(str(file_path), "test-key") == "transcribed text"


def test_transcribe_audio_non_200_error(monkeypatch, tmp_path):
    file_path = tmp_path / "audio.mp3"
    file_path.write_bytes(b"x" * (10 * 1024))

    class Resp:
        status_code = 500
        text = "error"

    monkeypatch.setattr(tr.requests, "post", lambda *args, **kwargs: Resp())

    assert tr.transcribe_audio(str(file_path), "test-key") == ""


def test_transcribe_audio_exception(monkeypatch, tmp_path):
    file_path = tmp_path / "audio.mp3"
    file_path.write_bytes(b"x" * (10 * 1024))

    def raise_error(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(tr.requests, "post", raise_error)
    assert tr.transcribe_audio(str(file_path), "test-key") == ""


def test_get_part_number():
    assert tr.get_part_number("sample_part12.mp3") == 12
    assert tr.get_part_number("no-match.wav") == 0


def test_transcribe_mp3_file_empty_when_no_api_key(tmp_path, monkeypatch):
    file_path = tmp_path / "audio.mp3"
    file_path.write_bytes(b"x" * 1024)
    monkeypatch.setattr(tr, "get_api_key", lambda: "")
    assert tr.transcribe_mp3_file(str(file_path), str(tmp_path)) is None


def test_transcribe_mp3_file_success_with_postprocessing(monkeypatch, tmp_path):
    source = tmp_path / "audio.mp3"
    source.write_bytes(b"x" * 1024)

    part1 = tmp_path / "audio_part1.mp3"
    part2 = tmp_path / "audio_part2.mp3"
    part1.write_bytes(b"a" * 128)
    part2.write_bytes(b"b" * 128)

    calls = []

    monkeypatch.setattr(tr, "get_api_key", lambda: "groq-key")
    monkeypatch.setattr(tr, "split_mp3", lambda *args, **kwargs: [str(part1), str(part2)])

    def fake_transcribe_audio(path, key, language=None, prompt=None):
        calls.append((os.path.basename(path), language, prompt))
        if "part1" in path:
            return "first chunk"
        return "second chunk"

    monkeypatch.setattr(tr, "transcribe_audio", fake_transcribe_audio)
    monkeypatch.setattr(tr, "get_claude_api_key", lambda: "claude-key")
    monkeypatch.setattr(tr, "post_process_transcript", lambda text: f"CLEANED:{text}")

    statuses = []

    result = tr.transcribe_mp3_file(str(source), str(tmp_path), progress_callback=statuses.append)

    assert result is not None
    assert Path(result).exists()
    assert Path(result).name == "audio_transcript.md"
    assert os.path.exists(result)
    content = Path(result).read_text(encoding="utf-8")
    assert "CLEANED:" in content
    assert any("Cz" in status for status in statuses)
    assert not (tmp_path / "temp_parts").exists()
    assert len(calls) == 2
    assert calls[0][0] == "audio_part1.mp3"


def test_transcribe_mp3_file_error_creates_error_report(monkeypatch, tmp_path):
    source = tmp_path / "audio.mp3"
    source.write_bytes(b"x" * 1024)

    part1 = tmp_path / "audio_part1.mp3"
    part1.write_bytes(b"a" * 128)

    monkeypatch.setattr(tr, "get_api_key", lambda: "groq-key")
    monkeypatch.setattr(tr, "split_mp3", lambda *args, **kwargs: [str(part1)])
    monkeypatch.setattr(tr, "transcribe_audio", lambda *args, **kwargs: "")
    monkeypatch.setattr(tr, "get_claude_api_key", lambda: "")

    result = tr.transcribe_mp3_file(str(source), str(tmp_path))

    text = Path(result).read_text(encoding="utf-8")
    assert "No transcription for this part" in text


def test_post_process_transcript(monkeypatch):
    monkeypatch.setattr(tr, "get_claude_api_key", lambda: "key")

    class Resp:
        status_code = 200
        def json(self):
            return {"content": [{"type": "text", "text": "fixed text"}]}

    monkeypatch.setattr(tr.requests, "post", lambda *args, **kwargs: Resp())
    assert tr.post_process_transcript("typo") == "fixed text"


def test_post_process_transcript_returns_none_without_api_key(monkeypatch):
    monkeypatch.setattr(tr, "get_claude_api_key", lambda: "")
    assert tr.post_process_transcript("text") is None


def test_generate_summary(monkeypatch):
    monkeypatch.setattr(tr, "get_claude_api_key", lambda: "key")
    captured = {}

    class Resp:
        status_code = 200
        def json(self):
            return {"content": [{"type": "text", "text": "summary"}]}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["payload"] = json
        return Resp()

    monkeypatch.setattr(tr.requests, "post", fake_post)
    summary = tr.generate_summary("tekst", 3)
    assert summary == "summary"
    assert captured["payload"]["messages"][0]["content"].startswith("Przygotuj podsumowanie")


def test_generate_summary_error(monkeypatch):
    monkeypatch.setattr(tr, "get_claude_api_key", lambda: "key")

    class Resp:
        status_code = 500
        text = "error"

    monkeypatch.setattr(tr.requests, "post", lambda *args, **kwargs: Resp())
    assert tr.generate_summary("tekst", 1) is None


# --- Token estimation and length guard tests ---


def test_estimate_token_count_empty():
    assert tr.estimate_token_count("") == 0
    assert tr.estimate_token_count(None) == 0


def test_estimate_token_count_short_text():
    # 100 chars → ~25 tokens
    text = "a" * 100
    assert tr.estimate_token_count(text) == 25


def test_estimate_token_count_long_text():
    # 200k chars → ~50k tokens
    text = "x" * 200_000
    assert tr.estimate_token_count(text) == 50_000


def test_is_text_too_long_for_correction_short():
    assert tr.is_text_too_long_for_correction("Hello world") is False


def test_is_text_too_long_for_correction_at_limit():
    # POST_PROCESS_MAX_INPUT_TOKENS = 50_000 → 200k chars
    text = "x" * (50_000 * 4)
    assert tr.is_text_too_long_for_correction(text) is False


def test_is_text_too_long_for_correction_over_limit():
    text = "x" * (50_001 * 4 + 4)
    assert tr.is_text_too_long_for_correction(text) is True


def test_is_text_too_long_for_summary_short():
    assert tr.is_text_too_long_for_summary("Short text") is False


def test_is_text_too_long_for_summary_over_limit():
    # SUMMARY_MAX_INPUT_TOKENS = 175_000 → 700k chars
    text = "x" * (175_001 * 4 + 4)
    assert tr.is_text_too_long_for_summary(text) is True


def test_post_process_skips_long_text(monkeypatch):
    """post_process_transcript returns None for text exceeding token limit."""
    monkeypatch.setattr(tr, "get_claude_api_key", lambda: "key")

    long_text = "word " * 60_000  # ~300k chars → ~75k tokens > 50k limit
    result = tr.post_process_transcript(long_text)
    assert result is None


def test_post_process_dynamic_max_tokens(monkeypatch):
    """post_process_transcript uses dynamic max_tokens based on input length."""
    monkeypatch.setattr(tr, "get_claude_api_key", lambda: "key")

    captured = {}

    class Resp:
        status_code = 200
        def json(self):
            return {"content": [{"type": "text", "text": "corrected"}]}

    def mock_post(url, *args, **kwargs):
        captured["json"] = kwargs.get("json", {})
        return Resp()

    monkeypatch.setattr(tr.requests, "post", mock_post)

    text = "a" * 4000  # ~1000 tokens
    tr.post_process_transcript(text)

    # max_tokens should be ~input_tokens + 2000, capped at 64000
    assert captured["json"]["max_tokens"] == 1000 + 2000


def test_generate_summary_skips_too_long_text(monkeypatch):
    """generate_summary returns None for text exceeding context window."""
    monkeypatch.setattr(tr, "get_claude_api_key", lambda: "key")

    long_text = "word " * 200_000  # ~1M chars → ~250k tokens > 175k limit
    result = tr.generate_summary(long_text, 1)
    assert result is None


def test_generate_summary_dynamic_max_tokens_by_type(monkeypatch):
    """generate_summary uses different max_tokens per summary type."""
    monkeypatch.setattr(tr, "get_claude_api_key", lambda: "key")

    captured = {}

    class Resp:
        status_code = 200
        def json(self):
            return {"content": [{"type": "text", "text": "summary"}]}

    def mock_post(url, *args, **kwargs):
        captured["json"] = kwargs.get("json", {})
        return Resp()

    monkeypatch.setattr(tr.requests, "post", mock_post)

    # Type 1 (short): 4096
    tr.generate_summary("text", 1)
    assert captured["json"]["max_tokens"] == 4096

    # Type 2 (detailed): 16384
    tr.generate_summary("text", 2)
    assert captured["json"]["max_tokens"] == 16384

    # Type 3 (bullet points): 8192
    tr.generate_summary("text", 3)
    assert captured["json"]["max_tokens"] == 8192

    # Type 4 (task division): 8192
    tr.generate_summary("text", 4)
    assert captured["json"]["max_tokens"] == 8192
