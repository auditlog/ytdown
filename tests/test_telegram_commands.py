"""Tests for /stop command handler and stop_* callback dispatcher."""

from unittest import mock


def test_stop_command_returns_empty_message_when_no_jobs():
    import asyncio
    from bot import telegram_commands
    from bot.jobs import JobRegistry

    registry = JobRegistry()
    update = mock.MagicMock()
    update.effective_chat.id = 1
    update.effective_message.reply_text = mock.AsyncMock()
    context = mock.MagicMock()

    with mock.patch("bot.telegram_commands.job_registry", registry):
        asyncio.run(telegram_commands.stop_command(update, context))

    update.effective_message.reply_text.assert_awaited_once()
    text = update.effective_message.reply_text.await_args.args[0]
    assert "Brak aktywnych operacji" in text


def test_stop_command_lists_active_jobs():
    import asyncio
    from datetime import datetime
    from bot import telegram_commands
    from bot.jobs import JobDescriptor, JobRegistry

    registry = JobRegistry()
    registry.register(7, JobDescriptor(
        job_id="", chat_id=7, kind="playlist_zip",
        label="Playlist 7z (mp3) — [12/30]",
        started_at=datetime.now(),
    ))
    registry.register(7, JobDescriptor(
        job_id="", chat_id=7, kind="single_dl",
        label="Pojedynczy plik (best)",
        started_at=datetime.now(),
    ))

    update = mock.MagicMock()
    update.effective_chat.id = 7
    update.effective_message.reply_text = mock.AsyncMock()
    context = mock.MagicMock()

    with mock.patch("bot.telegram_commands.job_registry", registry):
        asyncio.run(telegram_commands.stop_command(update, context))

    text = update.effective_message.reply_text.await_args.args[0]
    assert "Aktywne operacje (2)" in text
    assert "Playlist 7z" in text
    assert "Pojedynczy plik" in text
    keyboard = update.effective_message.reply_text.await_args.kwargs["reply_markup"]
    callback_data = [
        btn.callback_data for row in keyboard.inline_keyboard for btn in row
    ]
    assert any(cb.startswith("stop_") for cb in callback_data)
    assert "stop_all" in callback_data


def test_stop_callback_cancels_specific_job():
    import asyncio
    from datetime import datetime
    from bot.jobs import JobDescriptor, JobRegistry

    registry = JobRegistry()
    cancellation = registry.register(5, JobDescriptor(
        job_id="", chat_id=5, kind="single_dl",
        label="x", started_at=datetime.now(),
    ))

    update = mock.MagicMock()
    update.effective_chat.id = 5
    update.callback_query = mock.MagicMock()
    update.callback_query.edit_message_text = mock.AsyncMock()
    context = mock.MagicMock()

    with mock.patch("bot.telegram_commands.job_registry", registry):
        from bot.telegram_commands import handle_stop_callback
        asyncio.run(handle_stop_callback(update, context, f"stop_{cancellation.job_id}"))

    assert cancellation.event.is_set()


def test_stop_all_callback_cancels_every_job_in_chat():
    import asyncio
    from datetime import datetime
    from bot.jobs import JobDescriptor, JobRegistry

    registry = JobRegistry()
    c1 = registry.register(8, JobDescriptor(
        job_id="", chat_id=8, kind="single_dl",
        label="A", started_at=datetime.now(),
    ))
    c2 = registry.register(8, JobDescriptor(
        job_id="", chat_id=8, kind="playlist_zip",
        label="B", started_at=datetime.now(),
    ))

    update = mock.MagicMock()
    update.effective_chat.id = 8
    update.callback_query = mock.MagicMock()
    update.callback_query.edit_message_text = mock.AsyncMock()
    context = mock.MagicMock()

    with mock.patch("bot.telegram_commands.job_registry", registry):
        from bot.telegram_commands import handle_stop_callback
        asyncio.run(handle_stop_callback(update, context, "stop_all"))

    assert c1.event.is_set()
    assert c2.event.is_set()
