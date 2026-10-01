"""Download- and routing-oriented tests for Telegram callbacks."""

import asyncio
from unittest import mock

import pytest

from bot import telegram_callbacks as tc
from bot.handlers import time_range_callbacks as _trc
from tests.telegram_callbacks_support import _make_context, _make_update


@pytest.mark.parametrize(
    "estimate, seven_zip, transcribe, should_download",
    [(3000, True, False, True), (None, True, False, True),
     (11000, True, False, False), (3000, False, False, False), (3000, True, True, False)],
)
def test_large_single_download_can_reach_archive_offer(
    tmp_path, monkeypatch, estimate, seven_zip, transcribe, should_download,
):
    from types import SimpleNamespace
    from pathlib import Path
    from bot.handlers import download_callbacks as dc

    monkeypatch.setattr(dc, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(dc, "is_7z_available", lambda: seven_zip)
    monkeypatch.setattr(dc, "_mtproto_unavailability_reason", lambda: None)
    monkeypatch.setattr(dc, "get_media_label", lambda _: "filmie")
    monkeypatch.setattr(dc, "_get_session_value", lambda *args: None)
    monkeypatch.setattr(dc, "record_download_for", mock.Mock())
    monkeypatch.setattr(dc, "estimate_download_size", lambda _: estimate)

    def plan(**kwargs):
        return SimpleNamespace(info={"title": "Movie"}, title="Movie", duration_str="1:00",
                               sanitized_title="Movie", chat_download_path=kwargs["chat_download_path"])

    async def download(plan, **kwargs):
        assert kwargs["max_file_bytes"] == 10240 * 1024**2
        file = Path(plan.chat_download_path) / "movie.mp4"
        file.write_bytes(b"stand-in for a large video")
        return SimpleNamespace(file_path=str(file), file_size_mb=3000)

    fetch = mock.AsyncMock(side_effect=download)
    offer = mock.AsyncMock()
    monkeypatch.setattr(dc, "prepare_download_plan", plan)
    monkeypatch.setattr(dc, "execute_download", fetch)
    monkeypatch.setattr(dc, "_offer_archive_or_cancel", offer)
    update, context = _make_update("dl_video_best"), _make_context()
    asyncio.run(dc.download_file(update, context, "video", "best", "https://youtube.com/", transcribe=transcribe))
    assert fetch.await_count == int(should_download)
    assert offer.await_count == int(should_download)
    if should_download:
        assert Path(offer.await_args.kwargs["file_path"]).exists()
    else:
        assert not list(tmp_path.glob("*/dl_*"))


def test_size_failure_removes_only_current_download_workspace(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from pathlib import Path
    from bot.download_budget import DownloadLimitError
    from bot.handlers import download_callbacks as dc

    chat = tmp_path / "123"
    chat.mkdir()
    previous = chat / "keep.mp4"
    previous.write_bytes(b"existing file")
    monkeypatch.setattr(dc, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(dc, "get_media_label", lambda _: "filmie")
    monkeypatch.setattr(dc, "_get_session_value", lambda *args: None)
    monkeypatch.setattr(dc, "record_download_for", mock.Mock())
    monkeypatch.setattr(dc, "estimate_download_size", lambda _: None)

    def plan(**kwargs):
        return SimpleNamespace(info={}, title="Movie", duration_str="1:00", sanitized_title="Movie",
                               chat_download_path=kwargs["chat_download_path"])

    async def download(plan, **kwargs):
        (Path(plan.chat_download_path) / "movie.mp4.part").write_bytes(b"partial")
        raise DownloadLimitError("Pobierany plik przekroczył dozwolony limit rozmiaru.")

    monkeypatch.setattr(dc, "prepare_download_plan", plan)
    monkeypatch.setattr(dc, "execute_download", download)
    update, context = _make_update("dl_video_best"), _make_context()
    asyncio.run(dc.download_file(update, context, "video", "best", "https://youtube.com/"))
    assert previous.read_bytes() == b"existing file"
    assert not list(chat.glob("dl_*"))
    assert "limit rozmiaru" in update.callback_query.edit_message_text.await_args.args[0]


def test_handle_callback_video_and_audio_download_data_dispatch():
    tc.user_urls[555] = "https://www.youtube.com/watch?v=abc"

    audio_update = _make_update("dl_audio_format_140", chat_id=555)
    video_update = _make_update("dl_video_720p", chat_id=555)
    context = _make_context()

    calls = []

    async def fake_download_file(update_arg, context_arg, type_arg, format_arg, url, **kwargs):
        calls.append((type_arg, format_arg, url, kwargs))

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tc, "download_file", fake_download_file)
    try:
        asyncio.run(tc.handle_callback(audio_update, context))
        asyncio.run(tc.handle_callback(video_update, context))
    finally:
        monkeypatch.undo()

    assert ("audio", "140", "https://www.youtube.com/watch?v=abc", {"use_format_id": True}) in calls
    assert ("video", "720p", "https://www.youtube.com/watch?v=abc", {}) in calls


def test_handle_callback_invalid_format_id_does_not_download(monkeypatch):
    tc.user_urls[555] = "https://www.youtube.com/watch?v=abc"
    invalid_update = _make_update("dl_video_bad", chat_id=555)
    context = _make_context()

    called = False

    async def fake_download_file(update_arg, context_arg, type_arg, format_arg, url, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(tc, "download_file", fake_download_file)
    asyncio.run(tc.handle_callback(invalid_update, context))

    assert called is False
    invalid_update.callback_query.edit_message_text.assert_awaited_once_with(
        "Nieobsługiwany format. Spróbuj wybrać format ponownie."
    )


def test_handle_callback_formats_and_summary_option_routes():
    tc.user_urls[777] = "https://www.youtube.com/watch?v=abc"
    context = _make_context()

    format_update = _make_update("formats", chat_id=777)
    summary_update = _make_update("summary_option_4", chat_id=777)

    shown = {}
    transcribed = {}

    async def fake_handle_formats_list(update_arg, context_arg, url):
        shown["formats_url"] = url

    async def fake_download_file(update_arg, context_arg, type_arg, format_arg, url, transcribe=False, summary=False, summary_type=None):
        transcribed["type"] = type_arg
        transcribed["format"] = format_arg
        transcribed["url"] = url
        transcribed["summary"] = summary
        transcribed["summary_type"] = summary_type

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tc, "handle_formats_list", fake_handle_formats_list)
    monkeypatch.setattr(tc, "download_file", fake_download_file)
    try:
        asyncio.run(tc.handle_callback(format_update, context))
        asyncio.run(tc.handle_callback(summary_update, context))
    finally:
        monkeypatch.undo()

    assert shown["formats_url"] == "https://www.youtube.com/watch?v=abc"
    assert transcribed["summary"] is True
    assert transcribed["summary_type"] == 4


def test_handle_callback_summary_option_invalid_shows_warning():
    tc.user_urls[555] = "https://www.youtube.com/watch?v=abc"
    update = _make_update("summary_option_999", chat_id=555)
    context = _make_context()

    called = {}

    async def fake_download_file(update_arg, context_arg, type_arg, format_arg, url, **kwargs):
        called["called"] = True

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tc, "download_file", fake_download_file)
    try:
        asyncio.run(tc.handle_callback(update, context))
    finally:
        monkeypatch.undo()

    update.callback_query.edit_message_text.assert_awaited_once_with("Nieobsługiwana opcja podsumowania.")
    assert "called" not in called


def test_handle_callback_time_range_preset_dispatch(monkeypatch):
    tc.user_urls[999] = "https://www.youtube.com/watch?v=abc"
    update = _make_update("time_range_preset_first_10", chat_id=999)
    context = _make_context()

    dispatched = {}

    async def fake_apply_time_range_preset(update_arg, context_arg, url, preset):
        dispatched["url"] = url
        dispatched["preset"] = preset

    monkeypatch.setattr(tc, "apply_time_range_preset", fake_apply_time_range_preset)
    asyncio.run(tc.handle_callback(update, context))

    update.callback_query.answer.assert_awaited_once()
    assert dispatched["url"] == "https://www.youtube.com/watch?v=abc"
    assert dispatched["preset"] == "first_10"


def test_apply_time_range_preset_first_5_sets_range(monkeypatch):
    chat_id = 111
    update = _make_update("time_range_preset_first_5", chat_id=chat_id)
    context = _make_context()
    url = "https://www.youtube.com/watch?v=abc"
    tc.user_urls[chat_id] = url

    back_calls = {}

    async def fake_back(update_arg, context_arg, back_url):
        back_calls["url"] = back_url

    monkeypatch.setattr(_trc, "get_video_info", lambda *_: {"duration": 370, "title": "Sample"})
    monkeypatch.setattr(_trc, "back_to_main_menu", fake_back)

    asyncio.run(tc.apply_time_range_preset(update, context, url, "first_5"))

    assert tc.user_time_ranges[chat_id] == {
        "start": "0:00",
        "end": "5:00",
        "start_sec": 0,
        "end_sec": 300,
    }
    assert back_calls["url"] == url


def test_apply_time_range_preset_zero_duration_shows_error(monkeypatch):
    chat_id = 222
    update = _make_update("time_range_preset_last_5", chat_id=chat_id)
    context = _make_context()
    tc.user_urls[chat_id] = "https://www.youtube.com/watch?v=abc"

    monkeypatch.setattr(_trc, "get_video_info", lambda *_: {"duration": 0})
    asyncio.run(tc.apply_time_range_preset(update, context, tc.user_urls[chat_id], "last_5"))

    update.callback_query.edit_message_text.assert_awaited_once_with(
        "Nie można określić czasu trwania filmu."
    )


def test_handle_callback_time_range_options_and_clear():
    tc.user_urls[888] = "https://www.youtube.com/watch?v=abc"
    tc.user_time_ranges[888] = {"start": "0:10", "end": "1:00", "start_sec": 10, "end_sec": 60}
    context = _make_context()

    shown = {}
    back_called = {}

    async def fake_show_time_range_options(update_arg, context_arg, url):
        shown["time_range_url"] = url

    async def fake_back(update_arg, context_arg, url):
        back_called["url"] = url

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tc, "show_time_range_options", fake_show_time_range_options)
    monkeypatch.setattr(tc, "back_to_main_menu", fake_back)
    try:
        asyncio.run(tc.handle_callback(_make_update("time_range", chat_id=888), context))
        asyncio.run(tc.handle_callback(_make_update("time_range_clear", chat_id=888), context))
    finally:
        monkeypatch.undo()

    assert shown["time_range_url"] == "https://www.youtube.com/watch?v=abc"
    assert back_called["url"] == "https://www.youtube.com/watch?v=abc"
    assert 888 not in tc.user_time_ranges


