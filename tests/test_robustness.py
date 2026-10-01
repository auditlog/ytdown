"""Robustness tests: dead buttons, empty messages, edited updates, error handler."""

import asyncio
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from telegram import Chat, Message, MessageEntity, Update, User
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut
from telegram.ext import CommandHandler, MessageHandler

import main as app_main
from bot import telegram_callbacks as tc
from bot.handlers import media_extras_callbacks as mec
from bot.handlers.common_ui import build_main_keyboard, send_long_message
from bot.platforms import PLATFORMS
from tests.telegram_callbacks_support import _make_context, _make_update

# Handler entry points handle_callback may delegate to for main-keyboard buttons.
ROUTED_HANDLERS = [
    "download_file",
    "handle_formats_list",
    "show_time_range_options",
    "handle_thumbnail_download",
    "show_subtitle_source_menu",
    "show_summary_options",
    "_handle_trim_download",
    "ensure_trim_authorized",
    "_route_spotify_transcription",
    "_show_spotify_summary_options",
    "download_spotify_resolved",
    "_handle_instagram_download",
    "back_to_main_menu",
]


@pytest.mark.parametrize("large_file", [False, True])
@pytest.mark.parametrize("platform", [p.name for p in PLATFORMS])
def test_every_main_keyboard_button_is_handled(monkeypatch, platform, large_file):
    keyboard = build_main_keyboard(platform, large_file=large_file)
    callbacks = [button.callback_data for row in keyboard for button in row]
    assert "thumbnail" in callbacks or platform in {"spotify", "castbox"}

    monkeypatch.setattr(tc, "check_rate_limit", lambda _user_id: True)
    for data in callbacks:
        reactions = Mock()
        for name in ROUTED_HANDLERS:
            monkeypatch.setattr(tc, name, AsyncMock(side_effect=lambda *a, **k: reactions()))
        update = _make_update(data, chat_id=777)
        update.effective_user.id = 1
        update.callback_query.edit_message_text = AsyncMock(side_effect=lambda *a, **k: reactions())
        context = _make_context()
        context.user_data["platform"] = platform
        tc.user_urls[777] = "https://example.com/watch?v=abc"

        asyncio.run(tc.handle_callback(update, context))

        assert reactions.called, f"{platform}: callback {data!r} was not handled"


def _thumbnail_fixture(monkeypatch, tmp_path, *, info, thumb_path):
    monkeypatch.setattr(mec, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(mec, "get_video_info", lambda url: info)
    monkeypatch.setattr(mec, "download_thumbnail", lambda i, d, embed=False: thumb_path)
    update = _make_update("thumbnail", chat_id=42)
    context = _make_context()
    context.bot.send_photo = AsyncMock()
    return update, context


def test_thumbnail_sends_photo_as_new_message_and_removes_file(monkeypatch, tmp_path):
    thumb = tmp_path / "thumb.jpg"
    thumb.write_bytes(b"img")
    update, context = _thumbnail_fixture(
        monkeypatch, tmp_path, info={"title": "T" * 300}, thumb_path=str(thumb)
    )

    asyncio.run(mec.handle_thumbnail_download(update, context, "https://youtu.be/x"))

    context.bot.send_photo.assert_awaited_once()
    kwargs = context.bot.send_photo.await_args.kwargs
    assert kwargs["chat_id"] == 42
    assert kwargs["caption"] == "T" * 200
    assert not thumb.exists()
    # The format menu must stay clickable: no edit of the menu message.
    update.callback_query.edit_message_text.assert_not_called()
    context.bot.send_message.assert_not_called()


@pytest.mark.parametrize("scenario", ["no_info", "no_thumb", "send_fails"])
def test_thumbnail_failure_sends_new_error_message(monkeypatch, tmp_path, scenario):
    thumb = tmp_path / "thumb.jpg"
    thumb.write_bytes(b"img")
    update, context = _thumbnail_fixture(
        monkeypatch,
        tmp_path,
        info=None if scenario == "no_info" else {"title": "x"},
        thumb_path=None if scenario == "no_thumb" else str(thumb),
    )
    if scenario == "send_fails":
        context.bot.send_photo = AsyncMock(side_effect=RuntimeError("boom"))

    asyncio.run(mec.handle_thumbnail_download(update, context, "https://youtu.be/x"))

    context.bot.send_message.assert_awaited_once_with(
        chat_id=42, text="Nie udało się pobrać miniaturki tego materiału."
    )
    update.callback_query.edit_message_text.assert_not_called()
    if scenario == "send_fails":
        assert not thumb.exists()


def test_send_long_message_skips_empty_and_whitespace_parts():
    bot = Mock()
    bot.send_message = AsyncMock()
    text = ("a" * 4500) + "\nshort tail"

    asyncio.run(send_long_message(bot, 1, text, parse_mode=None))

    sent = [call.kwargs["text"] for call in bot.send_message.await_args_list]
    assert sent and all(part.strip() for part in sent)
    assert "".join(sent).replace("\n", "").startswith("a" * 4500)


def test_send_long_message_does_not_emit_empty_first_chunk_for_overlong_first_line():
    bot = Mock()
    bot.send_message = AsyncMock()

    # 4000 + 3999: the 3999-char remainder cannot share a chunk with the empty header.
    asyncio.run(send_long_message(bot, 1, "x" * 7999, parse_mode=None))

    assert all(c.kwargs["text"].strip() for c in bot.send_message.await_args_list)


def _registered_handlers():
    app = Mock()
    app_main.register_handlers(app)
    return [call.args[0] for call in app.add_handler.call_args_list]


def _text_update(*, edited: bool) -> Update:
    message = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=1, type="private"),
        from_user=User(id=1, is_bot=False, first_name="A"),
        text="/start",
        entities=[MessageEntity(type="bot_command", offset=0, length=6)],
    )
    # CommandHandler compares the optional @botname suffix with the bot username.
    message.set_bot(Mock(username="testbot"))
    return Update(update_id=1, edited_message=message) if edited else Update(update_id=1, message=message)


