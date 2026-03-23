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
