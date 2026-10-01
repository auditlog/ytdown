"""Tests for the ✂️ trim flow: prompt, pending input and the fragment job."""

import asyncio
import inspect
from collections import namedtuple
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from bot.handlers import trim_callbacks as tcb
from bot.handlers.time_range import ResolvedRange
from bot.jobs import JobDescriptor, job_registry
from bot.services import trim_store
from bot.services.audio_trim_service import AudioTrimError
from bot.session_store import PendingTranscriptPrompt, PendingTrimInput
from tests.telegram_callbacks_support import _make_context, _make_update

_Usage = namedtuple("usage", "total used free")
CHAT = 42
USER = 42


@pytest.fixture
def store(tmp_path, monkeypatch):
    root = tmp_path / "downloads"
    root.mkdir()
    monkeypatch.setattr(trim_store, "DOWNLOAD_PATH", str(root))
    monkeypatch.setattr(trim_store.shutil, "disk_usage", lambda _p: _Usage(100 * 1024**3, 0, 50 * 1024**3))
    monkeypatch.setattr(tcb, "_is_authorized", lambda _context, _user_id: True)
    monkeypatch.setattr(tcb, "record_download_for", lambda *a, **k: None)
    return root


@pytest.fixture(autouse=True)
def _clean_job_registry():
    # job_registry is a process-wide singleton; a test that stubs run_trim_job
    # leaves its pre-registered job behind, so drop it before the next test.
    yield
    for job in job_registry.list_for_chat(CHAT):
        job_registry.unregister(job.job_id)


def _scheduling_context():
    """Context whose application.create_task schedules on the running loop, like PTB."""

    context = _make_context()
    tasks = []

    def create_task(coroutine, update=None, **kwargs):
        task = asyncio.get_running_loop().create_task(coroutine)
        tasks.append(task)
        return task

    context.application.create_task = Mock(side_effect=create_task)
    return context, tasks


def _source(tmp_path, *, title="Podcast #120", duration=6130):
    audio = tmp_path / "episode.mp3"
    audio.write_bytes(b"ID3 audio")
    return trim_store.retain_source(CHAT, audio, title=title, performer="Host", duration_sec=duration)


def _callback(data):
    update = _make_update(data, chat_id=CHAT)
    update.effective_user.id = USER
    return update


def _text_update(text):
    update = Mock()
    update.effective_chat.id = CHAT
    update.effective_user.id = USER
    update.message = Mock()
    update.message.text = text
    status = Mock(edit_text=AsyncMock())
    update.message.reply_text = AsyncMock(return_value=status)
    return update, status


def _pending(context, source, *, age=timedelta(0), requester=USER):
    context.user_data["pending_trim"] = PendingTrimInput(source.token, requester, datetime.now(UTC) - age)


# --- prompt and callbacks ---------------------------------------------------


def test_trim_src_sends_prompt_and_sets_pending(store, tmp_path):
    source = _source(tmp_path)
    context = _make_context()
    data = f"trim_src_{source.token}"

    asyncio.run(tcb.handle_trim_callback(_callback(data), context, data))

    kwargs = context.bot.send_message.await_args.kwargs
    assert "Długość: 1:42:10" in kwargs["text"]
    assert "`2:15-` — od 2:15 do końca" in kwargs["text"]
    assert kwargs["parse_mode"] == "Markdown"
    assert kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "trim_cancel"
    pending = context.user_data["pending_trim"]
    assert (pending.token, pending.requester_id) == (source.token, USER)


def test_prompt_escapes_markdown_in_title(store, tmp_path):
    source = _source(tmp_path, title="a_b*c [x]")
    context = _make_context()
    asyncio.run(tcb.start_trim_prompt(context, chat_id=CHAT, requester_id=USER, source=source))
    assert "a\\_b\\*c \\[x]" in context.bot.send_message.await_args.kwargs["text"]


