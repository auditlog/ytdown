"""Tests for custom follow-up prompts on completed transcripts."""

import asyncio
from unittest.mock import AsyncMock, Mock

from bot.handlers import transcript_prompt_handlers as handlers
from bot.services.transcription_service import CustomAnalysisResult
from tests.telegram_callbacks_support import _attach_runtime, _make_context, _make_update


def _make_text_update(text: str, *, chat_id: int, user_id: int):
    update = Mock()
    update.effective_chat.id = chat_id
    update.effective_user.id = user_id
    update.message = Mock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update


def test_offer_custom_prompt_registers_transcript_and_sends_button(tmp_path):
    transcript = tmp_path / "sample_transcript.md"
    transcript.write_text("# Sample\n\nBody", encoding="utf-8")
    context = _make_context()

    token = asyncio.run(
        handlers.offer_custom_transcript_prompt(
            context,
            chat_id=10,
            requester_id=20,
            transcript_path=str(transcript),
            title="Sample",
        )
    )

    assert token
    stored = context.user_data["transcript_contexts"][token]
    assert stored.transcript_path == str(transcript)
    assert stored.requester_id == 20
    markup = context.bot.send_message.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == f"tr_prompt_{token}"


def test_prompt_callback_sets_pending_state(tmp_path):
    transcript = tmp_path / "sample_transcript.md"
    transcript.write_text("# Sample\n\nBody", encoding="utf-8")
    context = _make_context()
    token = handlers.register_transcript_context(
        context,
        chat_id=10,
        requester_id=20,
        transcript_path=str(transcript),
        title="Sample",
    )
    update = _make_update(f"tr_prompt_{token}", chat_id=10)
    update.effective_user.id = 20

    asyncio.run(
        handlers.handle_transcript_prompt_callback(
            update,
            context,
            f"tr_prompt_{token}",
        )
    )

    pending = context.user_data["pending_transcript_prompt"]
    assert pending.transcript_token == token
    assert pending.requester_id == 20
    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "Napisz, co mam zrobić" in text


def test_cancel_callback_clears_pending_and_keeps_repeat_button(tmp_path):
    transcript = tmp_path / "sample_transcript.md"
    transcript.write_text("Body", encoding="utf-8")
    context = _make_context()
    token = handlers.register_transcript_context(
        context,
        chat_id=10,
        requester_id=20,
        transcript_path=str(transcript),
        title="Sample",
    )
    context.user_data["pending_transcript_prompt"] = handlers.PendingTranscriptPrompt(token, 20)
    update = _make_update(f"tr_prompt_cancel_{token}", chat_id=10)
    update.effective_user.id = 20

    asyncio.run(
        handlers.handle_transcript_prompt_callback(
            update,
            context,
            f"tr_prompt_cancel_{token}",
        )
    )

    assert "pending_transcript_prompt" not in context.user_data
    markup = update.callback_query.edit_message_text.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == f"tr_prompt_{token}"


def test_oldest_transcript_context_is_evicted_after_limit(tmp_path):
    context = _make_context()
    tokens = []
    for index in range(handlers.MAX_TRANSCRIPT_CONTEXTS + 1):
        transcript = tmp_path / f"sample_{index}.md"
        transcript.write_text("Body", encoding="utf-8")
        tokens.append(
            handlers.register_transcript_context(
                context,
                chat_id=10,
                requester_id=20,
                transcript_path=str(transcript),
                title=f"Sample {index}",
            )
        )

    stored = context.user_data["transcript_contexts"]
    assert len(stored) == handlers.MAX_TRANSCRIPT_CONTEXTS
    assert tokens[0] not in stored
    assert tokens[-1] in stored


def test_pending_prompt_generates_message_and_markdown_result(tmp_path, monkeypatch):
    transcript = tmp_path / "sample_transcript.md"
    transcript.write_text("# Sample\n\nTranscript body", encoding="utf-8")
    analysis = tmp_path / "sample_custom.md"
    analysis.write_text("Analysis body", encoding="utf-8")

    context = _make_context()
    runtime = _attach_runtime(context)
    runtime.config["CLAUDE_API_KEY"] = "test-key"
    token = handlers.register_transcript_context(
        context,
        chat_id=10,
        requester_id=20,
        transcript_path=str(transcript),
        title="Sample",
    )
    runtime.session_store.set_field(
        10,
        "pending_transcript_prompt",
        handlers.PendingTranscriptPrompt(token, 20),
    )

    status_message = Mock()
    status_message.edit_text = AsyncMock()
    update = _make_text_update("List decisions", chat_id=10, user_id=20)
    update.message.reply_text.return_value = status_message

    async def fake_generate(**kwargs):
        assert kwargs["transcript_text"] == "Transcript body"
        assert kwargs["prompt"] == "List decisions"
        assert kwargs["api_key"] == "test-key"
        return CustomAnalysisResult("Analysis body", str(analysis))

    monkeypatch.setattr(handlers, "generate_custom_analysis_artifact", fake_generate)
    monkeypatch.setattr(handlers, "check_rate_limit", lambda _user_id: True)

    handled = asyncio.run(handlers.handle_pending_transcript_prompt(update, context))

    assert handled is True
    context.bot.send_document.assert_awaited_once()
    assert any(
        "Analysis body" in call.kwargs["text"]
        for call in context.bot.send_message.await_args_list
    )
    assert runtime.session_store.get_field(10, "pending_transcript_prompt") is None
    final_markup = status_message.edit_text.await_args.kwargs["reply_markup"]
    assert final_markup.inline_keyboard[0][0].callback_data == f"tr_prompt_{token}"


def test_prompt_too_long_keeps_pending_state(tmp_path, monkeypatch):
    transcript = tmp_path / "sample_transcript.md"
    transcript.write_text("Body", encoding="utf-8")
    context = _make_context()
    token = handlers.register_transcript_context(
        context,
        chat_id=10,
        requester_id=20,
        transcript_path=str(transcript),
        title="Sample",
    )
    context.user_data["pending_transcript_prompt"] = handlers.PendingTranscriptPrompt(token, 20)
    update = _make_text_update(
        "x" * (handlers.CUSTOM_PROMPT_MAX_CHARS + 1),
        chat_id=10,
        user_id=20,
    )
    monkeypatch.setattr(handlers, "check_rate_limit", lambda _user_id: True)

    handled = asyncio.run(handlers.handle_pending_transcript_prompt(update, context))

    assert handled is True
    assert "za długie" in update.message.reply_text.await_args.args[0]
    assert context.user_data["pending_transcript_prompt"].transcript_token == token
