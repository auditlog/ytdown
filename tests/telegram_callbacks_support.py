"""Shared helpers for Telegram callback handler tests."""

from unittest.mock import AsyncMock, Mock

from bot.runtime import AppRuntime
from bot.session_store import SecurityStore, SessionStore


def _make_update(data: str, chat_id: int = 123):
    update = Mock()
    update.effective_chat.id = chat_id
    query = Mock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    update.callback_query = query
    return update


def _make_context():
    context = Mock()
    context.user_data = {}
    context.bot = Mock()
    context.bot.send_document = AsyncMock()
    context.bot.send_message = AsyncMock()
    return context


def _attach_runtime(context):
    runtime = AppRuntime(
        config={},
        session_store=SessionStore(),
        security_store=SecurityStore(),
        services=Mock(),
        authorized_users_repository=Mock(),
        download_history_repository=Mock(),
        authorized_users_set=set(),
    )
    context.application = Mock()
    context.application.bot_data = {"app_runtime": runtime}
    return runtime