def test_prompt_supersedes_pending_transcript_prompt(store, tmp_path):
    context = _make_context()
    context.user_data["pending_transcript_prompt"] = PendingTranscriptPrompt("tok", USER)
    asyncio.run(tcb.start_trim_prompt(context, chat_id=CHAT, requester_id=USER, source=_source(tmp_path)))
    assert "pending_transcript_prompt" not in context.user_data


def test_prompt_with_query_edits_callback_message(store, tmp_path):
    source = _source(tmp_path)
    context = _make_context()
    update = _callback("trim_dl")
    asyncio.run(tcb.start_trim_prompt(
        context, chat_id=CHAT, requester_id=USER, source=source,
        query=update.callback_query, intro="Pobrano całość.\n\n",
    ))
    text = update.callback_query.edit_message_text.await_args.args[0]
    assert text.startswith("Pobrano całość.\n\n✂️ Przycinanie:")
    context.bot.send_message.assert_not_awaited()


def test_trim_src_with_unknown_token_reports_expiry(store):
    context = _make_context()
    asyncio.run(tcb.handle_trim_callback(_callback("trim_src_AAAAAAAAAAA"), context, "trim_src_AAAAAAAAAAA"))
    assert context.bot.send_message.await_args.kwargs["text"] == tcb.EXPIRED_TEXT
    assert "pending_trim" not in context.user_data


def test_trim_callback_requires_authorization(store, tmp_path, monkeypatch):
    source = _source(tmp_path)
    monkeypatch.setattr(tcb, "_is_authorized", lambda *_: False)
    context = _make_context()
    data = f"trim_src_{source.token}"
    asyncio.run(tcb.handle_trim_callback(_callback(data), context, data))
    assert context.bot.send_message.await_args.kwargs["text"] == tcb.AUTH_REQUIRED_TEXT
    assert "pending_trim" not in context.user_data


def test_trim_cancel_clears_pending(store, tmp_path):
    context = _make_context()
    _pending(context, _source(tmp_path))
    update = _callback("trim_cancel")
    asyncio.run(tcb.handle_trim_callback(update, context, "trim_cancel"))
    assert "pending_trim" not in context.user_data
    assert update.callback_query.edit_message_text.await_args.args[0] == "Anulowano przycinanie."


def test_trim_cancel_without_pending_reports_inactive(store):
    update = _callback("trim_cancel")
    asyncio.run(tcb.handle_trim_callback(update, _make_context(), "trim_cancel"))
    assert update.callback_query.edit_message_text.await_args.args[0] == "Ta prośba nie jest już aktywna."


def test_fragments_phrase_uses_polish_plurals():
    assert [tcb.fragments_phrase(n) for n in (1, 2, 4, 5, 12, 22, 25)] == [
        "1 fragment", "2 fragmenty", "4 fragmenty", "5 fragmentów",
        "12 fragmentów", "22 fragmenty", "25 fragmentów",
    ]


# --- pending text input ------------------------------------------------------


def test_pending_input_runs_trim_job_with_resolved_ranges(store, tmp_path, monkeypatch):
    source = _source(tmp_path)
    context, tasks = _scheduling_context()
    _pending(context, source)
    run = AsyncMock()
    monkeypatch.setattr(tcb, "run_trim_job", run)
    monkeypatch.setattr(tcb, "check_rate_limit", lambda *_: True)
    update, status = _text_update("12:00-15:30, 1:20:00-")

    async def scenario():
        handled = await tcb.handle_pending_trim_input(update, context)
        await asyncio.gather(*tasks)
        return handled

    assert asyncio.run(scenario()) is True

    # The job runs as a PTB task so /stop can be processed while it cuts.
    assert context.application.create_task.call_args.kwargs["update"] is update
    kwargs = run.await_args.kwargs
    assert kwargs["ranges"] == [ResolvedRange(720, 930, False), ResolvedRange(4800, 6130, True)]
    assert kwargs["status_message"] is status
    assert kwargs["source"] == source
    [job] = job_registry.list_for_chat(CHAT)
    assert kwargs["cancellation"].job_id == job.job_id
    assert "pending_trim" not in context.user_data
    assert update.message.reply_text.await_args.args[0] == "✂️ Przygotowuję 2 fragmenty..."