def test_offer_archive_or_cancel_registers_pending_job(tmp_path, monkeypatch):
    """_offer_archive_or_cancel registers state and shows two-button keyboard."""
    from bot.handlers import download_callbacks
    from bot.session_store import pending_archive_jobs, session_store

    session_store.reset()
    pretend = tmp_path / "big.mp4"
    pretend.write_bytes(b"x")

    monkeypatch.setattr(
        download_callbacks, "_mtproto_unavailability_reason", lambda: None
    )

    update = mock.MagicMock()
    update.callback_query = mock.MagicMock()
    update.callback_query.edit_message_text = mock.AsyncMock()
    context = mock.MagicMock()

    import asyncio
    asyncio.run(
        download_callbacks._offer_archive_or_cancel(
            update,
            context,
            chat_id=99,
            file_path=str(pretend),
            title="big-file",
            media_type="video",
            format_choice="best",
            file_size_mb=1500.0,
        )
    )

    # State registered.
    bucket = pending_archive_jobs.get(99) or {}
    assert len(bucket) == 1
    state = next(iter(bucket.values()))
    assert state.title == "big-file"
    assert state.file_size_mb == 1500.0

    # Two buttons shown.
    update.callback_query.edit_message_text.assert_awaited_once()
    sent_text, sent_kwargs = update.callback_query.edit_message_text.await_args.args, update.callback_query.edit_message_text.await_args.kwargs
    keyboard = sent_kwargs["reply_markup"]
    callback_data = [btn.callback_data for row in keyboard.inline_keyboard for btn in row]
    assert any(cb.startswith("arc_split_") for cb in callback_data)
    assert any(cb.startswith("arc_cancel_") for cb in callback_data)
    assert sent_text[0].endswith("i wysłać części.")

    session_store.reset()


