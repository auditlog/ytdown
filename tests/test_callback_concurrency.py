"""One work callback per chat at a time; navigation and stop_* are never blocked."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from bot import telegram_callbacks as tcb

BUSY_TOAST = "Trwa już inna operacja w tym czacie. Poczekaj albo przerwij ją komendą /stop."


def _make_update(data, chat_id, user_id=1):
    update = Mock()
    update.effective_user.id = user_id
    update.effective_chat.id = chat_id
    query = Mock()
    query.data = data
    query.answer = AsyncMock()
    update.callback_query = query
    return update


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    tcb._BUSY_WORK_CHATS.clear()
    monkeypatch.setattr(tcb, "check_rate_limit", lambda *_: True)
    yield
    tcb._BUSY_WORK_CHATS.clear()


def _gated_router(monkeypatch):
    """Replace routing with a stub whose work callbacks hang until released."""

    gate = asyncio.Event()
    started = []

    async def fake_route(update, context, data):
        started.append((update.effective_chat.id, data))
        if data.startswith("dl_"):
            await gate.wait()

    monkeypatch.setattr(tcb, "_route_callback", fake_route)
    return gate, started


def test_second_work_callback_in_same_chat_gets_busy_toast(monkeypatch):
    gate, started = _gated_router(monkeypatch)
    first = _make_update("dl_video_best", chat_id=10)
    second = _make_update("dl_audio_mp3", chat_id=10)

    async def scenario():
        task = asyncio.create_task(tcb.handle_callback(first, Mock()))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await tcb.handle_callback(second, Mock())
        gate.set()
        await task

    asyncio.run(scenario())

    second.callback_query.answer.assert_awaited_once_with(BUSY_TOAST, show_alert=True)
    first.callback_query.answer.assert_awaited_once_with()
    assert started == [(10, "dl_video_best")]
    assert tcb._BUSY_WORK_CHATS == set()


def test_other_chat_is_not_blocked(monkeypatch):
    gate, started = _gated_router(monkeypatch)
    first = _make_update("dl_video_best", chat_id=10)
    other = _make_update("dl_video_best", chat_id=11, user_id=2)

    async def scenario():
        task = asyncio.create_task(tcb.handle_callback(first, Mock()))
        await asyncio.sleep(0)
        other_task = asyncio.create_task(tcb.handle_callback(other, Mock()))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert {chat for chat, _ in started} == {10, 11}
        gate.set()
        await asyncio.gather(task, other_task)

    asyncio.run(scenario())

    other.callback_query.answer.assert_awaited_once_with()


def test_next_work_callback_runs_after_first_finishes(monkeypatch):
    gate, started = _gated_router(monkeypatch)
    gate.set()
    for data in ("dl_video_best", "dl_audio_mp3"):
        update = _make_update(data, chat_id=10)
        asyncio.run(tcb.handle_callback(update, Mock()))
        update.callback_query.answer.assert_awaited_once_with()
    assert [data for _, data in started] == ["dl_video_best", "dl_audio_mp3"]


def test_chat_is_released_when_routing_raises(monkeypatch):
    async def failing_route(update, context, data):
        raise RuntimeError("boom")

    monkeypatch.setattr(tcb, "_route_callback", failing_route)
    update = _make_update("dl_video_best", chat_id=10)

    with pytest.raises(RuntimeError):
        asyncio.run(tcb.handle_callback(update, Mock()))

    assert tcb._BUSY_WORK_CHATS == set()


@pytest.mark.parametrize("data", ["stop_all", "stop_abc123", "back", "pl_cancel", "tr_prompt_tok"])
def test_navigation_and_stop_pass_while_chat_is_busy(monkeypatch, data):
    gate, started = _gated_router(monkeypatch)
    busy = _make_update("dl_video_best", chat_id=10)
    nav = _make_update(data, chat_id=10)

    async def scenario():
        task = asyncio.create_task(tcb.handle_callback(busy, Mock()))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await tcb.handle_callback(nav, Mock())
        gate.set()
        await task

    asyncio.run(scenario())

    nav.callback_query.answer.assert_awaited_once_with()
    assert (10, data) in started


def test_rate_limited_work_callback_does_not_mark_chat_busy(monkeypatch):
    monkeypatch.setattr(tcb, "check_rate_limit", lambda *_: False)
    _gated_router(monkeypatch)
    update = _make_update("dl_video_best", chat_id=10)

    asyncio.run(tcb.handle_callback(update, Mock()))

    assert tcb._BUSY_WORK_CHATS == set()


def test_handler_blocking_flags_are_pinned():
    """Long jobs must not block the update loop; text and commands keep ordering."""

    import main as app_main
    from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler

    app = Mock()
    app_main.register_handlers(app)
    handlers = [call.args[0] for call in app.add_handler.call_args_list]

    callbacks = [h for h in handlers if isinstance(h, CallbackQueryHandler)]
    commands = [h for h in handlers if isinstance(h, CommandHandler)]
    messages = [h for h in handlers if isinstance(h, MessageHandler)]
    uploads = [h for h in messages if h.callback.__name__ in ("handle_audio_upload", "handle_video_upload")]
    texts = [h for h in messages if h not in uploads]

    assert len(callbacks) == 1 and bool(callbacks[0].block) is False
    assert len(uploads) == 4 and all(bool(h.block) is False for h in uploads)
    # Unset block resolves to PTB's DEFAULT_TRUE sentinel, which is truthy.
    assert texts and all(bool(h.block) is True for h in texts)
    assert commands and all(bool(h.block) is True for h in commands)


def test_busy_click_does_not_consume_rate_limit(monkeypatch):
    gate, started = _gated_router(monkeypatch)
    calls = []
    monkeypatch.setattr(tcb, "check_rate_limit", lambda *a: calls.append(a) or True)
    first = _make_update("dl_video_best", chat_id=10)
    second = _make_update("dl_audio_mp3", chat_id=10)

    async def scenario():
        task = asyncio.create_task(tcb.handle_callback(first, Mock()))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await tcb.handle_callback(second, Mock())
        gate.set()
        await task

    asyncio.run(scenario())

    assert len(calls) == 1  # only the first click was counted