def test_pending_input_registers_job_before_the_task_runs(store, tmp_path, monkeypatch):
    source = _source(tmp_path)
    context = _make_context()
    _pending(context, source)
    monkeypatch.setattr(tcb, "check_rate_limit", lambda *_: True)
    monkeypatch.setattr(tcb, "max_sendable_audio_mb", lambda: 50)
    monkeypatch.setattr(tcb, "send_audio_file", AsyncMock())
    cuts = []

    async def recording_cut(src, fragment, dest, *, title_tag, cancellation=None):
        cuts.append(cancellation)
        return await _write_fragment(src, fragment, dest, title_tag=title_tag)

    monkeypatch.setattr(tcb, "cut_fragment", recording_cut)
    seen = {}
    tasks = []

    def create_task(coroutine, update=None, **kwargs):
        seen["jobs"] = [(job.kind, job.label) for job in job_registry.list_for_chat(CHAT)]
        seen["cuts"] = len(cuts)
        task = asyncio.get_running_loop().create_task(coroutine)
        tasks.append(task)
        return task

    context.application.create_task = create_task
    first, _ = _text_update("1:00-2:00")
    second, _ = _text_update("3:00-4:00")

    async def scenario():
        assert await tcb.handle_pending_trim_input(first, context) is True
        # The user reopens the prompt (✂️ under the audio) before the task started.
        _pending(context, source)
        assert await tcb.handle_pending_trim_input(second, context) is True
        await asyncio.gather(*tasks)

    asyncio.run(scenario())

    assert seen == {"jobs": [("trim", "Przycinanie: Podcast #120")], "cuts": 0}
    assert second.message.reply_text.await_args.args[0] == tcb.BUSY_TEXT
    assert len(tasks) == 1 and len(cuts) == 1
    assert cuts[0] is not None
    assert job_registry.list_for_chat(CHAT) == []


def test_pending_input_unregisters_job_when_scheduling_fails(store, tmp_path, monkeypatch):
    context = _make_context()
    _pending(context, _source(tmp_path))
    monkeypatch.setattr(tcb, "check_rate_limit", lambda *_: True)
    scheduled = []

    def failing_create_task(coroutine, update=None, **kwargs):
        scheduled.append(coroutine)
        raise RuntimeError("application is shutting down")

    context.application.create_task = failing_create_task
    update, status = _text_update("1:00-2:00")

    assert asyncio.run(tcb.handle_pending_trim_input(update, context)) is True

    assert job_registry.list_for_chat(CHAT) == []
    assert status.edit_text.await_args.args[0] == "Nie udało się uruchomić przycinania. Spróbuj ponownie."
    # The unscheduled coroutine is closed, so no "never awaited" warning is emitted.
    assert inspect.getcoroutinestate(scheduled[0]) == inspect.CORO_CLOSED


def test_pending_input_reports_range_error_and_keeps_pending(store, tmp_path, monkeypatch):
    context = _make_context()
    _pending(context, _source(tmp_path))
    run = AsyncMock()
    monkeypatch.setattr(tcb, "run_trim_job", run)
    update, _ = _text_update("5:00-2:00")

    assert asyncio.run(tcb.handle_pending_trim_input(update, context)) is True

    call = update.message.reply_text.await_args
    assert call.args[0] == "W zakresie 5:00-2:00 początek musi być wcześniej niż koniec."
    assert "parse_mode" not in call.kwargs
    assert "pending_trim" in context.user_data
    run.assert_not_awaited()


def test_pending_input_ignores_other_users(store, tmp_path):
    context = _make_context()
    _pending(context, _source(tmp_path), requester=99)
    update, _ = _text_update("1:00-2:00")
    assert asyncio.run(tcb.handle_pending_trim_input(update, context)) is False


