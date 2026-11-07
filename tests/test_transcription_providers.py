"""Tests for the OpenRouter adapters in transcription_providers."""

import json

import requests as real_requests

from bot import transcription_providers as providers


class _Resp:
    """Minimal stand-in for a requests response."""

    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


def _scribe_payload(text="Dzień dobry.", words=None):
    return {"text": text, "words": words if words is not None else []}


def _audio(tmp_path, size=100):
    audio_file = tmp_path / "audio.mp3"
    audio_file.write_bytes(b"x" * size)
    return audio_file


def _transcribe_with_responses(monkeypatch, tmp_path, responses):
    """Run transcribe_audio against a scripted sequence of responses."""

    audio_file = _audio(tmp_path)
    remaining = list(responses)
    calls = []
    sleeps = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        outcome = remaining.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(providers.requests, "post", fake_post)
    result = providers.transcribe_audio(
        str(audio_file),
        "or-key",
        requests_module=providers.requests,
        sleep_fn=sleeps.append,
    )
    return result, calls, sleeps


# --- configuration ---------------------------------------------------------


def test_get_api_key_reads_openrouter_key():
    values = {"OPENROUTER_API_KEY": "sk-or-test"}
    assert providers.get_api_key(config_getter=lambda key, default="": values.get(key, default)) == "sk-or-test"


# --- transcription ----------------------------------------------------------


def test_transcribe_audio_sends_scribe_request_with_speakers_and_clean_text(monkeypatch, tmp_path):
    result, calls, _sleeps = _transcribe_with_responses(
        monkeypatch, tmp_path, [_Resp(payload=_scribe_payload())]
    )

    assert result is not None
    url, kwargs = calls[0]
    assert url == "https://openrouter.ai/api/v1/audio/transcriptions"
    assert kwargs["headers"]["Authorization"] == "Bearer or-key"
    assert kwargs["data"]["model"] == "elevenlabs/scribe-v2"
    assert kwargs["data"]["response_format"] == "verbose_json"
    assert kwargs["data"]["timestamp_granularities[]"] == "word"
    options = json.loads(kwargs["data"]["provider"])["options"]["elevenlabs"]
    assert options == {"diarize": True, "no_verbatim": True}
    name, body, mime = kwargs["files"]["file"]
    assert (name, mime) == ("audio.mp3", "audio/mpeg")
    assert body == b"x" * 100


def test_transcribe_audio_passes_language_when_given(monkeypatch, tmp_path):
    audio_file = _audio(tmp_path)
    captured = {}

    def fake_post(_url, **kwargs):
        captured.update(kwargs["data"])
        return _Resp(payload=_scribe_payload())

    monkeypatch.setattr(providers.requests, "post", fake_post)
    providers.transcribe_audio(str(audio_file), "or-key", language="pl", requests_module=providers.requests)

    assert captured["language"] == "pl"


def test_transcribe_audio_strips_sound_tags_from_text(monkeypatch, tmp_path):
    payload = _scribe_payload("No właśnie. [śmiech] Tak było [oklaski], prawda?")
    result, _calls, _sleeps = _transcribe_with_responses(monkeypatch, tmp_path, [_Resp(payload=payload)])

    assert result.text == "No właśnie. Tak było, prawda?"


def test_transcribe_audio_groups_words_into_speaker_turns(monkeypatch, tmp_path):
    words = [
        {"word": "Dzień", "start": 0.1, "end": 0.4, "speaker": 0},
        {"word": "dobry.", "start": 0.4, "end": 0.8, "speaker": 0},
        {"word": "[śmiech]", "start": 0.8, "end": 1.0, "speaker": 0, "type": "audio_event"},
        {"word": "Cześć!", "start": 1.2, "end": 1.6, "speaker": 1},
        {"word": "Witam.", "start": 1.7, "end": 2.0, "speaker": 0},
    ]
    payload = _scribe_payload("Dzień dobry. Cześć! Witam.", words)
    result, _calls, _sleeps = _transcribe_with_responses(monkeypatch, tmp_path, [_Resp(payload=payload)])

    assert result.turns == (
        providers.SpeakerTurn(speaker=0, start=0.1, end=0.8, text="Dzień dobry."),
        providers.SpeakerTurn(speaker=1, start=1.2, end=1.6, text="Cześć!"),
        providers.SpeakerTurn(speaker=0, start=1.7, end=2.0, text="Witam."),
    )


def test_transcribe_audio_returns_none_for_empty_text(monkeypatch, tmp_path):
    result, _calls, _sleeps = _transcribe_with_responses(
        monkeypatch, tmp_path, [_Resp(payload=_scribe_payload("  [śmiech] "))]
    )

    assert result is None


