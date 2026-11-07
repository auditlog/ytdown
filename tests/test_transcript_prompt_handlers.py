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


def _schedule_on_loop(context):
    """Make application.create_task schedule on the running loop, like PTB."""

    tasks = []

    def create_task(coroutine, update=None, **kwargs):
        task = asyncio.get_running_loop().create_task(coroutine)
        tasks.append(task)
        return task

    context.application.create_task = Mock(side_effect=create_task)
    return tasks


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
    runtime.config["OPENROUTER_API_KEY"] = "test-key"
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
    tasks = _schedule_on_loop(context)

    async def scenario():
        handled = await handlers.handle_pending_transcript_prompt(update, context)
        # The analysis runs as a PTB task; the handler returns before it finishes.
        assert len(tasks) == 1
        await asyncio.gather(*tasks)
        return handled

    handled = asyncio.run(scenario())

    assert handled is True
    assert context.application.create_task.call_args.kwargs["update"] is update
    context.bot.send_document.assert_awaited_once()
    assert any(
        "Analysis body" in call.kwargs["text"]
        for call in context.bot.send_message.await_args_list
    )
    assert runtime.session_store.get_field(10, "pending_transcript_prompt") is None
    final_markup = status_message.edit_text.await_args.kwargs["reply_markup"]
    assert final_markup.inline_keyboard[0][0].callback_data == f"tr_prompt_{token}"


def test_pending_prompt_without_claude_key_explains_without_variable_name(tmp_path, monkeypatch):
    transcript = tmp_path / "sample_transcript.md"
    transcript.write_text("# Sample\n\nTranscript body", encoding="utf-8")
    context = _make_context()
    runtime = _attach_runtime(context)
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
    monkeypatch.setattr(handlers, "check_rate_limit", lambda _user_id: True)
    update = _make_text_update("List decisions", chat_id=10, user_id=20)

    handled = asyncio.run(handlers.handle_pending_transcript_prompt(update, context))

    assert handled is True
    update.message.reply_text.assert_awaited_once_with(
        "Funkcja niedostępna — brak klucza API OpenRouter. Skontaktuj się z administratorem."
    )


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


def test_prompt_callback_clears_pending_trim(tmp_path):
    transcript = tmp_path / "sample_transcript.md"
    transcript.write_text("Body", encoding="utf-8")
    context = _make_context()
    token = handlers.register_transcript_context(
        context, chat_id=10, requester_id=20, transcript_path=str(transcript), title="Sample",
    )
    context.user_data["pending_trim"] = object()
    update = _make_update(f"tr_prompt_{token}", chat_id=10)
    update.effective_user.id = 20

    asyncio.run(handlers.handle_transcript_prompt_callback(update, context, f"tr_prompt_{token}"))

    assert "pending_trim" not in context.user_data
    assert context.user_data["pending_transcript_prompt"].transcript_token == token


def test_pending_prompt_scheduling_failure_reports_error(tmp_path, monkeypatch, caplog):
    transcript = tmp_path / "sample_transcript.md"
    transcript.write_text("# Sample\n\nTranscript body", encoding="utf-8")
    context = _make_context()
    runtime = _attach_runtime(context)
    runtime.config["OPENROUTER_API_KEY"] = "test-key"
    token = handlers.register_transcript_context(
        context, chat_id=10, requester_id=20, transcript_path=str(transcript), title="Sample",
    )
    runtime.session_store.set_field(
        10, "pending_transcript_prompt", handlers.PendingTranscriptPrompt(token, 20),
    )
    status_message = Mock()
    status_message.edit_text = AsyncMock()
    update = _make_text_update("List decisions", chat_id=10, user_id=20)
    update.message.reply_text.return_value = status_message
    generate = AsyncMock()
    monkeypatch.setattr(handlers, "generate_custom_analysis_artifact", generate)
    monkeypatch.setattr(handlers, "check_rate_limit", lambda _user_id: True)
    scheduled = []

    def failing_create_task(coroutine, update=None, **kwargs):
        scheduled.append(coroutine)
        raise RuntimeError("application is shutting down")

    context.application.create_task = failing_create_task

    assert asyncio.run(handlers.handle_pending_transcript_prompt(update, context)) is True

    assert status_message.edit_text.await_args.args[0] == "Nie udało się uruchomić analizy. Spróbuj ponownie."
    generate.assert_not_called()
    # The coroutine was closed, so nothing is left to be "never awaited".
    assert scheduled[0].cr_frame is None