def test_pending_input_url_cancels_and_falls_through(store, tmp_path):
    context = _make_context()
    _pending(context, _source(tmp_path))
    update, _ = _text_update("https://www.youtube.com/watch?v=abc")
    assert asyncio.run(tcb.handle_pending_trim_input(update, context)) is False
    assert "pending_trim" not in context.user_data


def test_pending_input_expires_after_timeout(store, tmp_path):
    context = _make_context()
    _pending(context, _source(tmp_path), age=timedelta(minutes=11))
    update, _ = _text_update("1:00-2:00")
    assert asyncio.run(tcb.handle_pending_trim_input(update, context)) is False
    assert "pending_trim" not in context.user_data


def test_pending_input_reports_expired_source(store, tmp_path):
    source = _source(tmp_path)
    context = _make_context()
    _pending(context, source)
    source.path.unlink()  # removed by aggressive cleanup while the prompt waited
    update, _ = _text_update("1:00-2:00")

    assert asyncio.run(tcb.handle_pending_trim_input(update, context)) is True
    assert update.message.reply_text.await_args.args[0] == tcb.EXPIRED_TEXT
    assert "pending_trim" not in context.user_data


def test_pending_input_respects_rate_limit(store, tmp_path, monkeypatch):
    context = _make_context()
    _pending(context, _source(tmp_path))
    run = AsyncMock()
    monkeypatch.setattr(tcb, "run_trim_job", run)
    monkeypatch.setattr(tcb, "check_rate_limit", lambda *_: False)
    update, _ = _text_update("1:00-2:00")

    assert asyncio.run(tcb.handle_pending_trim_input(update, context)) is True
    assert update.message.reply_text.await_args.args[0] == tcb.RATE_LIMIT_TEXT
    assert "pending_trim" in context.user_data
    run.assert_not_awaited()


def test_pending_input_rejects_parallel_trim(store, tmp_path, monkeypatch):
    context = _make_context()
    _pending(context, _source(tmp_path))
    run = AsyncMock()
    monkeypatch.setattr(tcb, "run_trim_job", run)
    monkeypatch.setattr(tcb, "check_rate_limit", lambda *_: True)
    update, _ = _text_update("1:00-2:00")

    async def scenario():
        descriptor = JobDescriptor("", CHAT, "trim", "Przycinanie: x", datetime.now())
        cancellation = job_registry.register(CHAT, descriptor)
        try:
            return await tcb.handle_pending_trim_input(update, context)
        finally:
            job_registry.unregister(cancellation.job_id)

    assert asyncio.run(scenario()) is True
    assert update.message.reply_text.await_args.args[0] == tcb.BUSY_TEXT
    run.assert_not_awaited()


# --- fragment job -------------------------------------------------------------


def _ranges(*pairs):
    return [ResolvedRange(start, end, False) for start, end in pairs]


async def _write_fragment(src, fragment, dest, *, title_tag, cancellation=None):
    dest.write_bytes(b"fragment")
    return dest


def _status():
    return Mock(edit_text=AsyncMock())


def test_run_trim_job_cuts_and_sends_each_fragment(store, tmp_path, monkeypatch):
    source = _source(tmp_path)
    sent = []

    async def fake_send(context, chat_id, path, **kwargs):
        assert Path(path).exists()
        sent.append((Path(path).name, kwargs["title"], kwargs["performer"]))

    monkeypatch.setattr(tcb, "cut_fragment", _write_fragment)
    monkeypatch.setattr(tcb, "send_audio_file", fake_send)
    monkeypatch.setattr(tcb, "max_sendable_audio_mb", lambda: 50)
    status = _status()

    asyncio.run(tcb.run_trim_job(
        _make_context(), chat_id=CHAT, requester_id=USER, source=source,
        ranges=_ranges((720, 930), (4800, 6130)), status_message=status,
    ))

    assert [title for _, title, _ in sent] == ["Podcast #120 [12:00–15:30]", "Podcast #120 [1:20:00–1:42:10]"]
    assert sent[0][0] == "Podcast #120 [12-00–15-30].mp3"
    assert all(performer == "Host" for *_, performer in sent)
    final = status.edit_text.await_args
    assert final.args[0] == "Gotowe: 2 fragmenty."
    assert final.kwargs["reply_markup"].inline_keyboard[0][0].callback_data == f"trim_src_{source.token}"
    assert sorted(p.name for p in source.workspace.iterdir()) == ["meta.json", "source.mp3"]
    assert job_registry.list_for_chat(CHAT) == []


