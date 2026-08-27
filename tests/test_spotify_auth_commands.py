"""Telegram command tests for Spotify OAuth management."""

import asyncio
from unittest.mock import AsyncMock, Mock

from bot.handlers import spotify_auth_commands as commands
from tests.telegram_callbacks_support import _attach_runtime, _make_context


def _update(user_id: int):
    update = Mock()
    update.effective_user.id = user_id
    update.effective_message = Mock()
    update.effective_message.reply_text = AsyncMock()
    return update


def test_spotify_login_sends_authorization_button(monkeypatch):
    context = _make_context()
    runtime = _attach_runtime(context)
    runtime.authorized_users_set.add(7)
    runtime.config.update(
        {
            "ADMIN_CHAT_ID": "7",
            "SPOTIFY_REDIRECT_URI": "https://example.test/spotify/callback",
        }
    )
    update = _update(7)
    monkeypatch.setattr(
        commands,
        "build_spotify_authorization_url",
        lambda user_id: "https://accounts.spotify.com/authorize?state=x",
    )
    monkeypatch.setattr(commands, "has_spotify_authorization", lambda: False)

    asyncio.run(commands.spotify_login_command(update, context))

    call = update.effective_message.reply_text.await_args
    keyboard = call.kwargs["reply_markup"].inline_keyboard
    assert keyboard[0][0].url.startswith("https://accounts.spotify.com/authorize")
    assert "Połącz konto Spotify" in call.args[0]


def test_spotify_login_rejects_non_admin(monkeypatch):
    context = _make_context()
    runtime = _attach_runtime(context)
    runtime.authorized_users_set.update({7, 8})
    runtime.config["ADMIN_CHAT_ID"] = "7"
    update = _update(8)
    build_url = Mock()
    monkeypatch.setattr(commands, "build_spotify_authorization_url", build_url)

    asyncio.run(commands.spotify_login_command(update, context))

    build_url.assert_not_called()
    assert "Tylko administrator" in update.effective_message.reply_text.await_args.args[0]


def test_spotify_logout_removes_token_for_admin(monkeypatch):
    context = _make_context()
    runtime = _attach_runtime(context)
    runtime.authorized_users_set.add(7)
    runtime.config["ADMIN_CHAT_ID"] = "7"
    update = _update(7)
    clear = Mock(return_value=True)
    monkeypatch.setattr(commands, "clear_spotify_authorization", clear)

    asyncio.run(commands.spotify_logout_command(update, context))

    clear.assert_called_once_with()
    assert "odłączone" in update.effective_message.reply_text.await_args.args[0]