def test_transcribe_audio_returns_none_for_a_body_that_is_not_json(monkeypatch, tmp_path):
    result, calls, _sleeps = _transcribe_with_responses(
        monkeypatch, tmp_path, [_Resp(status_code=200, text="<html>bad gateway</html>")]
    )

    assert result is None
    assert len(calls) == 1


def test_transcribe_audio_refuses_a_file_above_the_upload_cap(tmp_path):
    audio_file = _audio(tmp_path, size=26 * 1024 * 1024)

    assert providers.transcribe_audio(str(audio_file), "or-key") is None


def test_transcribe_audio_returns_none_for_missing_file(tmp_path):
    assert providers.transcribe_audio(str(tmp_path / "missing.mp3"), "or-key") is None


def test_transcribe_audio_retries_rate_limit_after_the_advertised_delay(monkeypatch, tmp_path):
    result, calls, sleeps = _transcribe_with_responses(monkeypatch, tmp_path, [
        _Resp(429, text="rate limited", headers={"Retry-After": "7"}),
        _Resp(payload=_scribe_payload("hello world")),
    ])

    assert result.text == "hello world"
    assert len(calls) == 2
    assert sleeps == [7.0]


def test_transcribe_audio_gives_up_after_repeated_server_errors(monkeypatch, tmp_path):
    attempts = providers.TRANSCRIPTION_API_MAX_RETRIES
    result, calls, sleeps = _transcribe_with_responses(
        monkeypatch, tmp_path, [_Resp(503, text="unavailable")] * attempts
    )

    assert result is None
    assert len(calls) == attempts
    assert len(sleeps) == attempts - 1


def test_transcribe_audio_does_not_retry_a_rejected_api_key(monkeypatch, tmp_path):
    result, calls, sleeps = _transcribe_with_responses(
        monkeypatch, tmp_path, [_Resp(401, text="invalid api key")]
    )

    assert result is None
    assert len(calls) == 1
    assert sleeps == []


def test_transcribe_audio_caps_a_very_long_retry_after(monkeypatch, tmp_path):
    result, _calls, sleeps = _transcribe_with_responses(monkeypatch, tmp_path, [
        _Resp(429, text="rate limited", headers={"Retry-After": "3600"}),
        _Resp(payload=_scribe_payload("ok")),
    ])

    assert result.text == "ok"
    assert sleeps == [providers.RETRY_AFTER_MAX_SEC]


def test_transcribe_audio_retries_a_dropped_connection(monkeypatch, tmp_path):
    result, _calls, sleeps = _transcribe_with_responses(monkeypatch, tmp_path, [
        real_requests.exceptions.ConnectionError("reset"),
        _Resp(payload=_scribe_payload("after reconnect")),
    ])

    assert result.text == "after reconnect"
    assert sleeps == [providers.TRANSCRIPTION_API_RETRY_BASE_DELAY]


def test_transcribe_audio_falls_back_to_backoff_for_a_date_retry_after(monkeypatch, tmp_path):
    # Retry-After may also be an HTTP date; only the seconds form is parsed.
    result, _calls, sleeps = _transcribe_with_responses(monkeypatch, tmp_path, [
        _Resp(503, text="busy", headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
        _Resp(payload=_scribe_payload("ok")),
    ])

    assert result.text == "ok"
    assert sleeps == [providers.TRANSCRIPTION_API_RETRY_BASE_DELAY]


# --- chat completions -------------------------------------------------------


def _chat_payload(content, finish_reason="stop"):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}