def test_run_trim_job_uses_and_releases_a_given_cancellation(store, tmp_path, monkeypatch):
    source = _source(tmp_path)
    cuts = []

    async def recording_cut(src, fragment, dest, *, title_tag, cancellation=None):
        cuts.append((cancellation, len(job_registry.list_for_chat(CHAT))))
        return await _write_fragment(src, fragment, dest, title_tag=title_tag)

    monkeypatch.setattr(tcb, "cut_fragment", recording_cut)
    monkeypatch.setattr(tcb, "send_audio_file", AsyncMock())
    monkeypatch.setattr(tcb, "max_sendable_audio_mb", lambda: 50)

    async def scenario():
        descriptor = JobDescriptor("", CHAT, "trim", "Przycinanie: Podcast #120", datetime.now())
        cancellation = job_registry.register(CHAT, descriptor)
        await tcb.run_trim_job(
            _make_context(), chat_id=CHAT, requester_id=USER, source=source,
            ranges=_ranges((60, 120)), status_message=_status(), cancellation=cancellation,
        )
        return cancellation

    cancellation = asyncio.run(scenario())

    assert cuts == [(cancellation, 1)]  # no second job registered
    assert job_registry.list_for_chat(CHAT) == []


def test_run_trim_job_stops_on_send_failure_and_keeps_source(store, tmp_path, monkeypatch):
    source = _source(tmp_path)
    calls = {"count": 0}

    async def flaky_send(context, chat_id, path, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("telegram timeout")

    monkeypatch.setattr(tcb, "cut_fragment", _write_fragment)
    monkeypatch.setattr(tcb, "send_audio_file", flaky_send)
    monkeypatch.setattr(tcb, "max_sendable_audio_mb", lambda: 50)
    status = _status()

    asyncio.run(tcb.run_trim_job(
        _make_context(), chat_id=CHAT, requester_id=USER, source=source,
        ranges=_ranges((60, 120), (330, 420), (500, 600)), status_message=status,
    ))

    final = status.edit_text.await_args
    assert final.args[0].startswith("Nie udało się wysłać fragmentu 5:30–7:00.")
    assert "Wysłano wcześniej: 1 fragment." in final.args[0]
    assert final.kwargs["reply_markup"] is not None
    assert calls["count"] == 2
    assert sorted(p.name for p in source.workspace.iterdir()) == ["meta.json", "source.mp3"]


def test_run_trim_job_reports_cut_failure(store, tmp_path, monkeypatch):
    source = _source(tmp_path)

    async def failing_cut(*args, **kwargs):
        raise AudioTrimError("ffmpeg cut failed")

    monkeypatch.setattr(tcb, "cut_fragment", failing_cut)
    monkeypatch.setattr(tcb, "max_sendable_audio_mb", lambda: 50)
    status = _status()

    asyncio.run(tcb.run_trim_job(
        _make_context(), chat_id=CHAT, requester_id=USER, source=source,
        ranges=_ranges((720, 930)), status_message=status,
    ))

    assert status.edit_text.await_args.args[0] == (
        "Nie udało się wyciąć fragmentu 12:00–15:30. Pozostałe fragmenty nie zostały wysłane."
    )


def test_run_trim_job_refuses_oversized_fragment(store, tmp_path, monkeypatch):
    source = _source(tmp_path)

    async def big_cut(src, fragment, dest, **kwargs):
        dest.write_bytes(b"\x00" * (2 * 1024 * 1024))
        return dest

    send = AsyncMock()
    monkeypatch.setattr(tcb, "cut_fragment", big_cut)
    monkeypatch.setattr(tcb, "send_audio_file", send)
    monkeypatch.setattr(tcb, "max_sendable_audio_mb", lambda: 1)
    status = _status()

    asyncio.run(tcb.run_trim_job(
        _make_context(), chat_id=CHAT, requester_id=USER, source=source,
        ranges=_ranges((720, 930)), status_message=status,
    ))

    assert status.edit_text.await_args.args[0] == (
        "Fragment 12:00–15:30 ma 2 MB — za dużo do wysłania. Wybierz krótszy zakres."
    )
    send.assert_not_awaited()


def test_run_trim_job_reports_cancellation(store, tmp_path, monkeypatch):
    source = _source(tmp_path)

    async def cancelled_cut(src, fragment, dest, *, title_tag, cancellation=None):
        cancellation.event.set()
        raise AudioTrimError("terminated")

    monkeypatch.setattr(tcb, "cut_fragment", cancelled_cut)
    monkeypatch.setattr(tcb, "max_sendable_audio_mb", lambda: 50)
    status = _status()

    asyncio.run(tcb.run_trim_job(
        _make_context(), chat_id=CHAT, requester_id=USER, source=source,
        ranges=_ranges((60, 120), (330, 420)), status_message=status,
    ))

    assert status.edit_text.await_args.args[0] == "⏹ Przerwano po 0 z 2 fragmentów."


# --- routing ------------------------------------------------------------------


def test_handle_callback_routes_trim_callbacks_without_session_url(monkeypatch):
    from bot import telegram_callbacks as tc

    routed = AsyncMock()
    monkeypatch.setattr(tc, "handle_trim_callback", routed)
    monkeypatch.setattr(tc, "check_rate_limit", lambda *_: True)
    update = _make_update("trim_src_AAAAAAAAAAA", chat_id=CHAT)

    asyncio.run(tc.handle_callback(update, _make_context()))

    routed.assert_awaited_once()
    assert routed.await_args.args[2] == "trim_src_AAAAAAAAAAA"


# --- download and trim ----------------------------------------------------------


def test_offer_trim_after_download_retains_and_prompts(store, tmp_path, monkeypatch):
    audio = tmp_path / "Episode.mp3"
    audio.write_bytes(b"ID3 audio")
    monkeypatch.setattr(tcb, "probe_duration", AsyncMock(return_value=1800.4))
    context = _make_context()
    update = _callback("trim_dl")

    ok = asyncio.run(tcb.offer_trim_after_download(
        context, chat_id=CHAT, requester_id=USER, file_path=str(audio),
        title="Episode", performer="Show", query=update.callback_query,
    ))

    assert ok is True
    assert not audio.exists()
    text = update.callback_query.edit_message_text.await_args.args[0]
    assert text.startswith(tcb.TRIM_AFTER_DOWNLOAD_INTRO)
    assert "Długość: 30:00" in text
    assert context.user_data["pending_trim"].requester_id == USER


def test_offer_trim_after_download_reports_probe_failure(store, tmp_path, monkeypatch):
    audio = tmp_path / "Episode.mp3"
    audio.write_bytes(b"garbage")
    monkeypatch.setattr(tcb, "probe_duration", AsyncMock(side_effect=AudioTrimError("bad")))
    update = _callback("trim_dl")

    ok = asyncio.run(tcb.offer_trim_after_download(
        _make_context(), chat_id=CHAT, requester_id=USER, file_path=str(audio),
        title="Episode", performer=None, query=update.callback_query,
    ))

    assert ok is False
    assert "Nie udało się odczytać długości" in update.callback_query.edit_message_text.await_args.args[0]


def test_offer_trim_after_download_reports_low_disk(store, tmp_path, monkeypatch):
    audio = tmp_path / "Episode.mp3"
    audio.write_bytes(b"ID3 audio")
    monkeypatch.setattr(tcb, "probe_duration", AsyncMock(return_value=60.0))
    monkeypatch.setattr(tcb, "retain_source", lambda *a, **k: None)
    update = _callback("trim_dl")

    ok = asyncio.run(tcb.offer_trim_after_download(
        _make_context(), chat_id=CHAT, requester_id=USER, file_path=str(audio),
        title="Episode", performer=None, query=update.callback_query,
    ))

    assert ok is False
    assert update.callback_query.edit_message_text.await_args.args[0] == tcb.NO_ROOM_TEXT


def _trim_dl_setup(monkeypatch, *, platform, room=True):
    from bot import telegram_callbacks as tc

    tc.user_urls[CHAT] = "https://castbox.fm/episode/x" if platform == "castbox" else "https://open.spotify.com/episode/x"
    monkeypatch.setattr(tc, "check_rate_limit", lambda *_: True)
    monkeypatch.setattr(tc, "normalize_url", lambda url: url)
    monkeypatch.setattr(tc, "ensure_trim_authorized", AsyncMock(return_value=True))
    monkeypatch.setattr(tc, "has_room_for_sources", lambda: room)
    download_file = AsyncMock()
    download_spotify = AsyncMock()
    monkeypatch.setattr(tc, "download_file", download_file)
    monkeypatch.setattr(tc, "download_spotify_resolved", download_spotify)
    context = _make_context()
    context.user_data["platform"] = platform
    if platform == "spotify":
        context.user_data["spotify_resolved"] = {"source": "itunes", "title": "Ep"}
    return tc, context, download_file, download_spotify


def test_trim_dl_routes_spotify_to_resolved_download(monkeypatch):
    tc, context, download_file, download_spotify = _trim_dl_setup(monkeypatch, platform="spotify")
    asyncio.run(tc.handle_callback(_make_update("trim_dl", chat_id=CHAT), context))
    assert download_spotify.await_args.kwargs["trim_after"] is True
    assert download_spotify.await_args.args[3] == "mp3"
    download_file.assert_not_awaited()


def test_trim_dl_routes_castbox_to_download_file(monkeypatch):
    tc, context, download_file, download_spotify = _trim_dl_setup(monkeypatch, platform="castbox")
    asyncio.run(tc.handle_callback(_make_update("trim_dl", chat_id=CHAT), context))
    args = download_file.await_args
    assert args.args[2:5] == ("audio", "mp3", "https://castbox.fm/episode/x")
    assert args.kwargs["trim_after"] is True
    download_spotify.assert_not_awaited()


def test_trim_dl_refuses_when_disk_is_low(monkeypatch):
    tc, context, download_file, download_spotify = _trim_dl_setup(monkeypatch, platform="castbox", room=False)
    update = _make_update("trim_dl", chat_id=CHAT)
    asyncio.run(tc.handle_callback(update, context))
    assert update.callback_query.edit_message_text.await_args.args[0] == tcb.NO_ROOM_TEXT
    download_file.assert_not_awaited()


# --- uploaded audio -------------------------------------------------------------


def test_trim_upload_links_source_and_sends_prompt(store, tmp_path, monkeypatch):
    upload = tmp_path / "2026-10-01_voice.mp3"
    upload.write_bytes(b"ID3 audio")
    monkeypatch.setattr(tcb, "probe_duration", AsyncMock(return_value=95.0))
    context = _make_context()
    context.user_data["audio_file_path"] = str(upload)
    context.user_data["audio_file_title"] = "Wiadomość głosowa"
    update = _callback("trim_upload")

    asyncio.run(tcb.handle_trim_callback(update, context, "trim_upload"))

    assert upload.exists()  # transcription of the same upload still works
    text = context.bot.send_message.await_args.kwargs["text"]
    assert "Wiadomość głosowa" in text and "Długość: 1:35" in text
    update.callback_query.edit_message_text.assert_not_awaited()  # upload menu stays intact
    assert context.user_data["pending_trim"].requester_id == USER


def test_trim_upload_without_session_reports_expiry(store):
    update = _callback("trim_upload")
    asyncio.run(tcb.handle_trim_callback(update, _make_context(), "trim_upload"))
    assert update.callback_query.edit_message_text.await_args.args[0] == "Sesja wygasła — wyślij plik ponownie."
