"""Unit tests for bot.services.archive_service workspace + registry helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest


def test_prepare_playlist_workspace_creates_pl_prefixed_dir(tmp_path, monkeypatch):
    from bot.services import archive_service

    monkeypatch.setattr(archive_service, "DOWNLOAD_PATH", str(tmp_path))
    fixed_ts = datetime(2026, 5, 2, 9, 30, 15)
    with mock.patch("bot.services.archive_service.datetime") as dt_mock:
        dt_mock.now.return_value = fixed_ts
        ws = archive_service.prepare_playlist_workspace(7, "Lista A")

    assert ws.exists() and ws.is_dir()
    assert ws.parent == tmp_path / "7"
    assert ws.name.startswith("pl_")
    assert "Lista_A" in ws.name or "Lista A" in ws.name
    assert "20260502-093015" in ws.name


def test_prepare_playlist_workspace_transliterates_polish_chars(tmp_path, monkeypatch):
    from bot.services import archive_service

    monkeypatch.setattr(archive_service, "DOWNLOAD_PATH", str(tmp_path))
    ws = archive_service.prepare_playlist_workspace(9, "Pączki ąęłż")

    assert "Paczki" in ws.name
    assert "ą" not in ws.name and "ł" not in ws.name


def test_prepare_playlist_workspace_uses_big_prefix_when_requested(tmp_path, monkeypatch):
    from bot.services import archive_service

    monkeypatch.setattr(archive_service, "DOWNLOAD_PATH", str(tmp_path))
    ws = archive_service.prepare_playlist_workspace(1, "video", prefix="big")

    assert ws.name.startswith("big_")


def test_register_pending_archive_job_returns_unique_tokens(tmp_path):
    from bot.services import archive_service
    from bot.session_store import (
        ArchiveJobState,
        pending_archive_jobs,
        session_store,
    )

    session_store.reset()
    state = ArchiveJobState(
        file_path=Path(tmp_path / "x.mp4"),
        title="t",
        media_type="video",
        format_choice="best",
        file_size_mb=200.0,
        use_mtproto=False,
        created_at=datetime(2026, 5, 2),
    )

    tokens = {archive_service.register_pending_archive_job(99, state) for _ in range(50)}

    assert len(tokens) == 50
    assert pending_archive_jobs[99].keys() == tokens
    session_store.reset()


def test_register_archived_delivery_stores_state():
    from bot.services import archive_service
    from bot.session_store import (
        ArchivedDeliveryState,
        archived_deliveries,
        session_store,
    )

    session_store.reset()
    delivery = ArchivedDeliveryState(
        workspace=Path("/tmp/pl_ws"),
        volumes=[Path("/tmp/pl_ws/x.7z.001")],
        caption_prefix="ABC",
        use_mtproto=True,
        created_at=datetime(2026, 5, 2),
    )

    token = archive_service.register_archived_delivery(11, delivery)

    assert token in archived_deliveries[11]
    assert archived_deliveries[11][token] is delivery
    session_store.reset()


def test_download_playlist_into_keeps_files_after_download(tmp_path, monkeypatch):
    from bot.services import archive_service

    workspace = tmp_path / "pl_x"
    workspace.mkdir()

    async def fake_run(entry, workspace_path, *, media_type, format_choice, executor):
        produced = workspace_path / f"{entry['title']}.bin"
        produced.write_bytes(b"data")
        return produced, 1.5  # path, size_mb

    monkeypatch.setattr(archive_service, "_download_one_into_workspace", fake_run)

    entries = [{"url": "u1", "title": "first"}, {"url": "u2", "title": "second"}]
    import asyncio

    paths, failed = asyncio.run(
        archive_service.download_playlist_into(
            workspace,
            entries,
            media_type="audio",
            format_choice="mp3",
            executor=mock.MagicMock(),
            status_cb=mock.AsyncMock(),
        )
    )

    assert {p.name for p in paths} == {"first.bin", "second.bin"}
    assert failed == []
    assert (workspace / "first.bin").exists()
    assert (workspace / "second.bin").exists()


def test_download_playlist_into_returns_empty_when_all_fail(tmp_path, monkeypatch):
    from bot.services import archive_service

    workspace = tmp_path / "pl_x"
    workspace.mkdir()

    async def fake_run(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(archive_service, "_download_one_into_workspace", fake_run)

    entries = [{"url": "u1", "title": "a"}, {"url": "u2", "title": "b"}]
    import asyncio

    paths, failed = asyncio.run(
        archive_service.download_playlist_into(
            workspace, entries, media_type="audio", format_choice="mp3",
            executor=mock.MagicMock(), status_cb=mock.AsyncMock(),
        )
    )

    assert paths == []
    assert failed == ["a", "b"]


def test_download_playlist_into_returns_failed_titles_on_partial(tmp_path, monkeypatch):
    from bot.services import archive_service

    workspace = tmp_path / "pl_x"
    workspace.mkdir()
    call_count = {"n": 0}

    async def fake_run(entry, workspace_path, **kwargs):
        call_count["n"] += 1
        if entry["title"] == "bad":
            raise RuntimeError("fail")
        produced = workspace_path / f"{entry['title']}.bin"
        produced.write_bytes(b"x")
        return produced, 0.5

    monkeypatch.setattr(archive_service, "_download_one_into_workspace", fake_run)

    entries = [
        {"url": "u1", "title": "good1"},
        {"url": "u2", "title": "bad"},
        {"url": "u3", "title": "good2"},
    ]
    import asyncio

    paths, failed = asyncio.run(
        archive_service.download_playlist_into(
            workspace, entries, media_type="audio", format_choice="mp3",
            executor=mock.MagicMock(), status_cb=mock.AsyncMock(),
        )
    )

    assert {p.name for p in paths} == {"good1.bin", "good2.bin"}
    assert failed == ["bad"]


def test_download_playlist_into_respects_max_archive_item_size(tmp_path, monkeypatch):
    from bot.services import archive_service

    workspace = tmp_path / "pl_x"
    workspace.mkdir()

    async def fake_run(entry, workspace_path, **kwargs):
        # Pretend the second entry is huge.
        if entry["title"] == "huge":
            return None, 99999.0  # too big
        produced = workspace_path / f"{entry['title']}.bin"
        produced.write_bytes(b"x")
        return produced, 1.0

    monkeypatch.setattr(archive_service, "_download_one_into_workspace", fake_run)

    entries = [
        {"url": "u1", "title": "ok"},
        {"url": "u2", "title": "huge"},
    ]
    import asyncio

    paths, failed = asyncio.run(
        archive_service.download_playlist_into(
            workspace, entries, media_type="audio", format_choice="mp3",
            executor=mock.MagicMock(), status_cb=mock.AsyncMock(),
        )
    )

    assert [p.name for p in paths] == ["ok.bin"]
    assert any("huge" in title and "za duzy" in title for title in failed)


def test_send_volumes_uses_botapi_for_small_volumes(tmp_path, monkeypatch):
    from bot.services import archive_service

    v1 = tmp_path / "out.7z.001"
    v1.write_bytes(b"x" * (10 * 1024 * 1024))  # 10 MB
    v2 = tmp_path / "out.7z.002"
    v2.write_bytes(b"x" * (5 * 1024 * 1024))   # 5 MB

    bot = mock.MagicMock()
    bot.send_document = mock.AsyncMock()

    mtproto_calls = []

    async def fake_mtproto(*args, **kwargs):
        mtproto_calls.append((args, kwargs))
        return True

    monkeypatch.setattr(archive_service, "send_document_mtproto", fake_mtproto)
    monkeypatch.setattr(
        archive_service, "mtproto_unavailability_reason",
        lambda: "n/a in this test",
    )

    import asyncio

    asyncio.run(
        archive_service.send_volumes(
            bot,
            chat_id=42,
            volumes=[v1, v2],
            caption_prefix="My playlist (audio mp3)",
            use_mtproto=False,
            status_cb=mock.AsyncMock(),
        )
    )

    assert bot.send_document.await_count == 2
    assert mtproto_calls == []
    first_call = bot.send_document.await_args_list[0].kwargs
    assert first_call["chat_id"] == 42
    assert first_call["caption"] == "My playlist (audio mp3) [1/2]"


def test_send_volumes_uses_mtproto_for_large_volumes(tmp_path, monkeypatch):
    from bot.security_limits import TELEGRAM_UPLOAD_LIMIT_MB
    from bot.services import archive_service

    big = tmp_path / "out.7z.001"
    big.write_bytes(b"x" * int((TELEGRAM_UPLOAD_LIMIT_MB + 5) * 1024 * 1024))

    bot = mock.MagicMock()
    bot.send_document = mock.AsyncMock()

    mtproto_calls = []

    async def fake_mtproto(chat_id, file_path, caption=None, file_name=None):
        mtproto_calls.append((chat_id, file_path, caption, file_name))
        return True

    monkeypatch.setattr(archive_service, "send_document_mtproto", fake_mtproto)
    monkeypatch.setattr(
        archive_service, "mtproto_unavailability_reason", lambda: None
    )

    import asyncio

    asyncio.run(
        archive_service.send_volumes(
            bot,
            chat_id=42,
            volumes=[big],
            caption_prefix="X",
            use_mtproto=True,
            status_cb=mock.AsyncMock(),
        )
    )

    assert bot.send_document.await_count == 0
    assert len(mtproto_calls) == 1
    assert mtproto_calls[0][0] == 42
    assert mtproto_calls[0][3] == "out.7z.001"


def test_send_volumes_raises_when_volume_too_large_and_no_mtproto(tmp_path, monkeypatch):
    from bot.security_limits import TELEGRAM_UPLOAD_LIMIT_MB
    from bot.services import archive_service

    big = tmp_path / "out.7z.001"
    big.write_bytes(b"x" * int((TELEGRAM_UPLOAD_LIMIT_MB + 5) * 1024 * 1024))

    bot = mock.MagicMock()
    bot.send_document = mock.AsyncMock()

    monkeypatch.setattr(
        archive_service, "mtproto_unavailability_reason",
        lambda: "Skonfiguruj API_ID",
    )

    import asyncio

    with pytest.raises(RuntimeError, match="MTProto"):
        asyncio.run(
            archive_service.send_volumes(
                bot,
                chat_id=42,
                volumes=[big],
                caption_prefix="X",
                use_mtproto=False,
                status_cb=mock.AsyncMock(),
            )
        )


def test_send_volumes_resumes_from_start_index(tmp_path, monkeypatch):
    from bot.services import archive_service

    v1 = tmp_path / "out.7z.001"
    v1.write_bytes(b"x")
    v2 = tmp_path / "out.7z.002"
    v2.write_bytes(b"x")
    v3 = tmp_path / "out.7z.003"
    v3.write_bytes(b"x")

    bot = mock.MagicMock()
    bot.send_document = mock.AsyncMock()

    import asyncio

    asyncio.run(
        archive_service.send_volumes(
            bot,
            chat_id=42,
            volumes=[v1, v2, v3],
            caption_prefix="X",
            use_mtproto=False,
            start_index=2,
            status_cb=mock.AsyncMock(),
        )
    )

    assert bot.send_document.await_count == 1
    assert bot.send_document.await_args.kwargs["caption"] == "X [3/3]"


def test_send_volumes_raises_when_mtproto_returns_false(tmp_path, monkeypatch):
    from bot.security_limits import TELEGRAM_UPLOAD_LIMIT_MB
    from bot.services import archive_service

    big = tmp_path / "out.7z.001"
    big.write_bytes(b"x" * int((TELEGRAM_UPLOAD_LIMIT_MB + 5) * 1024 * 1024))

    monkeypatch.setattr(archive_service, "mtproto_unavailability_reason", lambda: None)
    monkeypatch.setattr(
        archive_service, "send_document_mtproto",
        mock.AsyncMock(return_value=False),
    )

    bot = mock.MagicMock()
    bot.send_document = mock.AsyncMock()

    import asyncio

    with pytest.raises(RuntimeError, match="nie powiodła się"):
        asyncio.run(
            archive_service.send_volumes(
                bot,
                chat_id=42,
                volumes=[big],
                caption_prefix="X",
                use_mtproto=True,
                status_cb=mock.AsyncMock(),
            )
        )


def test_execute_playlist_archive_flow_happy_path(tmp_path, monkeypatch):
    """Ensures the end-to-end flow chains workspace → download → pack → send."""
    from bot.services import archive_service
    from bot.session_store import session_store

    session_store.reset()
    monkeypatch.setattr(archive_service, "DOWNLOAD_PATH", str(tmp_path))

    async def fake_download_into(workspace, entries, **kwargs):
        path1 = workspace / "a.mp3"
        path1.write_bytes(b"a")
        path2 = workspace / "b.mp3"
        path2.write_bytes(b"b")
        return [path1, path2], []

    async def fake_pack(sources, dest_basename, volume_size_mb, **kwargs):
        produced = dest_basename.parent / f"{dest_basename.with_suffix('.7z').name}.001"
        produced.write_bytes(b"vol")
        return [produced]

    sent_volumes = []

    async def fake_send_volumes(bot, chat_id, volumes, caption_prefix, use_mtproto, **kwargs):
        sent_volumes.extend(volumes)

    monkeypatch.setattr(archive_service, "download_playlist_into", fake_download_into)
    monkeypatch.setattr(archive_service, "pack_to_volumes", fake_pack)
    monkeypatch.setattr(archive_service, "send_volumes", fake_send_volumes)
    monkeypatch.setattr(archive_service, "mtproto_unavailability_reason", lambda: None)

    update = mock.MagicMock()
    update.callback_query = mock.MagicMock()
    update.callback_query.edit_message_text = mock.AsyncMock()
    context = mock.MagicMock()
    context.bot = mock.MagicMock()

    import asyncio

    playlist = {"title": "Hits", "entries": [{"url": "u1", "title": "a"}]}

    asyncio.run(
        archive_service.execute_playlist_archive_flow(
            update, context, chat_id=99, playlist=playlist,
            media_type="audio", format_choice="mp3",
            executor=mock.MagicMock(),
        )
    )

    # One volume produced and shipped.
    assert len(sent_volumes) == 1
    # Workspace persists for retention.
    assert any(p.name.startswith("pl_") for p in (tmp_path / "99").iterdir())
    session_store.reset()


def test_execute_playlist_archive_flow_aborts_when_no_items_succeed(tmp_path, monkeypatch):
    from bot.services import archive_service
    from bot.session_store import session_store

    session_store.reset()
    monkeypatch.setattr(archive_service, "DOWNLOAD_PATH", str(tmp_path))

    async def fake_download_into(workspace, entries, **kwargs):
        return [], ["a", "b"]

    pack_called = mock.AsyncMock()
    monkeypatch.setattr(archive_service, "download_playlist_into", fake_download_into)
    monkeypatch.setattr(archive_service, "pack_to_volumes", pack_called)
    monkeypatch.setattr(archive_service, "is_7z_available", lambda: True)
    monkeypatch.setattr(archive_service, "mtproto_unavailability_reason", lambda: None)

    update = mock.MagicMock()
    update.callback_query = mock.MagicMock()
    update.callback_query.edit_message_text = mock.AsyncMock()
    context = mock.MagicMock()

    import asyncio

    playlist = {"title": "Empty", "entries": [{"url": "u", "title": "a"}]}
    asyncio.run(
        archive_service.execute_playlist_archive_flow(
            update, context, chat_id=99, playlist=playlist,
            media_type="audio", format_choice="mp3",
            executor=mock.MagicMock(),
        )
    )
    assert pack_called.await_count == 0
    # Workspace removed because everything failed.
    chat_dir = tmp_path / "99"
    if chat_dir.exists():
        assert not any(chat_dir.iterdir())
    session_store.reset()


def test_download_playlist_into_breaks_on_cancel(tmp_path, monkeypatch):
    import asyncio
    from bot.services import archive_service
    from bot.jobs import JobCancellation

    workspace = tmp_path / "pl_x"
    workspace.mkdir()

    cancellation = JobCancellation(job_id="t", event=asyncio.Event())
    iter_count = {"n": 0}

    async def fake_run(entry, workspace_path, **kwargs):
        iter_count["n"] += 1
        if iter_count["n"] == 2:
            cancellation.event.set()
        produced = workspace_path / f"{entry['title']}.bin"
        produced.write_bytes(b"x")
        return produced, 0.5

    monkeypatch.setattr(archive_service, "_download_one_into_workspace", fake_run)

    entries = [
        {"url": "u1", "title": "first"},
        {"url": "u2", "title": "second"},
        {"url": "u3", "title": "third"},
    ]

    paths, failed = asyncio.run(
        archive_service.download_playlist_into(
            workspace, entries, media_type="audio", format_choice="mp3",
            executor=mock.MagicMock(), status_cb=mock.AsyncMock(),
            cancellation=cancellation,
        )
    )

    assert {p.name for p in paths} == {"first.bin", "second.bin"}
    assert any("anulowano" in t.lower() for t in failed)


def test_send_volumes_breaks_on_cancel(tmp_path, monkeypatch):
    import asyncio
    from bot.services import archive_service
    from bot.jobs import JobCancellation

    volumes = []
    for i in range(1, 6):
        v = tmp_path / f"out.7z.00{i}"
        v.write_bytes(b"x")
        volumes.append(v)

    cancellation = JobCancellation(job_id="t", event=asyncio.Event())

    bot = mock.MagicMock()
    sent = []

    async def fake_send(**kwargs):
        sent.append(kwargs["caption"])
        if len(sent) == 2:
            cancellation.event.set()

    bot.send_document = fake_send

    monkeypatch.setattr(
        archive_service, "mtproto_unavailability_reason", lambda: "n/a",
    )

    asyncio.run(
        archive_service.send_volumes(
            bot, chat_id=1, volumes=volumes, caption_prefix="X",
            use_mtproto=False, status_cb=mock.AsyncMock(),
            cancellation=cancellation,
        )
    )

    assert len(sent) == 2  # third volume was not sent


def test_execute_playlist_archive_flow_registers_and_unregisters(tmp_path, monkeypatch):
    import asyncio
    from bot.services import archive_service
    from bot.jobs import JobRegistry
    from bot.session_store import session_store

    session_store.reset()
    monkeypatch.setattr(archive_service, "DOWNLOAD_PATH", str(tmp_path))

    test_registry = JobRegistry()
    monkeypatch.setattr(archive_service, "job_registry", test_registry)

    job_seen_during_run = {}

    async def fake_download_into(workspace, entries, **kwargs):
        # Snapshot job listing while the flow is in-flight.
        job_seen_during_run["count"] = len(test_registry.list_for_chat(99))
        path = workspace / "a.mp3"
        path.write_bytes(b"x")
        return [path], []

    async def fake_pack(sources, dest_basename, volume_size_mb, **kwargs):
        produced = dest_basename.parent / f"{dest_basename.with_suffix('.7z').name}.001"
        produced.write_bytes(b"v")
        return [produced]

    async def fake_send(*a, **kw):
        return None

    monkeypatch.setattr(archive_service, "download_playlist_into", fake_download_into)
    monkeypatch.setattr(archive_service, "pack_to_volumes", fake_pack)
    monkeypatch.setattr(archive_service, "send_volumes", fake_send)
    monkeypatch.setattr(archive_service, "mtproto_unavailability_reason", lambda: None)

    update = mock.MagicMock()
    update.callback_query = mock.MagicMock()
    update.callback_query.edit_message_text = mock.AsyncMock()
    context = mock.MagicMock()

    asyncio.run(
        archive_service.execute_playlist_archive_flow(
            update, context, chat_id=99,
            playlist={"title": "Hits", "entries": [{"url": "u", "title": "a"}]},
            media_type="audio", format_choice="mp3",
            executor=mock.MagicMock(),
        )
    )

    assert job_seen_during_run["count"] == 1
    # Unregistered after success.
    assert test_registry.list_for_chat(99) == []
    session_store.reset()


def test_execute_playlist_archive_flow_captures_partial_state_on_cancel(tmp_path, monkeypatch):
    import asyncio
    from bot.services import archive_service
    from bot.jobs import JobRegistry
    from bot.session_store import partial_archive_workspaces, session_store

    session_store.reset()
    monkeypatch.setattr(archive_service, "DOWNLOAD_PATH", str(tmp_path))

    test_registry = JobRegistry()
    monkeypatch.setattr(archive_service, "job_registry", test_registry)

    async def fake_download_into(workspace, entries, *, cancellation=None, **kwargs):
        # Simulate two entries downloaded, then cancellation.
        p1 = workspace / "a.mp3"; p1.write_bytes(b"x")
        p2 = workspace / "b.mp3"; p2.write_bytes(b"x")
        cancellation.event.set()
        return [p1, p2], ["c (anulowano)"]

    pack_called = mock.AsyncMock()
    send_called = mock.AsyncMock()
    monkeypatch.setattr(archive_service, "download_playlist_into", fake_download_into)
    monkeypatch.setattr(archive_service, "pack_to_volumes", pack_called)
    monkeypatch.setattr(archive_service, "send_volumes", send_called)
    monkeypatch.setattr(archive_service, "mtproto_unavailability_reason", lambda: None)

    update = mock.MagicMock()
    update.callback_query = mock.MagicMock()
    update.callback_query.edit_message_text = mock.AsyncMock()
    context = mock.MagicMock()

    asyncio.run(
        archive_service.execute_playlist_archive_flow(
            update, context, chat_id=99,
            playlist={"title": "Hits", "entries": [
                {"url": "u1", "title": "a"},
                {"url": "u2", "title": "b"},
                {"url": "u3", "title": "c"},
            ]},
            media_type="audio", format_choice="mp3",
            executor=mock.MagicMock(),
        )
    )

    # Pack and send must NOT have been called.
    assert pack_called.await_count == 0
    assert send_called.await_count == 0
    # Partial state captured.
    bucket = partial_archive_workspaces.get(99) or {}
    assert len(bucket) == 1
    state = next(iter(bucket.values()))
    assert len(state.downloaded) == 2
    assert state.title == "Hits"
    session_store.reset()


def test_execute_single_file_archive_flow_consumes_pending_job(tmp_path, monkeypatch):
    from bot.services import archive_service
    from bot.session_store import (
        ArchiveJobState,
        pending_archive_jobs,
        session_store,
    )

    session_store.reset()
    monkeypatch.setattr(archive_service, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(archive_service, "is_7z_available", lambda: True)
    monkeypatch.setattr(archive_service, "mtproto_unavailability_reason", lambda: None)

    src = tmp_path / "input.mp4"
    src.write_bytes(b"x")
    state = ArchiveJobState(
        file_path=src,
        title="MyVid",
        media_type="video",
        format_choice="best",
        file_size_mb=10.0,
        use_mtproto=False,
        created_at=datetime(2026, 5, 2),
    )
    token = archive_service.register_pending_archive_job(33, state)

    async def fake_pack(sources, dest_basename, volume_size_mb, **kwargs):
        produced = dest_basename.parent / f"{dest_basename.with_suffix('.7z').name}.001"
        produced.write_bytes(b"v")
        return [produced]

    sent = []

    async def fake_send_volumes(bot, chat_id, volumes, caption_prefix, use_mtproto, **kwargs):
        sent.extend(volumes)

    monkeypatch.setattr(archive_service, "pack_to_volumes", fake_pack)
    monkeypatch.setattr(archive_service, "send_volumes", fake_send_volumes)

    update = mock.MagicMock()
    update.callback_query = mock.MagicMock()
    update.callback_query.edit_message_text = mock.AsyncMock()
    context = mock.MagicMock()
    context.bot = mock.MagicMock()

    import asyncio

    asyncio.run(
        archive_service.execute_single_file_archive_flow(
            update, context, chat_id=33, token=token,
        )
    )

    # Pending job consumed.
    assert pending_archive_jobs.get(33, {}).get(token) is None
    # File migrated into workspace and a volume produced + sent.
    assert len(sent) == 1
    session_store.reset()


def test_execute_partial_archive_flow_packs_remaining(tmp_path, monkeypatch):
    import asyncio
    from datetime import datetime
    from pathlib import Path
    from bot.services import archive_service
    from bot.session_store import (
        ArchivePartialState,
        partial_archive_workspaces,
        session_store,
    )

    session_store.reset()
    monkeypatch.setattr(archive_service, "DOWNLOAD_PATH", str(tmp_path))

    workspace = tmp_path / "ws"
    workspace.mkdir()
    p1 = workspace / "a.mp3"; p1.write_bytes(b"x")
    p2 = workspace / "b.mp3"; p2.write_bytes(b"x")

    state = ArchivePartialState(
        workspace=workspace, downloaded=[p1, p2],
        title="Hits", media_type="audio", format_choice="mp3",
        use_mtproto=False,
        created_at=datetime(2026, 5, 3),
    )
    partial_archive_workspaces[44] = {"tok": state}

    pack_called = mock.AsyncMock(return_value=[workspace / "out.7z.001"])
    send_called = mock.AsyncMock()
    monkeypatch.setattr(archive_service, "pack_to_volumes", pack_called)
    monkeypatch.setattr(archive_service, "send_volumes", send_called)
    monkeypatch.setattr(archive_service, "mtproto_unavailability_reason", lambda: "n/a")

    update = mock.MagicMock()
    update.callback_query = mock.MagicMock()
    update.callback_query.edit_message_text = mock.AsyncMock()
    context = mock.MagicMock()
    context.bot = mock.MagicMock()

    asyncio.run(
        archive_service.execute_partial_archive_flow(
            update, context, chat_id=44, token="tok",
        )
    )

    pack_called.assert_awaited_once()
    send_called.assert_awaited_once()
    # Partial state consumed.
    assert partial_archive_workspaces.get(44, {}).get("tok") is None
    session_store.reset()