def test_message_and_command_handlers_ignore_edited_messages():
    handlers = _registered_handlers()
    message_like = [h for h in handlers if isinstance(h, (MessageHandler, CommandHandler))]
    assert message_like

    edited = _text_update(edited=True)
    for handler in message_like:
        assert not handler.check_update(edited), handler


def test_message_handlers_still_accept_new_messages():
    new_message = _text_update(edited=False)
    assert any(
        isinstance(h, CommandHandler) and h.check_update(new_message)
        for h in _registered_handlers()
    )


def test_error_handler_registered():
    app = Mock()
    app_main.register_handlers(app)
    app.add_error_handler.assert_called_once_with(app_main.on_error)


def _error_context(error):
    context = SimpleNamespace(error=error, bot=Mock())
    context.bot.send_message = AsyncMock()
    return context


def _chat_update():
    update = Mock(spec=Update)
    update.effective_chat = SimpleNamespace(id=99)
    return update


def test_error_handler_logs_and_notifies_user(caplog):
    context = _error_context(ValueError("boom"))
    with caplog.at_level(logging.ERROR):
        asyncio.run(app_main.on_error(_chat_update(), context))

    assert any("boom" in r.getMessage() or r.exc_info for r in caplog.records)
    context.bot.send_message.assert_awaited_once()
    assert context.bot.send_message.await_args.kwargs["chat_id"] == 99
    assert context.bot.send_message.await_args.kwargs["text"] == (
        "⚠️ Nie udało się obsłużyć tej akcji z powodu błędu po stronie bota. "
        "Spróbuj ponownie — jeśli to się powtórzy, wyślij link lub plik jeszcze raz."
    )


@pytest.mark.parametrize(
    "error", [TimedOut(), NetworkError("down"), RetryAfter(3), Forbidden("blocked")]
)
def test_error_handler_stays_silent_for_transient_errors(error):
    context = _error_context(error)
    asyncio.run(app_main.on_error(_chat_update(), context))
    context.bot.send_message.assert_not_called()


def test_error_handler_ignores_message_not_modified(caplog):
    error = BadRequest("Message is not modified: specified new message content is the same")
    context = _error_context(error)
    with caplog.at_level(logging.DEBUG):
        asyncio.run(app_main.on_error(_chat_update(), context))

    context.bot.send_message.assert_not_called()
    # A harmless repeat edit is not an error worth an ERROR log line.
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_error_handler_keeps_other_bad_requests_silent_but_logged(caplog):
    # BadRequest subclasses NetworkError in PTB, so it stays in the silent group.
    context = _error_context(BadRequest("Chat not found"))
    with caplog.at_level(logging.ERROR):
        asyncio.run(app_main.on_error(_chat_update(), context))

    context.bot.send_message.assert_not_called()
    assert [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_error_handler_without_chat_does_not_notify():
    context = _error_context(ValueError("boom"))
    asyncio.run(app_main.on_error(object(), context))
    asyncio.run(app_main.on_error(None, context))
    context.bot.send_message.assert_not_called()


def test_error_handler_survives_send_failure():
    context = _error_context(ValueError("boom"))
    context.bot.send_message = AsyncMock(side_effect=RuntimeError("send failed"))
    asyncio.run(app_main.on_error(_chat_update(), context))
