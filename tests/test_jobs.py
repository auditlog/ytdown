"""Unit tests for bot.jobs registry."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest import mock

import pytest


def _descriptor(chat_id=42, kind="single_dl", label="t"):
    from bot.jobs import JobDescriptor
    return JobDescriptor(
        job_id="",  # will be set by registry
        chat_id=chat_id,
        kind=kind,
        label=label,
        started_at=datetime.now(),
    )


def test_register_returns_unique_job_id():
    from bot.jobs import JobRegistry

    registry = JobRegistry()
    c1 = registry.register(1, _descriptor())
    c2 = registry.register(1, _descriptor())

    assert c1.job_id != c2.job_id
    assert len(c1.job_id) >= 8


def test_register_creates_unset_event():
    from bot.jobs import JobRegistry

    registry = JobRegistry()
    cancellation = registry.register(1, _descriptor())

    assert isinstance(cancellation.event, asyncio.Event)
    assert cancellation.event.is_set() is False
    assert cancellation.process is None
    assert cancellation.pyrogram_task is None
    assert cancellation.cancelled_reason is None


def test_get_returns_registered_cancellation():
    from bot.jobs import JobRegistry

    registry = JobRegistry()
    c1 = registry.register(1, _descriptor())

    assert registry.get(c1.job_id) is c1
    assert registry.get("nonexistent") is None


def test_list_for_chat_returns_descriptors():
    from bot.jobs import JobRegistry

    registry = JobRegistry()
    c1 = registry.register(7, _descriptor(chat_id=7, label="A"))
    c2 = registry.register(7, _descriptor(chat_id=7, label="B"))

    descriptors = registry.list_for_chat(7)
    labels = [d.label for d in descriptors]

    assert sorted(labels) == ["A", "B"]
    assert all(d.chat_id == 7 for d in descriptors)


def test_list_for_chat_excludes_other_chats():
    from bot.jobs import JobRegistry

    registry = JobRegistry()
    registry.register(1, _descriptor(chat_id=1, label="A"))
    registry.register(2, _descriptor(chat_id=2, label="B"))

    assert [d.label for d in registry.list_for_chat(1)] == ["A"]
    assert [d.label for d in registry.list_for_chat(2)] == ["B"]


def test_update_label_changes_descriptor():
    from bot.jobs import JobRegistry

    registry = JobRegistry()
    c = registry.register(1, _descriptor(label="initial"))

    registry.update_label(c.job_id, "updated [3/5]")
    descriptors = registry.list_for_chat(1)

    assert descriptors[0].label == "updated [3/5]"


def test_update_label_silently_ignores_unknown_job():
    from bot.jobs import JobRegistry

    registry = JobRegistry()
    registry.update_label("nonexistent", "x")  # must not raise


def test_unregister_removes_descriptor():
    from bot.jobs import JobRegistry

    registry = JobRegistry()
    c = registry.register(1, _descriptor())

    registry.unregister(c.job_id)

    assert registry.get(c.job_id) is None
    assert registry.list_for_chat(1) == []


def test_unregister_unknown_is_noop():
    from bot.jobs import JobRegistry

    registry = JobRegistry()
    registry.unregister("nonexistent")  # must not raise


def test_global_singleton_is_jobregistry():
    from bot.jobs import JobRegistry, job_registry

    assert isinstance(job_registry, JobRegistry)


def test_cancel_sets_event_and_returns_true():
    from bot.jobs import JobRegistry

    registry = JobRegistry()
    c = registry.register(1, _descriptor())

    result = registry.cancel(c.job_id, reason="test")

    assert result is True
    assert c.event.is_set() is True
    assert c.cancelled_reason == "test"


def test_cancel_unknown_returns_false():
    from bot.jobs import JobRegistry

    registry = JobRegistry()
    result = registry.cancel("nonexistent")

    assert result is False


def test_cancel_terminates_attached_subprocess(monkeypatch):
    from bot.jobs import JobRegistry

    registry = JobRegistry()
    c = registry.register(1, _descriptor())

    fake_process = mock.MagicMock()
    fake_process.terminate = mock.MagicMock()
    fake_process.wait = mock.AsyncMock(return_value=0)
    fake_process.returncode = None
    fake_process.kill = mock.MagicMock()
    c.process = fake_process

    asyncio.run(registry.cancel_async(c.job_id, reason="t"))

    fake_process.terminate.assert_called_once()
    fake_process.kill.assert_not_called()


def test_cancel_kills_subprocess_when_terminate_times_out():
    from bot.jobs import JobRegistry
    from bot.security_limits import JOB_TERMINATE_GRACE_SEC

    registry = JobRegistry()
    c = registry.register(1, _descriptor())

    fake_process = mock.MagicMock()
    fake_process.terminate = mock.MagicMock()
    fake_process.wait = mock.AsyncMock(side_effect=asyncio.TimeoutError())
    fake_process.kill = mock.MagicMock()
    c.process = fake_process

    asyncio.run(registry.cancel_async(c.job_id, reason="t"))

    fake_process.terminate.assert_called_once()
    fake_process.kill.assert_called_once()


def test_cancel_cancels_attached_pyrogram_task():
    from bot.jobs import JobRegistry

    registry = JobRegistry()
    c = registry.register(1, _descriptor())

    fake_task = mock.MagicMock()
    fake_task.done = mock.MagicMock(return_value=False)
    fake_task.cancel = mock.MagicMock()
    c.pyrogram_task = fake_task

    asyncio.run(registry.cancel_async(c.job_id, reason="t"))

    fake_task.cancel.assert_called_once()


def test_cancel_skips_already_done_pyrogram_task():
    from bot.jobs import JobRegistry

    registry = JobRegistry()
    c = registry.register(1, _descriptor())

    fake_task = mock.MagicMock()
    fake_task.done = mock.MagicMock(return_value=True)
    fake_task.cancel = mock.MagicMock()
    c.pyrogram_task = fake_task

    asyncio.run(registry.cancel_async(c.job_id, reason="t"))

    fake_task.cancel.assert_not_called()


def test_cancelled_reason_propagates():
    from bot.jobs import JobRegistry

    registry = JobRegistry()
    c = registry.register(1, _descriptor())

    registry.cancel(c.job_id, reason="user via /stop")

    assert c.cancelled_reason == "user via /stop"
