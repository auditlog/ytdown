"""Rate limiting applies only to work-starting callbacks; menus stay free."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from bot import telegram_callbacks as tcb
from bot.handlers.callback_parsing import is_work_callback
from bot.security_throttling import RATE_LIMIT_TOAST

# Every callback_data family the bot emits (grep callback_data= under bot/).
WORK_CALLBACKS = [
    "dl_video_best", "dl_video_720p", "dl_video_137", "dl_audio_mp3",
    "dl_audio_flac", "dl_audio_format_140", "dl_ig_all", "dl_ig_photos",
    "spv_video_720p", "spv_audio_m4a", "trim_dl", "thumbnail",
    "transcribe", "transcribe_summary", "summary_option_1",
    "audio_transcribe", "audio_transcribe_summary", "audio_summary_option_3",
    "sub_lang_pl", "sub_lang_en_sum", "sub_auto_pl", "sub_src_ai",
    "sub_src_ai_sum", "sub_sum_2", "pl_dl_audio_mp3", "pl_dl_video_best",
    "pl_zip_dl_audio_m4a", "pl_zip_dl_video_720p", "spc_dl_mp3", "spc_dl_m4a",
    "arc_split_tok", "arc_resend_tok_0", "arc_pack_partial_tok",
    "spc_pack_mp3_50", "spc_pack_mp3_100", "spc_pack_mp3_all",
    "spc_pack_m4a_50", "spc_pack_m4a_100", "spc_pack_m4a_all",
]
NAVIGATION_CALLBACKS = [
    "spc_t_3", "spc_p_0", "spc_p_2", "spc_all", "spc_clear", "spc_pack_back",
    "spc_pack_mp3", "spc_pack_m4a", "pl_more", "pl_cancel", "pl_single",
    "pl_full", "back", "time_range", "time_range_clear",
    "time_range_preset_first_5", "time_range_preset_last_10", "formats",
    "stop_all", "stop_dismiss", "stop_refresh", "stop_abc123",
    "trim_cancel", "trim_upload", "trim_src_tok", "tr_prompt_tok",
    "tr_prompt_cancel_tok", "arc_cancel_tok", "arc_purge_tok",
    "arc_purge_partial_tok", "noop",
    # Malformed pack choices are not work.
    "spc_pack_flac_50", "spc_pack_mp3_7",
]


@pytest.mark.parametrize("data", WORK_CALLBACKS)
def test_work_callbacks_are_classified_as_work(data):
    assert is_work_callback(data) is True


@pytest.mark.parametrize("data", NAVIGATION_CALLBACKS)
def test_navigation_callbacks_are_not_work(data):
    assert is_work_callback(data) is False


@pytest.mark.parametrize("data", [None, 5, b"dl_video_best", ""])
def test_non_string_payload_is_not_work(data):
    assert is_work_callback(data) is False


def _make_update(data, user_id=4242, chat_id=4242):
    update = Mock()
    update.effective_user.id = user_id
    update.effective_chat.id = chat_id
    query = Mock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update.callback_query = query
    return update


def _context():
    context = Mock()
    context.user_data = {}
    context.bot_data = {}
    return context


def test_eleven_navigation_clicks_never_hit_the_limit(monkeypatch):
    calls = []
    monkeypatch.setattr(tcb, "check_rate_limit", lambda *a: calls.append(a) or False)
    # Resolve to the pl_cancel branch, which needs no session or network.
    for _ in range(11):
        update = _make_update("pl_cancel")
        asyncio.run(tcb.handle_callback(update, _context()))
        update.callback_query.answer.assert_awaited_once_with()
        text = update.callback_query.edit_message_text.await_args.args[0]
        assert text == "Pobieranie playlisty anulowane."
    assert calls == []


def test_work_click_over_limit_shows_toast_and_keeps_menu(monkeypatch):
    monkeypatch.setattr(tcb, "check_rate_limit", lambda *_: False)
    update = _make_update("dl_video_best")

    asyncio.run(tcb.handle_callback(update, _context()))

    update.callback_query.answer.assert_awaited_once_with(RATE_LIMIT_TOAST, show_alert=True)
    update.callback_query.edit_message_text.assert_not_awaited()


def test_work_click_within_limit_answers_once_without_toast(monkeypatch):
    monkeypatch.setattr(tcb, "check_rate_limit", lambda *_: True)
    update = _make_update("dl_video_best")

    asyncio.run(tcb.handle_callback(update, _context()))

    update.callback_query.answer.assert_awaited_once_with()


def test_toast_text():
    assert RATE_LIMIT_TOAST == "Za dużo żądań — poczekaj chwilę i spróbuj ponownie."