def test_chat_completion_posts_openai_style_request(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _Resp(payload=_chat_payload("odpowiedź"))

    monkeypatch.setattr(providers.requests, "post", fake_post)
    reply = providers.chat_completion(
        "or-key",
        model="anthropic/claude-haiku-4.5",
        messages=[{"role": "user", "content": "pytanie"}],
        max_tokens=100,
        timeout=30,
        requests_module=providers.requests,
        sleep_fn=lambda _s: None,
    )

    assert reply == providers.ChatReply(text="odpowiedź", finish_reason="stop")
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer or-key"
    assert captured["json"]["model"] == "anthropic/claude-haiku-4.5"
    assert captured["json"]["max_tokens"] == 100
    assert "response_format" not in captured["json"]


def test_chat_completion_returns_none_for_an_empty_reply(monkeypatch):
    monkeypatch.setattr(providers.requests, "post", lambda *_a, **_k: _Resp(payload=_chat_payload("  ")))

    reply = providers.chat_completion(
        "or-key", model="m", messages=[], max_tokens=10, timeout=5,
        requests_module=providers.requests, sleep_fn=lambda _s: None,
    )

    assert reply is None


def test_chat_completion_returns_none_for_a_malformed_body(monkeypatch):
    monkeypatch.setattr(providers.requests, "post", lambda *_a, **_k: _Resp(payload={"error": {"message": "x"}}))

    reply = providers.chat_completion(
        "or-key", model="m", messages=[], max_tokens=10, timeout=5,
        requests_module=providers.requests, sleep_fn=lambda _s: None,
    )

    assert reply is None


# --- summaries and custom analyses -------------------------------------------


def test_generate_summary_uses_haiku_through_openrouter(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs["json"])
        return _Resp(payload=_chat_payload("Streszczenie"))

    monkeypatch.setattr(providers.requests, "post", fake_post)
    result = providers.generate_summary(
        "tekst", 3, api_key="or-key", requests_module=providers.requests, sleep_fn=lambda _s: None,
    )

    assert result == "Streszczenie"
    assert captured["url"].endswith("/chat/completions")
    assert captured["model"] == "anthropic/claude-haiku-4.5"
    assert captured["max_tokens"] == 8192
    assert captured["messages"][0]["content"].endswith("tekst")
    assert "punktów" in captured["messages"][0]["content"]


def test_generate_summary_uses_retry_sleep_hook(monkeypatch):
    calls = {"attempts": 0, "sleep": []}

    def fake_post(*_args, **_kwargs):
        calls["attempts"] += 1
        return _Resp(500, text="error")

    monkeypatch.setattr(providers.requests, "post", fake_post)

    result = providers.generate_summary(
        "tekst", 1, api_key="key", requests_module=providers.requests,
        sleep_fn=lambda seconds: calls["sleep"].append(seconds),
    )

    assert result is None
    assert calls["attempts"] == 3
    assert calls["sleep"] == [10, 20]


def test_generate_summary_retries_on_timeout(monkeypatch):
    calls = {"attempts": 0}

    def fake_post(*_args, **_kwargs):
        calls["attempts"] += 1
        raise real_requests.exceptions.Timeout("timeout")

    monkeypatch.setattr(providers.requests, "post", fake_post)

    result = providers.generate_summary(
        "tekst", 1, api_key="key", requests_module=providers.requests, sleep_fn=lambda _s: None,
    )

    assert result is None
    assert calls["attempts"] == 3


def test_generate_summary_returns_none_without_api_key():
    assert providers.generate_summary("text", 1, api_key=None) is None


def test_generate_summary_skips_when_text_exceeds_context_window(monkeypatch):
    called = []
    monkeypatch.setattr(providers.requests, "post", lambda *a, **k: called.append(1))

    result = providers.generate_summary(
        "x" * 800_000, 1, api_key="key", requests_module=providers.requests, sleep_fn=lambda _s: None,
    )

    assert result is None
    assert called == []


def test_generate_custom_analysis_separates_instruction_from_transcript(monkeypatch):
    captured = {}

    def fake_post(_url, **kwargs):
        captured.update(kwargs["json"])
        return _Resp(payload=_chat_payload("custom result"))

    monkeypatch.setattr(providers.requests, "post", fake_post)

    result = providers.generate_custom_analysis(
        "transcript body",
        "List decisions",
        api_key="key",
        requests_module=providers.requests,
        sleep_fn=lambda _seconds: None,
    )

    assert result == "custom result"
    assert captured["model"] == "anthropic/claude-haiku-4.5"
    system, user = captured["messages"]
    assert system["role"] == "system"
    assert "source material" in system["content"]
    blocks = user["content"]
    assert "List decisions" in blocks[0]["text"]
    assert "transcript body" not in blocks[0]["text"]
    assert "transcript body" in blocks[1]["text"]


def test_generate_custom_analysis_returns_none_without_api_key():
    assert providers.generate_custom_analysis("text", "instruction", api_key=None) is None


def test_generate_custom_analysis_skips_when_input_exceeds_context_window(monkeypatch):
    called = []
    monkeypatch.setattr(providers.requests, "post", lambda *a, **k: called.append(1))

    result = providers.generate_custom_analysis(
        "x" * 800_000, "instruction", api_key="key", requests_module=providers.requests, sleep_fn=lambda _s: None,
    )

    assert result is None
    assert called == []


def test_transcribe_audio_retries_a_broken_chunked_response(monkeypatch, tmp_path):
    result, calls, _sleeps = _transcribe_with_responses(monkeypatch, tmp_path, [
        real_requests.exceptions.ChunkedEncodingError("connection broken"),
        _Resp(payload=_scribe_payload("ok")),
    ])

    assert result.text == "ok"
    assert len(calls) == 2