# --- speaker naming ------------------------------------------------------------

from bot import speakers as speaker_data  # noqa: E402
from bot.services.transcription_service import SpeakerLabelsResult  # noqa: E402
from bot.transcription_providers import SpeakerTurn  # noqa: E402


def _conversation_transcript(tmp_path, voices=2):
    transcript = tmp_path / "talk_transcript.md"
    transcript.write_text("# Talk\n\nPytanie? Odpowiedź.", encoding="utf-8")
    turns = [SpeakerTurn(0, 0.0, 1.0, "Pytanie?")]
    if voices == 2:
        turns.append(SpeakerTurn(1, 1.0, 2.0, "Odpowiedź."))
    speaker_data.save_speaker_parts(str(transcript), [speaker_data.SpeakerPart(part=1, turns=tuple(turns))])
    return transcript


def _callback_data(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def test_offer_adds_speaker_buttons_when_two_voices_were_heard(tmp_path):
    transcript = _conversation_transcript(tmp_path)
    context = _make_context()

    token = asyncio.run(handlers.offer_custom_transcript_prompt(
        context, chat_id=10, requester_id=20, transcript_path=str(transcript), title="Talk",
    ))

    kwargs = context.bot.send_message.await_args.kwargs
    assert _callback_data(kwargs["reply_markup"]) == [
        f"tr_prompt_{token}", f"tr_spk_auto_{token}", f"tr_spk_hint_{token}",
    ]
    assert "kilka osób" in kwargs["text"]


def test_offer_has_no_speaker_buttons_for_a_single_voice(tmp_path):
    transcript = _conversation_transcript(tmp_path, voices=1)
    context = _make_context()

    token = asyncio.run(handlers.offer_custom_transcript_prompt(
        context, chat_id=10, requester_id=20, transcript_path=str(transcript), title="Talk",
    ))

    kwargs = context.bot.send_message.await_args.kwargs
    assert _callback_data(kwargs["reply_markup"]) == [f"tr_prompt_{token}"]
    assert "kilka osób" not in kwargs["text"]


def test_hint_callback_waits_for_participants(tmp_path):
    transcript = _conversation_transcript(tmp_path)
    context = _make_context()
    token = handlers.register_transcript_context(
        context, chat_id=10, requester_id=20, transcript_path=str(transcript), title="Talk",
    )
    context.user_data["pending_trim"] = object()
    update = _make_update(f"tr_spk_hint_{token}", chat_id=10)
    update.effective_user.id = 20

    asyncio.run(handlers.handle_speaker_callback(update, context, f"tr_spk_hint_{token}"))

    pending = context.user_data["pending_transcript_prompt"]
    assert (pending.transcript_token, pending.requester_id, pending.kind) == (token, 20, handlers.SPEAKER_HINTS_KIND)
    assert "pending_trim" not in context.user_data
    edit = update.callback_query.edit_message_text.await_args
    assert "kto bierze udział" in edit.args[0]
    assert _callback_data(edit.kwargs["reply_markup"]) == [f"tr_spk_cancel_{token}"]


def test_speaker_cancel_clears_the_wait(tmp_path):
    transcript = _conversation_transcript(tmp_path)
    context = _make_context()
    token = handlers.register_transcript_context(
        context, chat_id=10, requester_id=20, transcript_path=str(transcript), title="Talk",
    )
    context.user_data["pending_transcript_prompt"] = handlers.PendingTranscriptPrompt(
        token, 20, kind=handlers.SPEAKER_HINTS_KIND,
    )
    update = _make_update(f"tr_spk_cancel_{token}", chat_id=10)
    update.effective_user.id = 20

    asyncio.run(handlers.handle_speaker_callback(update, context, f"tr_spk_cancel_{token}"))

    assert "pending_transcript_prompt" not in context.user_data
    edit = update.callback_query.edit_message_text.await_args
    assert f"tr_spk_auto_{token}" in _callback_data(edit.kwargs["reply_markup"])


def test_speaker_callback_refuses_another_user(tmp_path):
    transcript = _conversation_transcript(tmp_path)
    context = _make_context()
    token = handlers.register_transcript_context(
        context, chat_id=10, requester_id=20, transcript_path=str(transcript), title="Talk",
    )
    update = _make_update(f"tr_spk_auto_{token}", chat_id=10)
    update.effective_user.id = 99

    asyncio.run(handlers.handle_speaker_callback(update, context, f"tr_spk_auto_{token}"))

    assert "Tylko osoba" in context.bot.send_message.await_args.kwargs["text"]
    update.callback_query.edit_message_text.assert_not_awaited()


def test_auto_speaker_callback_without_key_explains(tmp_path):
    transcript = _conversation_transcript(tmp_path)
    context = _make_context()
    _attach_runtime(context)
    token = handlers.register_transcript_context(
        context, chat_id=10, requester_id=20, transcript_path=str(transcript), title="Talk",
    )
    update = _make_update(f"tr_spk_auto_{token}", chat_id=10)
    update.effective_user.id = 20

    asyncio.run(handlers.handle_speaker_callback(update, context, f"tr_spk_auto_{token}"))

    assert update.callback_query.edit_message_text.await_args.args[0] == handlers.MISSING_AI_KEY_ADMIN_TEXT


def test_auto_speaker_callback_delivers_labels_and_offers_the_new_file(tmp_path, monkeypatch):
    transcript = _conversation_transcript(tmp_path)
    labeled = tmp_path / "Talk_speakers_ab12.md"
    labeled.write_text("# Talk\n\nKarolina: Pytanie?", encoding="utf-8")
    context = _make_context()
    runtime = _attach_runtime(context)
    runtime.config["OPENROUTER_API_KEY"] = "test-key"
    token = handlers.register_transcript_context(
        context, chat_id=10, requester_id=20, transcript_path=str(transcript), title="Talk",
    )
    seen = {}

    async def fake_labels(**kwargs):
        seen.update(kwargs)
        return SpeakerLabelsResult("• Karolina (pewność: wysoka)", str(labeled))

    monkeypatch.setattr(handlers, "generate_speaker_labels_artifact", fake_labels)
    update = _make_update(f"tr_spk_auto_{token}", chat_id=10)
    update.effective_user.id = 20

    asyncio.run(handlers.handle_speaker_callback(update, context, f"tr_spk_auto_{token}"))

    assert seen["transcript_path"] == str(transcript)
    assert seen["hints"] == ""
    assert seen["api_key"] == "test-key"
    texts = [call.kwargs["text"] for call in context.bot.send_message.await_args_list]
    assert any("Karolina (pewność: wysoka)" in text for text in texts)
    context.bot.send_document.assert_awaited_once()
    final = update.callback_query.edit_message_text.await_args
    assert "podpisani" in final.args[0]
    assert f"tr_spk_hint_{token}" in _callback_data(final.kwargs["reply_markup"])
    # The labelled file becomes a transcript of its own for follow-up prompts.
    new_offer = context.bot.send_message.await_args.kwargs["reply_markup"]
    new_token = _callback_data(new_offer)[0].removeprefix("tr_prompt_")
    assert new_token != token
    assert handlers._find_context(context, 10, new_token).transcript_path == str(labeled)


def test_auto_speaker_failure_keeps_retry_buttons(tmp_path, monkeypatch):
    transcript = _conversation_transcript(tmp_path)
    context = _make_context()
    runtime = _attach_runtime(context)
    runtime.config["OPENROUTER_API_KEY"] = "test-key"
    token = handlers.register_transcript_context(
        context, chat_id=10, requester_id=20, transcript_path=str(transcript), title="Talk",
    )
    monkeypatch.setattr(handlers, "generate_speaker_labels_artifact", AsyncMock(return_value=None))
    update = _make_update(f"tr_spk_auto_{token}", chat_id=10)
    update.effective_user.id = 20

    asyncio.run(handlers.handle_speaker_callback(update, context, f"tr_spk_auto_{token}"))

    final = update.callback_query.edit_message_text.await_args
    assert final.args[0] == "Nie udało się rozpoznać rozmówców. Spróbuj ponownie."
    assert f"tr_spk_auto_{token}" in _callback_data(final.kwargs["reply_markup"])
    context.bot.send_document.assert_not_awaited()


def test_pending_participants_run_naming_with_hints(tmp_path, monkeypatch):
    transcript = _conversation_transcript(tmp_path)
    labeled = tmp_path / "Talk_speakers_cd34.md"
    labeled.write_text("# Talk\n\nAnna: Pytanie?", encoding="utf-8")
    context = _make_context()
    runtime = _attach_runtime(context)
    runtime.config["OPENROUTER_API_KEY"] = "test-key"
    token = handlers.register_transcript_context(
        context, chat_id=10, requester_id=20, transcript_path=str(transcript), title="Talk",
    )
    runtime.session_store.set_field(
        10, "pending_transcript_prompt",
        handlers.PendingTranscriptPrompt(token, 20, kind=handlers.SPEAKER_HINTS_KIND),
    )
    status_message = Mock()
    status_message.edit_text = AsyncMock()
    update = _make_text_update("Anna – prowadząca, Karolina – gość", chat_id=10, user_id=20)
    update.message.reply_text.return_value = status_message
    seen = {}

    async def fake_labels(**kwargs):
        seen.update(kwargs)
        return SpeakerLabelsResult("• Anna, prowadząca (pewność: wysoka)", str(labeled))

    monkeypatch.setattr(handlers, "generate_speaker_labels_artifact", fake_labels)
    monkeypatch.setattr(handlers, "check_rate_limit", lambda _user_id: True)
    tasks = _schedule_on_loop(context)

    async def scenario():
        handled = await handlers.handle_pending_transcript_prompt(update, context)
        await asyncio.gather(*tasks)
        return handled

    assert asyncio.run(scenario()) is True
    assert seen["hints"] == "Anna – prowadząca, Karolina – gość"
    assert runtime.session_store.get_field(10, "pending_transcript_prompt") is None
    context.bot.send_document.assert_awaited_once()
    assert "podpisani" in status_message.edit_text.await_args.args[0]


def test_participants_longer_than_the_limit_keep_waiting(tmp_path, monkeypatch):
    transcript = _conversation_transcript(tmp_path)
    context = _make_context()
    token = handlers.register_transcript_context(
        context, chat_id=10, requester_id=20, transcript_path=str(transcript), title="Talk",
    )
    context.user_data["pending_transcript_prompt"] = handlers.PendingTranscriptPrompt(
        token, 20, kind=handlers.SPEAKER_HINTS_KIND,
    )
    update = _make_text_update("x" * (handlers.SPEAKER_HINTS_MAX_CHARS + 1), chat_id=10, user_id=20)
    monkeypatch.setattr(handlers, "check_rate_limit", lambda _user_id: True)

    assert asyncio.run(handlers.handle_pending_transcript_prompt(update, context)) is True

    assert "za długa" in update.message.reply_text.await_args.args[0]
    assert context.user_data["pending_transcript_prompt"].kind == handlers.SPEAKER_HINTS_KIND


def test_original_transcript_survives_several_naming_rounds(tmp_path):
    transcript = _conversation_transcript(tmp_path)
    context = _make_context()
    original = handlers.register_transcript_context(
        context, chat_id=10, requester_id=20, transcript_path=str(transcript), title="Talk",
    )
    for index in range(4):
        labeled = tmp_path / f"Talk_speakers_{index}.md"
        labeled.write_text("Body", encoding="utf-8")
        handlers.register_transcript_context(
            context, chat_id=10, requester_id=20, transcript_path=str(labeled), title="Talk (z rozmówcami)",
        )

    assert handlers._find_context(context, 10, original) is not None


def test_auto_speaker_callback_drops_a_waiting_participant_list(tmp_path, monkeypatch):
    # Otherwise the user's next message (e.g. a link) would be taken as participants.
    transcript = _conversation_transcript(tmp_path)
    context = _make_context()
    runtime = _attach_runtime(context)
    runtime.config["OPENROUTER_API_KEY"] = "test-key"
    token = handlers.register_transcript_context(
        context, chat_id=10, requester_id=20, transcript_path=str(transcript), title="Talk",
    )
    runtime.session_store.set_field(
        10, "pending_transcript_prompt",
        handlers.PendingTranscriptPrompt("other", 20, kind=handlers.SPEAKER_HINTS_KIND),
    )
    monkeypatch.setattr(handlers, "generate_speaker_labels_artifact", AsyncMock(return_value=None))
    update = _make_update(f"tr_spk_auto_{token}", chat_id=10)
    update.effective_user.id = 20

    asyncio.run(handlers.handle_speaker_callback(update, context, f"tr_spk_auto_{token}"))

    assert runtime.session_store.get_field(10, "pending_transcript_prompt") is None
