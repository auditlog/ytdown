"""Tests for stable provider adapters in transcription_providers."""

import requests as real_requests

from bot import transcription_providers as providers


def test_generate_summary_uses_retry_sleep_hook(monkeypatch):
    calls = {"sleep": [], "attempts": 0}

    class Resp:
        status_code = 500
        text = "error"

    def fake_post(*_args, **_kwargs):
        calls["attempts"] += 1
        return Resp()

    monkeypatch.setattr(providers.requests, "post", fake_post)

    result = providers.generate_summary(
        "tekst",
        1,
        api_key="key",
        requests_module=providers.requests,
        sleep_fn=lambda seconds: calls["sleep"].append(seconds),
    )

    assert result is None
    assert calls["attempts"] == 3
    assert calls["sleep"] == [10, 20]


def test_post_process_transcript_extracts_text_blocks(monkeypatch):
    class Resp:
        status_code = 200

        def json(self):
            return {"content": [{"type": "text", "text": "fixed"}]}

    monkeypatch.setattr(providers.requests, "post", lambda *_args, **_kwargs: Resp())

    result = providers.post_process_transcript(
        "typo",
        api_key="key",
        requests_module=providers.requests,
        sleep_fn=lambda _seconds: None,
    )

    assert result == "fixed"


def test_post_process_transcript_returns_none_without_api_key():
    result = providers.post_process_transcript("text", api_key=None)
    assert result is None


def test_generate_summary_returns_none_without_api_key():
    result = providers.generate_summary("text", 1, api_key=None)
    assert result is None


def test_post_process_transcript_skips_when_text_too_long(monkeypatch):
    called = []
    monkeypatch.setattr(providers.requests, "post", lambda *a, **k: called.append(1))

    result = providers.post_process_transcript(
        "x" * 250_000,
        api_key="key",
        requests_module=providers.requests,
        sleep_fn=lambda _s: None,
    )

    assert result is None
    assert called == []


def test_generate_summary_skips_when_text_exceeds_context_window(monkeypatch):
    called = []
    monkeypatch.setattr(providers.requests, "post", lambda *a, **k: called.append(1))

    result = providers.generate_summary(
        "x" * 800_000,
        1,
        api_key="key",
        requests_module=providers.requests,
        sleep_fn=lambda _s: None,
    )

    assert result is None
    assert called == []


def test_post_process_transcript_retries_on_timeout(monkeypatch):
    calls = {"attempts": 0, "sleep": []}

    def fake_post(*_args, **_kwargs):
        calls["attempts"] += 1
        raise real_requests.exceptions.Timeout("timeout")

    monkeypatch.setattr(providers.requests, "post", fake_post)

    result = providers.post_process_transcript(
        "text",
        api_key="key",
        requests_module=providers.requests,
        sleep_fn=lambda seconds: calls["sleep"].append(seconds),
    )

    assert result is None
    assert calls["attempts"] == 3
    assert calls["sleep"] == [10, 20]


def test_transcribe_audio_returns_empty_when_api_returns_empty_body(monkeypatch, tmp_path):
    """Groq API returning 200 with empty response body should yield an empty string."""

    audio_file = tmp_path / "audio.mp3"
    audio_file.write_bytes(b"x" * 100)

    class Resp:
        status_code = 200
        text = "   "  # whitespace-only — stripped to empty

    monkeypatch.setattr(providers.requests, "post", lambda *_a, **_k: Resp())

    result = providers.transcribe_audio(
        str(audio_file),
        "groq-key",
        requests_module=providers.requests,
    )

    assert result == ""


def test_transcribe_audio_returns_empty_on_malformed_response(monkeypatch, tmp_path):
    """Groq API returning non-200 status should yield an empty string, not raise."""

    audio_file = tmp_path / "audio.mp3"
    audio_file.write_bytes(b"x" * 100)

    class Resp:
        status_code = 422
        text = '{"error": "Unprocessable Entity"}'

    monkeypatch.setattr(providers.requests, "post", lambda *_a, **_k: Resp())

    result = providers.transcribe_audio(
        str(audio_file),
        "groq-key",
        requests_module=providers.requests,
    )

    assert result == ""


def test_transcribe_audio_returns_empty_on_connection_timeout(monkeypatch, tmp_path):
    """A connection timeout during transcription should yield empty string, not raise."""

    audio_file = tmp_path / "audio.mp3"
    audio_file.write_bytes(b"x" * 100)

    def raise_timeout(*_a, **_k):
        raise real_requests.exceptions.Timeout("read timeout")

    monkeypatch.setattr(providers.requests, "post", raise_timeout)

    result = providers.transcribe_audio(
        str(audio_file),
        "groq-key",
        requests_module=providers.requests,
    )

    assert result == ""


def test_post_process_transcript_returns_none_when_api_returns_empty_content(monkeypatch):
    """Claude returning 200 but no text blocks should yield None (treat as failure)."""

    class Resp:
        status_code = 200

        def json(self):
            # Valid response structure but all content blocks are non-text
            return {"content": [{"type": "tool_use", "id": "x"}]}

    monkeypatch.setattr(providers.requests, "post", lambda *_a, **_k: Resp())

    result = providers.post_process_transcript(
        "some text",
        api_key="key",
        requests_module=providers.requests,
        sleep_fn=lambda _s: None,
    )

    assert result is None


def test_post_process_transcript_returns_none_on_connection_error(monkeypatch):
    """A ConnectionError (not Timeout) should also retry and ultimately return None."""

    calls = {"attempts": 0}

    def raise_conn_error(*_a, **_k):
        calls["attempts"] += 1
        raise real_requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr(providers.requests, "post", raise_conn_error)

    result = providers.post_process_transcript(
        "text",
        api_key="key",
        requests_module=providers.requests,
        sleep_fn=lambda _s: None,
    )

    assert result is None
    assert calls["attempts"] == 3