def test_arc_cancel_removes_file_immediately(tmp_path, monkeypatch):
    from bot.handlers import download_callbacks
    from bot.session_store import (
        ArchiveJobState,
        pending_archive_jobs,
        session_store,
    )
    from datetime import datetime
    from pathlib import Path

    session_store.reset()
    src = tmp_path / "to_cancel.mp4"
    src.write_bytes(b"x")
    state = ArchiveJobState(
        file_path=src,
        title="x", media_type="video", format_choice="best",
        file_size_mb=200.0, use_mtproto=False,
        created_at=datetime(2026, 5, 2),
    )
    pending_archive_jobs[7] = {"tok": state}

    update = mock.MagicMock()
    update.effective_chat.id = 7
    update.callback_query = mock.MagicMock()
    update.callback_query.edit_message_text = mock.AsyncMock()
    context = mock.MagicMock()

    import asyncio
    asyncio.run(
        download_callbacks.handle_archive_callback(
            update, context, "arc_cancel_tok"
        )
    )

    assert not src.exists()
    assert pending_archive_jobs.get(7, {}).get("tok") is None
    session_store.reset()


def test_arc_split_dispatches_to_archive_service(tmp_path, monkeypatch):
    from bot.handlers import download_callbacks
    from bot.session_store import (
        ArchiveJobState,
        pending_archive_jobs,
        session_store,
    )
    from datetime import datetime

    session_store.reset()
    src = tmp_path / "x.mp4"
    src.write_bytes(b"x")
    pending_archive_jobs[7] = {"tok2": ArchiveJobState(
        file_path=src, title="x", media_type="video", format_choice="best",
        file_size_mb=200.0, use_mtproto=False,
        created_at=datetime(2026, 5, 2),
    )}

    fake_flow = mock.AsyncMock()
    monkeypatch.setattr(
        download_callbacks, "execute_single_file_archive_flow", fake_flow
    )

    update = mock.MagicMock()
    update.effective_chat.id = 7
    update.callback_query = mock.MagicMock()
    update.callback_query.edit_message_text = mock.AsyncMock()
    context = mock.MagicMock()

    import asyncio
    asyncio.run(
        download_callbacks.handle_archive_callback(
            update, context, "arc_split_tok2"
        )
    )

    assert fake_flow.await_count == 1
    kwargs = fake_flow.await_args.kwargs
    assert kwargs["chat_id"] == 7
    assert kwargs["token"] == "tok2"
    session_store.reset()


