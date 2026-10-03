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


def test_post_process_transcript_keeps_raw_text_when_reply_hits_max_tokens(monkeypatch):
    # A reply cut off at max_tokens is missing the end of the transcript;
    # returning it would silently replace the full raw text with a shorter one.
    class Resp:
        status_code = 200

        def json(self):
            return {
                "content": [{"type": "text", "text": "first half of the corrected text"}],
                "stop_reason": "max_tokens",
            }

    monkeypatch.setattr(providers.requests, "post", lambda *_args, **_kwargs: Resp())

    result = providers.post_process_transcript(
        "first half of the raw text and the second half",
        api_key="key",
        requests_module=providers.requests,
        sleep_fn=lambda _seconds: None,
    )

    assert result is None


def test_post_process_transcript_returns_none_without_api_key():
    result = providers.post_process_transcript("text", api_key=None)
    assert result is None


def test_generate_summary_returns_none_without_api_key():
    result = providers.generate_summary("text", 1, api_key=None)
    assert result is None


def test_generate_custom_analysis_separates_instruction_from_transcript(monkeypatch):
    captured = {}

    class Resp:
        status_code = 200

        def json(self):
            return {"content": [{"type": "text", "text": "custom result"}]}

    def fake_post(_url, **kwargs):
        captured.update(kwargs["json"])
        return Resp()

    monkeypatch.setattr(providers.requests, "post", fake_post)

    result = providers.generate_custom_analysis(
        "transcript body",
        "List decisions",
        api_key="key",
        requests_module=providers.requests,
        sleep_fn=lambda _seconds: None,
    )

    assert result == "custom result"
    assert "source material" in captured["system"]
    blocks = captured["messages"][0]["content"]
    assert "List decisions" in blocks[0]["text"]
    assert "transcript body" not in blocks[0]["text"]
    assert "transcript body" in blocks[1]["text"]


def test_generate_custom_analysis_returns_none_without_api_key():
    assert providers.generate_custom_analysis("text", "instruction", api_key=None) is None


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


def test_generate_custom_analysis_skips_when_input_exceeds_context_window(monkeypatch):
    called = []
    monkeypatch.setattr(providers.requests, "post", lambda *a, **k: called.append(1))

    result = providers.generate_custom_analysis(
        "x" * 800_000,
        "instruction",
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
        sleep_fn=lambda _seconds: None,
    )

    assert result == ""


class _GroqResp:
    def __init__(self, status_code, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


def _transcribe_with_responses(monkeypatch, tmp_path, responses):
    """Run transcribe_audio against a scripted sequence of Groq responses."""

    audio_file = tmp_path / "audio.mp3"
    audio_file.write_bytes(b"x" * 100)
    remaining = list(responses)
    calls = []
    sleeps = []

    def fake_post(*_a, **_k):
        calls.append(1)
        return remaining.pop(0)

    monkeypatch.setattr(providers.requests, "post", fake_post)
    result = providers.transcribe_audio(
        str(audio_file),
        "groq-key",
        requests_module=providers.requests,
        sleep_fn=sleeps.append,
    )
    return result, len(calls), sleeps


def test_transcribe_audio_retries_rate_limit_after_the_advertised_delay(monkeypatch, tmp_path):
    result, calls, sleeps = _transcribe_with_responses(monkeypatch, tmp_path, [
        _GroqResp(429, "rate limited", headers={"Retry-After": "7"}),
        _GroqResp(200, "hello world"),
    ])

    assert result == "hello world"
    assert calls == 2
    assert sleeps == [7.0]


def test_transcribe_audio_gives_up_after_repeated_server_errors(monkeypatch, tmp_path):
    attempts = providers.GROQ_API_MAX_RETRIES
    result, calls, sleeps = _transcribe_with_responses(
        monkeypatch, tmp_path, [_GroqResp(503, "unavailable")] * attempts
    )

    assert result == ""
    assert calls == attempts
    assert len(sleeps) == attempts - 1


def test_transcribe_audio_does_not_retry_a_rejected_api_key(monkeypatch, tmp_path):
    result, calls, sleeps = _transcribe_with_responses(
        monkeypatch, tmp_path, [_GroqResp(401, "invalid api key")]
    )

    assert result == ""
    assert calls == 1
    assert sleeps == []


def test_transcribe_audio_caps_a_very_long_retry_after(monkeypatch, tmp_path):
    result, _calls, sleeps = _transcribe_with_responses(monkeypatch, tmp_path, [
        _GroqResp(429, "rate limited", headers={"Retry-After": "3600"}),
        _GroqResp(200, "ok"),
    ])

    assert result == "ok"
    assert sleeps == [providers.GROQ_RETRY_AFTER_MAX_SEC]


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
