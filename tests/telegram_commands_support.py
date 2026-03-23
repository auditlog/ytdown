"""Shared helpers for Telegram command handler tests."""

import asyncio
from unittest.mock import AsyncMock, Mock

from bot import telegram_commands as tc
from bot.runtime import AppRuntime
from bot.session_store import SecurityStore, SessionStore


def _set_runtime_values(monkeypatch, **values):
    monkeypatch.setattr(tc, "get_runtime_value", lambda key, default=None: values.get(key, default))


def _set_authorized_users(monkeypatch, users):
    monkeypatch.setattr(tc, "get_authorized_user_ids_for", lambda *_args, **_kwargs: users)


def _async(coro):
    return asyncio.run(coro)


def _make_update(text: str = "", user_id: int = 123456, chat_id: int = 123456):
    update = Mock()
    update.effective_user.id = user_id
    update.effective_user.first_name = "User"
    update.effective_chat.id = chat_id

    update.message = Mock()
    update.message.text = text
    update.message.reply_text = AsyncMock(return_value=Mock(edit_text=AsyncMock()))
    update.message.delete = AsyncMock()

    return update


def _make_context():
    context = Mock()
    context.user_data = {}
    context.bot = Mock()
    return context


def _attach_runtime(context, *, authorized_users=None):
    runtime = AppRuntime(
        config={},
        session_store=SessionStore(),
        security_store=SecurityStore(),
        services=Mock(),
        authorized_users_repository=Mock(),
        download_history_repository=Mock(),
        authorized_users_set=set() if authorized_users is None else set(authorized_users),
    )
    context.application = Mock()
    context.application.bot_data = {"app_runtime": runtime}
    return runtime