def test_arc_resend_calls_send_volumes_with_index(tmp_path, monkeypatch):
    from bot.handlers import download_callbacks
    from bot.session_store import (
        ArchivedDeliveryState,
        archived_deliveries,
        session_store,
    )
    from datetime import datetime
    from pathlib import Path

    session_store.reset()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    v1 = workspace / "x.7z.001"
    v1.write_bytes(b"a")
    v2 = workspace / "x.7z.002"
    v2.write_bytes(b"a")
    archived_deliveries[5] = {"tk": ArchivedDeliveryState(
        workspace=workspace, volumes=[v1, v2],
        caption_prefix="X", use_mtproto=True,
        created_at=datetime(2026, 5, 2),
    )}

    sent = mock.AsyncMock()
    monkeypatch.setattr(download_callbacks, "send_volumes", sent)

    update = mock.MagicMock()
    update.effective_chat.id = 5
    update.callback_query = mock.MagicMock()
    update.callback_query.edit_message_text = mock.AsyncMock()
    context = mock.MagicMock()
    context.bot = mock.MagicMock()

    import asyncio
    asyncio.run(
        download_callbacks.handle_archive_callback(
            update, context, "arc_resend_tk_1"
        )
    )

    assert sent.await_count == 1
    assert sent.await_args.kwargs["start_index"] == 1
    update.callback_query.edit_message_text.assert_awaited_with("Wysłano części od [2/2].")
    session_store.reset()


def test_arc_purge_removes_workspace(tmp_path, monkeypatch):
    from bot.handlers import download_callbacks
    from bot.session_store import (
        ArchivedDeliveryState,
        archived_deliveries,
        session_store,
    )
    from datetime import datetime

    session_store.reset()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "x.7z.001").write_bytes(b"a")
    archived_deliveries[5] = {"tk2": ArchivedDeliveryState(
        workspace=workspace, volumes=[workspace / "x.7z.001"],
        caption_prefix="X", use_mtproto=True,
        created_at=datetime(2026, 5, 2),
    )}

    update = mock.MagicMock()
    update.effective_chat.id = 5
    update.callback_query = mock.MagicMock()
    update.callback_query.edit_message_text = mock.AsyncMock()
    context = mock.MagicMock()

    import asyncio
    asyncio.run(
        download_callbacks.handle_archive_callback(
            update, context, "arc_purge_tk2"
        )
    )

    assert not workspace.exists()
    assert archived_deliveries.get(5, {}).get("tk2") is None
    session_store.reset()


def test_download_file_registers_and_unregisters_job(tmp_path, monkeypatch):
    import asyncio
    from bot.handlers import download_callbacks
    from bot.jobs import JobRegistry
    from bot.session_store import session_store

    session_store.reset()
    test_registry = JobRegistry()
    monkeypatch.setattr(download_callbacks, "job_registry", test_registry)

    # Patch prepare_download_plan to short-circuit by returning None.
    monkeypatch.setattr(
        download_callbacks, "prepare_download_plan", lambda **kw: None,
    )

    update = mock.MagicMock()
    update.callback_query = mock.MagicMock()
    update.callback_query.edit_message_text = mock.AsyncMock()
    update.effective_chat.id = 7
    context = mock.MagicMock()

    asyncio.run(
        download_callbacks.download_file(
            update, context,
            type="video", format="best", url="https://x",
        )
    )

    # Even on early-exit path the job should register and unregister.
    assert test_registry.list_for_chat(7) == []
    session_store.reset()


def _patch_single_download(monkeypatch, tmp_path, *, filename, size_mb=5):
    """Stub the yt-dlp side of download_file so only the send path runs."""

    from pathlib import Path
    from types import SimpleNamespace
    from bot.handlers import download_callbacks as dc

    monkeypatch.setattr(dc, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(dc, "is_7z_available", lambda: False)
    monkeypatch.setattr(dc, "_mtproto_unavailability_reason", lambda: None)
    monkeypatch.setattr(dc, "get_media_label", lambda _: "filmie")
    monkeypatch.setattr(dc, "_get_session_value", lambda *args: None)
    monkeypatch.setattr(dc, "record_download_for", mock.Mock())
    monkeypatch.setattr(dc, "estimate_download_size", lambda _: size_mb)
    monkeypatch.setattr(dc, "download_thumbnail", lambda *args: None)

    def plan(**kwargs):
        plan.kwargs = kwargs
        return SimpleNamespace(info={"title": "Song"}, title="Song", duration_str="3:00",
                               sanitized_title="Song", chat_download_path=kwargs["chat_download_path"])

    async def download(plan_obj, **kwargs):
        file = Path(plan_obj.chat_download_path) / filename
        file.write_bytes(b"audio bytes")
        return SimpleNamespace(file_path=str(file), file_size_mb=size_mb)

    monkeypatch.setattr(dc, "prepare_download_plan", plan)
    monkeypatch.setattr(dc, "execute_download", download)
    return dc, plan


def test_download_file_audio_sends_through_trim_helper(tmp_path, monkeypatch):
    from types import SimpleNamespace

    dc, _plan = _patch_single_download(monkeypatch, tmp_path, filename="song.mp3")
    sender = mock.AsyncMock(return_value=SimpleNamespace(token="AAAAAAAAAAA"))
    monkeypatch.setattr(dc, "send_audio_with_trim", sender)
    update, context = _make_update("dl_audio_mp3"), _make_context()

    asyncio.run(dc.download_file(update, context, "audio", "mp3", "https://youtube.com/"))

    assert sender.await_args.kwargs["title"] == "Song"
    assert sender.await_args.args[2].endswith("song.mp3")
    final = update.callback_query.edit_message_text.await_args.args[0]
    assert final.startswith("Plik został wysłany!")
    assert "✂️ Pod plikiem jest przycisk „Przytnij”" in final


def test_download_file_audio_without_trim_keeps_plain_status(tmp_path, monkeypatch):
    dc, _plan = _patch_single_download(monkeypatch, tmp_path, filename="song.mp3")
    monkeypatch.setattr(dc, "send_audio_with_trim", mock.AsyncMock(return_value=None))
    update, context = _make_update("dl_audio_mp3"), _make_context()

    asyncio.run(dc.download_file(update, context, "audio", "mp3", "https://youtube.com/"))

    assert update.callback_query.edit_message_text.await_args.args[0] == "Plik został wysłany!"


def test_download_file_audio_reports_delivery_error(tmp_path, monkeypatch):
    from bot.handlers.audio_delivery import AudioDeliveryError

    dc, _plan = _patch_single_download(monkeypatch, tmp_path, filename="song.mp3")
    monkeypatch.setattr(
        dc, "send_audio_with_trim",
        mock.AsyncMock(side_effect=AudioDeliveryError("Plik za duży dla Bot API (60 MB, limit: 50 MB).\nBrak pyrogram.")),
    )
    update, context = _make_update("dl_audio_mp3"), _make_context()

    asyncio.run(dc.download_file(update, context, "audio", "mp3", "https://youtube.com/"))

    assert "Brak pyrogram." in update.callback_query.edit_message_text.await_args.args[0]


def test_download_file_trim_after_keeps_file_and_prompts(tmp_path, monkeypatch):
    from pathlib import Path

    dc, plan = _patch_single_download(monkeypatch, tmp_path, filename="episode.mp3")
    monkeypatch.setattr(
        dc, "_get_session_value",
        lambda *args: {"start": "0:10", "end": "0:20", "start_sec": 10, "end_sec": 20},
    )
    offered = {}

    async def fake_offer(context, **kwargs):
        offered.update(kwargs)
        offered["exists"] = Path(kwargs["file_path"]).exists()
        return True

    monkeypatch.setattr(dc, "offer_trim_after_download", fake_offer)
    sender = mock.AsyncMock()
    monkeypatch.setattr(dc, "send_audio_with_trim", sender)
    update, context = _make_update("trim_dl"), _make_context()
    update.effective_user.id = 123

    asyncio.run(dc.download_file(update, context, "audio", "mp3", "https://castbox.fm/x", trim_after=True))

    assert plan.kwargs["time_range"] is None
    assert offered["exists"] is True
    assert (offered["title"], offered["requester_id"]) == ("Song", 123)
    sender.assert_not_awaited()
    dc.record_download_for.assert_called_once()


def test_time_range_menu_lists_open_range_examples(monkeypatch):
    monkeypatch.setattr(_trc, "get_video_info", lambda _url: {"title": "Clip", "duration": 600})
    update, context = _make_update("time_range"), _make_context()

    asyncio.run(_trc.show_time_range_options(update, context, "https://youtube.com/watch?v=x"))

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "`2:15-` (do końca)" in text
    assert "`-5:00` (od początku)" in text


_SESSION_RANGE = {"start": "0:11", "end": "0:41", "start_sec": 11, "end_sec": 41}


def test_download_file_audio_with_range_downloads_whole_and_cuts_locally(tmp_path, monkeypatch):
    from types import SimpleNamespace

    dc, plan = _patch_single_download(monkeypatch, tmp_path, filename="song.mp3")
    monkeypatch.setattr(dc, "_get_session_value", lambda *args: dict(_SESSION_RANGE))
    range_sender = mock.AsyncMock(return_value=SimpleNamespace(token="AAAAAAAAAAA"))
    full_sender = mock.AsyncMock()
    monkeypatch.setattr(dc, "send_audio_range_with_trim", range_sender)
    monkeypatch.setattr(dc, "send_audio_with_trim", full_sender)
    update, context = _make_update("dl_audio_mp3"), _make_context()

    asyncio.run(dc.download_file(update, context, "audio", "mp3", "https://youtube.com/"))

    # yt-dlp fetches the whole track so ✂️ under the fragment can trim the original.
    assert plan.kwargs["time_range"] is None
    kwargs = range_sender.await_args.kwargs
    assert (kwargs["start_sec"], kwargs["end_sec"], kwargs["title"]) == (11, 41, "Song")
    full_sender.assert_not_awaited()
    final = update.callback_query.edit_message_text.await_args.args[0]
    assert "tnie pełny oryginał" in final
    assert dc.record_download_for.call_args.args[6] == _SESSION_RANGE


def test_download_file_video_with_range_keeps_ytdlp_sections(tmp_path, monkeypatch):
    dc, plan = _patch_single_download(monkeypatch, tmp_path, filename="clip.mp4")
    monkeypatch.setattr(dc, "_get_session_value", lambda *args: dict(_SESSION_RANGE))
    range_sender = mock.AsyncMock()
    monkeypatch.setattr(dc, "send_audio_range_with_trim", range_sender)
    update, context = _make_update("dl_video_720p"), _make_context()

    asyncio.run(dc.download_file(update, context, "video", "720p", "https://youtube.com/"))

    assert plan.kwargs["time_range"] == _SESSION_RANGE
    range_sender.assert_not_awaited()


def _markup_of(call):
    return call.kwargs.get("reply_markup")


def test_download_file_progress_has_stop_button_and_final_has_none(tmp_path, monkeypatch):
    from bot.jobs import JobRegistry

    dc, _plan = _patch_single_download(monkeypatch, tmp_path, filename="song.mp3")
    registry = JobRegistry()
    monkeypatch.setattr(dc, "job_registry", registry)
    monkeypatch.setattr(dc, "send_audio_with_trim", mock.AsyncMock(return_value=None))
    update, context = _make_update("dl_audio_mp3"), _make_context()
    seen = {}

    async def spy_download(plan_obj, **kwargs):
        seen["job_id"] = kwargs["cancellation"].job_id
        return await download_orig(plan_obj, **kwargs)

    download_orig = dc.execute_download
    monkeypatch.setattr(dc, "execute_download", spy_download)

    asyncio.run(dc.download_file(update, context, "audio", "mp3", "https://youtube.com/"))

    calls = update.callback_query.edit_message_text.await_args_list
    progress = [c for c in calls if c.args[0].startswith("Rozpoczynam pobieranie")]
    assert progress
    button = _markup_of(progress[0]).inline_keyboard[0][0]
    assert button.text == "⏹ Zatrzymaj"
    assert button.callback_data == f"stop_{seen['job_id']}"
    assert calls[-1].args[0] == "Plik został wysłany!"
    assert _markup_of(calls[-1]) is None


def test_download_file_stopped_mid_download_reports_stop_not_failure(tmp_path, monkeypatch):
    import yt_dlp
    from bot.jobs import JobRegistry

    dc, _plan = _patch_single_download(monkeypatch, tmp_path, filename="song.mp3")
    registry = JobRegistry()
    monkeypatch.setattr(dc, "job_registry", registry)

    async def stopped_download(plan_obj, **kwargs):
        kwargs["cancellation"].event.set()
        raise yt_dlp.utils.DownloadError("cancelled by user")

    monkeypatch.setattr(dc, "execute_download", stopped_download)
    update, context = _make_update("dl_audio_mp3"), _make_context()

    asyncio.run(dc.download_file(update, context, "audio", "mp3", "https://youtube.com/"))

    last = update.callback_query.edit_message_text.await_args
    assert last.args[0] == "⏹ Zatrzymano pobieranie."
    assert _markup_of(last) is None
    dc.record_download_for.assert_not_called()


def test_download_error_without_stop_still_records_failure(tmp_path, monkeypatch):
    import yt_dlp

    dc, _plan = _patch_single_download(monkeypatch, tmp_path, filename="song.mp3")

    async def broken_download(plan_obj, **kwargs):
        raise yt_dlp.utils.DownloadError("boom")

    monkeypatch.setattr(dc, "execute_download", broken_download)
    update, context = _make_update("dl_audio_mp3"), _make_context()

    asyncio.run(dc.download_file(update, context, "audio", "mp3", "https://youtube.com/"))

    assert update.callback_query.edit_message_text.await_args.args[0].startswith("Wystąpił błąd")
    assert dc.record_download_for.call_args.kwargs["status"] == "failure"


def test_missing_groq_key_message_has_no_stop_button(tmp_path, monkeypatch):
    dc, _plan = _patch_single_download(monkeypatch, tmp_path, filename="song.mp3")
    monkeypatch.setattr(dc, "get_runtime_value", lambda key, default="": "")
    update, context = _make_update("dl_audio_mp3"), _make_context()

    asyncio.run(dc.download_file(
        update, context, "audio", "mp3", "https://youtube.com/", transcribe=True,
    ))

    last = update.callback_query.edit_message_text.await_args
    assert last.args[0].startswith("Funkcja niedostępna")
    assert _markup_of(last) is None


def test_progress_edit_after_stop_signal_has_no_stop_button(tmp_path, monkeypatch):
    dc, _plan = _patch_single_download(monkeypatch, tmp_path, filename="song.mp3")
    monkeypatch.setattr(dc, "send_audio_with_trim", mock.AsyncMock(return_value=None))
    update, context = _make_update("dl_audio_mp3"), _make_context()
    download_orig = dc.execute_download

    async def stop_then_download(plan_obj, **kwargs):
        kwargs["cancellation"].event.set()
        await kwargs["status_callback"]("Pobieranie: 50%")
        return await download_orig(plan_obj, **kwargs)

    monkeypatch.setattr(dc, "execute_download", stop_then_download)

    asyncio.run(dc.download_file(update, context, "audio", "mp3", "https://youtube.com/"))

    calls = update.callback_query.edit_message_text.await_args_list
    stopping = [c for c in calls if c.args[0] == "Pobieranie: 50%"]
    assert stopping and _markup_of(stopping[0]) is None


def _fake_keys(**values):
    return lambda key, default="": values.get(key, default)


def test_link_transcription_checks_groq_key_before_downloading(tmp_path, monkeypatch):
    dc, _plan = _patch_single_download(monkeypatch, tmp_path, filename="song.mp3")
    monkeypatch.setattr(dc, "get_runtime_value", _fake_keys(CLAUDE_API_KEY="c"))
    prepare = mock.Mock()
    monkeypatch.setattr(dc, "prepare_download_plan", prepare)
    download = mock.AsyncMock()
    monkeypatch.setattr(dc, "execute_download", download)
    update, context = _make_update("dl_audio_mp3"), _make_context()

    asyncio.run(dc.download_file(
        update, context, "audio", "mp3", "https://youtube.com/", transcribe=True,
    ))

    download.assert_not_awaited()
    prepare.assert_not_called()
    last = update.callback_query.edit_message_text.await_args
    assert last.args[0] == (
        "Funkcja niedostępna — brak klucza API do transkrypcji. Skontaktuj się z administratorem."
    )
    assert _markup_of(last) is None


def test_link_summary_checks_claude_key_before_downloading(tmp_path, monkeypatch):
    dc, _plan = _patch_single_download(monkeypatch, tmp_path, filename="song.mp3")
    monkeypatch.setattr(dc, "get_runtime_value", _fake_keys(GROQ_API_KEY="g"))
    download = mock.AsyncMock()
    monkeypatch.setattr(dc, "execute_download", download)
    update, context = _make_update("dl_audio_mp3"), _make_context()

    asyncio.run(dc.download_file(
        update, context, "audio", "mp3", "https://youtube.com/",
        transcribe=True, summary=True, summary_type=1,
    ))

    download.assert_not_awaited()
    last = update.callback_query.edit_message_text.await_args
    assert last.args[0] == (
        "Podsumowanie jest niedostępne — brak klucza API Claude. "
        "Wybierz samą transkrypcję albo skontaktuj się z administratorem."
    )
    assert _markup_of(last) is None


def test_link_summary_failure_still_sends_transcript(tmp_path, monkeypatch):
    dc, _plan = _patch_single_download(monkeypatch, tmp_path, filename="song.mp3")
    monkeypatch.setattr(dc, "get_runtime_value", _fake_keys(GROQ_API_KEY="g", CLAUDE_API_KEY="c"))
    transcript = tmp_path / "Song_transcript.md"
    transcript.write_text("# Song\n\nTekst.\n", encoding="utf-8")
    monkeypatch.setattr(dc, "run_transcription_with_progress", mock.AsyncMock(return_value=str(transcript)))
    monkeypatch.setattr(dc, "generate_summary_artifact", mock.AsyncMock(return_value=None))
    offer = mock.AsyncMock()
    monkeypatch.setattr(dc, "offer_custom_transcript_prompt", offer)
    update, context = _make_update("dl_audio_mp3"), _make_context()

    asyncio.run(dc.download_file(
        update, context, "audio", "mp3", "https://youtube.com/",
        transcribe=True, summary=True, summary_type=1,
    ))

    context.bot.send_document.assert_awaited_once()
    texts = [c.args[0] for c in update.callback_query.edit_message_text.await_args_list]
    assert (
        "Transkrypcja gotowa, ale nie udało się wygenerować podsumowania. "
        "Wysyłam samą transkrypcję."
    ) in texts
    assert dc.record_download_for.call_args.args[4] == "transcription"
    offer.assert_awaited_once()


def test_link_summary_exception_still_sends_transcript(tmp_path, monkeypatch):
    dc, _plan = _patch_single_download(monkeypatch, tmp_path, filename="song.mp3")
    monkeypatch.setattr(dc, "get_runtime_value", _fake_keys(GROQ_API_KEY="g", CLAUDE_API_KEY="c"))
    transcript = tmp_path / "Song_transcript.md"
    transcript.write_text("# Song\n\nTekst.\n", encoding="utf-8")
    monkeypatch.setattr(dc, "run_transcription_with_progress", mock.AsyncMock(return_value=str(transcript)))
    monkeypatch.setattr(dc, "generate_summary_artifact", mock.AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(dc, "offer_custom_transcript_prompt", mock.AsyncMock())
    update, context = _make_update("dl_audio_mp3"), _make_context()

    asyncio.run(dc.download_file(
        update, context, "audio", "mp3", "https://youtube.com/",
        transcribe=True, summary=True, summary_type=1,
    ))

    context.bot.send_document.assert_awaited_once()


def test_plain_audio_download_ignores_missing_api_keys(tmp_path, monkeypatch):
    dc, _plan = _patch_single_download(monkeypatch, tmp_path, filename="song.mp3")
    monkeypatch.setattr(dc, "get_runtime_value", _fake_keys())
    sender = mock.AsyncMock(return_value=None)
    monkeypatch.setattr(dc, "send_audio_with_trim", sender)
    update, context = _make_update("dl_audio_mp3"), _make_context()

    asyncio.run(dc.download_file(update, context, "audio", "mp3", "https://youtube.com/"))

    sender.assert_awaited_once()
    assert update.callback_query.edit_message_text.await_args.args[0].startswith("Plik został wysłany!")
