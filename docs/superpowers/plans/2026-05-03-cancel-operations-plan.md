# Plan implementacji: Komenda `/stop` — anulowanie długotrwałych operacji

> **Dla agentic worker:** WYMAGANY SUB-SKILL — `superpowers:subagent-driven-development` (rekomendowany) lub `superpowers:executing-plans`. Kroki używają składni checkbox `- [ ]`.

**Cel:** Dodać komendę `/stop` która listuje aktywne długotrwałe operacje per chat (playlisty, single-file, transkrypcja, 7z pack/send, MTProto upload, summary) i pozwala je anulować — pojedynczo lub wszystkie naraz — bez restartu bota.

**Architektura:** Nowy moduł `bot/jobs.py` z `JobRegistry` (globalny singleton) wystawia `JobCancellation` per zadanie. Cancel-handle łączy 3 mechanizmy: `asyncio.Event` (sprawdzane przez async pętle), opcjonalny `asyncio.subprocess.Process` (terminate→kill dla 7z) i opcjonalny `asyncio.Task` (cancel dla pyrogram/Anthropic SDK). Każda warstwa konsumuje cancellation w sposób natywny.

**Tech Stack:** Python 3.11+, `asyncio`, `asyncio.subprocess`, `pyrogram`, `python-telegram-bot`, `pytest` + `pytest-asyncio`. 7z (p7zip-full) już zainstalowany.

**Gałąź:** `develop` — wszystkie commity bezpośrednio na `develop` (zgodnie z polityką repo).

**Spec referencyjny:** `docs/superpowers/specs/2026-05-03-cancel-operations-design.md` (commit `efe05fa`).

---

## Mapa plików

| Plik | Akcja | Odpowiedzialność |
|---|---|---|
| `bot/security_limits.py` | modify | Stałe `JOB_DEAD_AGE_HOURS`, `JOB_TERMINATE_GRACE_SEC`. |
| `bot/jobs.py` | **create** | `JobCancellation`, `JobDescriptor`, `JobRegistry`, globalny `job_registry`. |
| `bot/session_store.py` | modify | `ArchivePartialState` dataclass + `partial_archive_workspaces` field map. |
| `bot/archive.py` | modify | `pack_to_volumes` przyjmuje `cancellation`, terminate→kill subprocess. |
| `bot/services/download_service.py` | modify | `execute_download` przyjmuje `cancellation`, progress hook raise. |
| `bot/services/archive_service.py` | modify | `download_playlist_into`, `send_volumes`, `execute_*_archive_flow` przyjmują cancellation. Nowa `execute_partial_archive_flow`. |
| `bot/handlers/playlist_callbacks.py` | modify | Legacy `download_playlist` register/unregister job. Callback `arc_pack_partial_*`. |
| `bot/handlers/download_callbacks.py` | modify | `download_file` register/unregister job. |
| `bot/transcription_pipeline.py` | modify | Pętla po chunkach sprawdza `cancellation.event`. |
| `bot/transcription_providers.py` | modify | `generate_summary` opakowane w cancellable task. |
| `bot/mtproto.py` | modify | `send_*_mtproto` attach pyrogram task do cancellation. |
| `bot/telegram_commands.py` | modify | Nowy handler `/stop` + callbacks `stop_*`. |
| `bot/telegram_callbacks.py` | modify | Router rozpoznaje prefix `stop_`. |
| `bot/cleanup.py` | modify | `purge_dead(6h)` + `_purge_partial_archive_workspaces`. |
| `tests/test_jobs.py` | **create** | Unit dla `JobRegistry`, `JobCancellation`. |
| `tests/test_archive.py` | modify | Cancel terminate test. |
| `tests/test_archive_service.py` | modify | Cancel testy dla download_playlist_into, send_volumes, flow. |
| `tests/test_download_service.py` | modify | Cancel test dla execute_download progress hook. |
| `tests/test_telegram_commands.py` | modify | `/stop` handler + callbacks. |
| `tests/test_cleanup.py` | modify | `purge_dead` + partial workspaces test. |
| `tests/test_mtproto.py` | modify | `send_*_mtproto` attaches task. |
| `tests/test_session_store.py` | modify | `ArchivePartialState` + `partial_archive_workspaces`. |

---

## Task 1: Stałe w `bot/security_limits.py`

**Files:**
- Modify: `/mnt/c/code/ytdown/bot/security_limits.py`
- Test: `/mnt/c/code/ytdown/tests/test_security_unit.py`

- [ ] **Step 1.1: Failing test**

Dopisz na końcu `tests/test_security_unit.py`:

```python
def test_job_dead_age_constant_defined():
    from bot import security_limits

    assert security_limits.JOB_DEAD_AGE_HOURS == 6
    assert security_limits.JOB_DEAD_AGE_HOURS > 0


def test_job_terminate_grace_constant_defined():
    from bot import security_limits

    assert security_limits.JOB_TERMINATE_GRACE_SEC == 1.0
    assert 0 < security_limits.JOB_TERMINATE_GRACE_SEC <= 5.0
```

- [ ] **Step 1.2: Run test, expect FAIL**

```bash
source /home/pi/venv/bin/activate
cd /mnt/c/code/ytdown
pytest tests/test_security_unit.py -v -k "JOB_"
```

Expected: `AttributeError: module 'bot.security_limits' has no attribute 'JOB_DEAD_AGE_HOURS'`.

- [ ] **Step 1.3: Add constants**

Append to `bot/security_limits.py` (after the existing archive-related constants):

```python
# Cleanup of stale (zombie) entries in JobRegistry. Defends /stop list
# against operations that never unregistered due to bugs or crashes.
JOB_DEAD_AGE_HOURS = 6

# Grace between SIGTERM and SIGKILL when terminating a 7z subprocess
# attached to a JobCancellation. Long enough for 7z to finish writing
# its current 1 MiB block, short enough to not block /stop UX.
JOB_TERMINATE_GRACE_SEC = 1.0
```

- [ ] **Step 1.4: Run tests, expect PASS**

```bash
pytest tests/test_security_unit.py -v
```

- [ ] **Step 1.5: Commit**

```bash
git add bot/security_limits.py tests/test_security_unit.py
git commit -m "Add JOB_DEAD_AGE_HOURS and JOB_TERMINATE_GRACE_SEC constants"
```

---

## Task 2: `bot/jobs.py` — `JobCancellation`, `JobDescriptor`, `JobRegistry` (rejestracja, listowanie, unregister)

**Files:**
- Create: `/mnt/c/code/ytdown/bot/jobs.py`
- Test: `/mnt/c/code/ytdown/tests/test_jobs.py` (create)

- [ ] **Step 2.1: Failing tests**

Create `tests/test_jobs.py`:

```python
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
```

- [ ] **Step 2.2: Run tests, expect FAIL**

```bash
pytest tests/test_jobs.py -v
```

Expected: `ModuleNotFoundError: No module named 'bot.jobs'`.

- [ ] **Step 2.3: Create `bot/jobs.py`**

```python
"""In-memory registry of long-running jobs that can be cancelled.

Boundaries:
- Knows nothing about Telegram, sessions, downloads, or 7z.
- Consumers (handlers, services) import JobRegistry/JobCancellation and
  attach process/pyrogram_task references on their own.
- The global ``job_registry`` singleton is the canonical instance — tests
  build their own JobRegistry() to stay isolated.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import RLock
from typing import Literal


JobKind = Literal[
    "playlist_legacy",
    "playlist_zip",
    "single_dl",
    "transcription",
    "summary",
    "archive_pack",
    "archive_send",
]


@dataclass
class JobCancellation:
    """Cancellation handle shared across async/threadpool/subprocess layers.

    The single Event is the primary signal — every long-running loop
    polls it. ``process`` and ``pyrogram_task`` are optional resources
    that JobRegistry.cancel() will tear down at signal time.
    """

    job_id: str
    event: asyncio.Event
    process: asyncio.subprocess.Process | None = None
    pyrogram_task: asyncio.Task | None = None
    cancelled_reason: str | None = None


@dataclass
class JobDescriptor:
    """User-facing description of a job, listed by /stop."""

    job_id: str
    chat_id: int
    kind: JobKind
    label: str
    started_at: datetime


class JobRegistry:
    """Thread-safe registry of running JobCancellation handles."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._descriptors: dict[str, JobDescriptor] = {}
        self._cancellations: dict[str, JobCancellation] = {}

    def register(self, chat_id: int, descriptor: JobDescriptor) -> JobCancellation:
        """Register a new job for ``chat_id`` and return its cancellation handle."""

        job_id = secrets.token_hex(4)
        with self._lock:
            descriptor.job_id = job_id
            descriptor.chat_id = chat_id
            self._descriptors[job_id] = descriptor
            cancellation = JobCancellation(job_id=job_id, event=asyncio.Event())
            self._cancellations[job_id] = cancellation
        logging.info(
            "job register: chat=%d kind=%s id=%s label=%r",
            chat_id, descriptor.kind, job_id, descriptor.label,
        )
        return cancellation

    def get(self, job_id: str) -> JobCancellation | None:
        """Return the cancellation handle, or None if unknown / already finished."""

        with self._lock:
            return self._cancellations.get(job_id)

    def list_for_chat(self, chat_id: int) -> list[JobDescriptor]:
        """Return descriptors for jobs running in ``chat_id`` (sorted by start)."""

        with self._lock:
            descriptors = [
                d for d in self._descriptors.values() if d.chat_id == chat_id
            ]
        descriptors.sort(key=lambda d: d.started_at)
        return descriptors

    def update_label(self, job_id: str, label: str) -> None:
        """Change the displayed label of a running job. No-op when unknown."""

        with self._lock:
            descriptor = self._descriptors.get(job_id)
            if descriptor is not None:
                descriptor.label = label

    def unregister(self, job_id: str) -> None:
        """Drop a finished job. No-op when unknown."""

        with self._lock:
            descriptor = self._descriptors.pop(job_id, None)
            self._cancellations.pop(job_id, None)
        if descriptor is not None:
            logging.info(
                "job unregister: chat=%d kind=%s id=%s",
                descriptor.chat_id, descriptor.kind, job_id,
            )


# Global singleton consumed by handlers/services.
job_registry = JobRegistry()
```

- [ ] **Step 2.4: Run tests, expect PASS**

```bash
pytest tests/test_jobs.py -v
```

- [ ] **Step 2.5: Commit**

```bash
git add bot/jobs.py tests/test_jobs.py
git commit -m "Add JobRegistry with JobCancellation + JobDescriptor for /stop"
```

---

## Task 3: `JobRegistry.cancel()` — sygnał + terminate subprocess + cancel pyrogram task

**Files:**
- Modify: `/mnt/c/code/ytdown/bot/jobs.py`
- Modify: `/mnt/c/code/ytdown/tests/test_jobs.py`

- [ ] **Step 3.1: Failing tests**

Append to `tests/test_jobs.py`:

```python
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
```

- [ ] **Step 3.2: Run tests, expect FAIL**

```bash
pytest tests/test_jobs.py -v -k "cancel"
```

- [ ] **Step 3.3: Add `cancel` and `cancel_async` to `JobRegistry`**

Insert these methods inside `class JobRegistry:` (after `unregister`):

```python
    def cancel(self, job_id: str, reason: str = "user via /stop") -> bool:
        """Sync cancel: set the event + reason. Subprocess/task teardown happens
        in cancel_async (called by the /stop handler which is async).

        Returns True if the job was registered and signalled, False otherwise.
        """

        with self._lock:
            cancellation = self._cancellations.get(job_id)
            if cancellation is None:
                return False
            cancellation.cancelled_reason = reason
            cancellation.event.set()
        logging.info("job cancel: id=%s reason=%r", job_id, reason)
        return True

    async def cancel_async(self, job_id: str, reason: str = "user via /stop") -> bool:
        """Async cancel: also terminates an attached subprocess and cancels an
        attached pyrogram_task. Used by the /stop callback handler."""

        from bot.security_limits import JOB_TERMINATE_GRACE_SEC

        with self._lock:
            cancellation = self._cancellations.get(job_id)
            if cancellation is None:
                return False
            cancellation.cancelled_reason = reason
            cancellation.event.set()
            process = cancellation.process
            task = cancellation.pyrogram_task

        if process is not None:
            try:
                process.terminate()
                try:
                    await asyncio.wait_for(
                        process.wait(), timeout=JOB_TERMINATE_GRACE_SEC,
                    )
                except asyncio.TimeoutError:
                    logging.warning(
                        "job %s: SIGTERM grace expired, sending SIGKILL", job_id,
                    )
                    process.kill()
            except ProcessLookupError:
                # Already exited — nothing to do.
                pass

        if task is not None and not task.done():
            task.cancel()

        logging.info("job cancel_async done: id=%s reason=%r", job_id, reason)
        return True
```

- [ ] **Step 3.4: Run tests, expect PASS**

```bash
pytest tests/test_jobs.py -v
```

- [ ] **Step 3.5: Commit**

```bash
git add bot/jobs.py tests/test_jobs.py
git commit -m "Add JobRegistry.cancel and cancel_async with subprocess/task teardown"
```

---

## Task 4: `JobRegistry.purge_dead()` — usuwanie zombie joboów

**Files:**
- Modify: `/mnt/c/code/ytdown/bot/jobs.py`
- Modify: `/mnt/c/code/ytdown/tests/test_jobs.py`

- [ ] **Step 4.1: Failing tests**

Append to `tests/test_jobs.py`:

```python
def test_purge_dead_removes_old_entries():
    from bot.jobs import JobRegistry

    registry = JobRegistry()
    old = _descriptor(label="old")
    old.started_at = datetime.now() - timedelta(hours=7)
    registry.register(1, old)
    fresh = _descriptor(label="fresh")
    registry.register(1, fresh)

    removed = registry.purge_dead(threshold=timedelta(hours=6))

    assert removed == 1
    labels = [d.label for d in registry.list_for_chat(1)]
    assert labels == ["fresh"]


def test_purge_dead_returns_zero_when_all_fresh():
    from bot.jobs import JobRegistry

    registry = JobRegistry()
    registry.register(1, _descriptor(label="A"))
    registry.register(1, _descriptor(label="B"))

    removed = registry.purge_dead(threshold=timedelta(hours=6))

    assert removed == 0
    assert len(registry.list_for_chat(1)) == 2
```

- [ ] **Step 4.2: Run, expect FAIL**

```bash
pytest tests/test_jobs.py -v -k "purge_dead"
```

- [ ] **Step 4.3: Add `purge_dead` to JobRegistry**

Insert in `class JobRegistry:` after `cancel_async`:

```python
    def purge_dead(self, threshold: timedelta) -> int:
        """Drop entries older than ``threshold``. Used by cleanup.py.

        Returns the number of entries removed. Logs each as a warning —
        a zombie job means a layer failed to call unregister in finally.
        """

        cutoff = datetime.now() - threshold
        removed = 0
        with self._lock:
            for job_id in list(self._descriptors):
                descriptor = self._descriptors[job_id]
                if descriptor.started_at < cutoff:
                    age_h = (datetime.now() - descriptor.started_at).total_seconds() / 3600
                    logging.warning(
                        "purge zombie job: id=%s kind=%s chat=%d age=%.1fh",
                        job_id, descriptor.kind, descriptor.chat_id, age_h,
                    )
                    self._descriptors.pop(job_id, None)
                    self._cancellations.pop(job_id, None)
                    removed += 1
        return removed
```

- [ ] **Step 4.4: Run, expect PASS**

```bash
pytest tests/test_jobs.py -v
```

- [ ] **Step 4.5: Commit**

```bash
git add bot/jobs.py tests/test_jobs.py
git commit -m "Add JobRegistry.purge_dead for zombie job cleanup"
```

---

## Task 5: `ArchivePartialState` w `session_store.py`

**Files:**
- Modify: `/mnt/c/code/ytdown/bot/session_store.py`
- Modify: `/mnt/c/code/ytdown/tests/test_session_store.py`

- [ ] **Step 5.1: Failing test**

Append to `tests/test_session_store.py`:

```python
def test_partial_archive_workspaces_field_holds_state():
    from bot.session_store import (
        ArchivePartialState,
        partial_archive_workspaces,
        session_store,
    )
    from datetime import datetime
    from pathlib import Path

    session_store.reset()
    state = ArchivePartialState(
        workspace=Path("/tmp/ws"),
        downloaded=[Path("/tmp/ws/a.mp3"), Path("/tmp/ws/b.mp3")],
        title="My playlist",
        media_type="audio",
        format_choice="mp3",
        use_mtproto=True,
        created_at=datetime(2026, 5, 3, 10, 0, 0),
    )
    partial_archive_workspaces[55] = {"tok-1": state}

    assert partial_archive_workspaces[55] == {"tok-1": state}
    session_store.reset()
```

- [ ] **Step 5.2: Run, expect FAIL**

```bash
pytest tests/test_session_store.py -v -k "partial_archive"
```

- [ ] **Step 5.3: Add `ArchivePartialState` and field**

In `bot/session_store.py`, locate `ArchivedDeliveryState` and add after it:

```python
@dataclass
class ArchivePartialState:
    """In-memory state for a cancelled-mid-flight playlist archive.

    Captured by execute_playlist_archive_flow when a cancel signal arrives
    after some entries downloaded; lets the user click [Spakuj co mam] to
    package whatever was already pulled.
    """

    workspace: Any        # pathlib.Path
    downloaded: list[Any] # list[Path]
    title: str
    media_type: str
    format_choice: str
    use_mtproto: bool
    created_at: Any       # datetime
```

In `SessionState`, add field after `archived_deliveries`:

```python
    partial_archive_workspaces: dict[str, "ArchivePartialState"] | None = None
```

In `_cleanup_if_empty` predicate, add the new field to the conjunction:

```python
            and session.archived_deliveries is None
            and session.partial_archive_workspaces is None
```

At the end of file (after `archived_deliveries = SessionFieldMap(...)`):

```python
partial_archive_workspaces = SessionFieldMap(session_store, "partial_archive_workspaces")
```

- [ ] **Step 5.4: Run, expect PASS**

```bash
pytest tests/test_session_store.py -v
```

- [ ] **Step 5.5: Commit**

```bash
git add bot/session_store.py tests/test_session_store.py
git commit -m "Add ArchivePartialState session field for /stop recovery flow"
```

---

## Task 6: `pack_to_volumes` przyjmuje `cancellation`

**Files:**
- Modify: `/mnt/c/code/ytdown/bot/archive.py`
- Modify: `/mnt/c/code/ytdown/tests/test_archive.py`

- [ ] **Step 6.1: Failing tests**

Append to `tests/test_archive.py`:

```python
def test_pack_to_volumes_attaches_process_to_cancellation(tmp_path, monkeypatch):
    from bot import archive
    from bot.jobs import JobCancellation
    import asyncio

    src = tmp_path / "a.bin"
    src.write_bytes(b"x")
    dest = tmp_path / "out"

    captured_cancellation = JobCancellation(job_id="t", event=asyncio.Event())

    completed = mock.AsyncMock()
    completed.communicate = mock.AsyncMock(return_value=(b"", b""))
    completed.returncode = 0

    async def fake_exec(*args, **kwargs):
        # Simulate completed pack with one volume.
        (tmp_path / "out.7z.001").write_bytes(b"v")
        return completed

    with mock.patch("bot.archive.asyncio.create_subprocess_exec", side_effect=fake_exec):
        asyncio.run(
            archive.pack_to_volumes(
                [src], dest, volume_size_mb=1, cancellation=captured_cancellation,
            )
        )

    # During run cancellation.process was set to the subprocess.
    # After completion, it does not need to be reset; verifying attach is sufficient.
    assert captured_cancellation.process is completed


def test_pack_to_volumes_terminates_on_cancel(tmp_path):
    from bot import archive
    from bot.jobs import JobCancellation
    import asyncio

    src = tmp_path / "a.bin"
    src.write_bytes(b"x")
    dest = tmp_path / "out"

    cancellation = JobCancellation(job_id="t", event=asyncio.Event())
    cancellation.event.set()  # already cancelled

    fake_proc = mock.MagicMock()
    fake_proc.communicate = mock.AsyncMock(return_value=(b"", b""))
    fake_proc.returncode = 0
    fake_proc.terminate = mock.MagicMock()
    fake_proc.kill = mock.MagicMock()
    fake_proc.wait = mock.AsyncMock(return_value=0)
    # Empty stdout so _stream_7z_progress exits quickly.
    fake_proc.stdout = mock.MagicMock()
    fake_proc.stdout.readline = mock.AsyncMock(return_value=b"")

    async def fake_exec(*args, **kwargs):
        return fake_proc

    with mock.patch("bot.archive.asyncio.create_subprocess_exec", side_effect=fake_exec):
        with pytest.raises(RuntimeError, match="cancelled"):
            asyncio.run(
                archive.pack_to_volumes(
                    [src], dest, volume_size_mb=1, cancellation=cancellation,
                )
            )

    fake_proc.terminate.assert_called_once()


def test_pack_to_volumes_cleans_partial_volumes_on_cancel(tmp_path):
    from bot import archive
    from bot.jobs import JobCancellation
    import asyncio

    src = tmp_path / "a.bin"
    src.write_bytes(b"x")
    dest = tmp_path / "playlist"
    cancellation = JobCancellation(job_id="t", event=asyncio.Event())
    cancellation.event.set()

    fake_proc = mock.MagicMock()
    fake_proc.communicate = mock.AsyncMock(return_value=(b"", b""))
    fake_proc.returncode = 0
    fake_proc.terminate = mock.MagicMock()
    fake_proc.kill = mock.MagicMock()
    fake_proc.wait = mock.AsyncMock(return_value=0)
    fake_proc.stdout = mock.MagicMock()
    fake_proc.stdout.readline = mock.AsyncMock(return_value=b"")

    async def fake_exec(*args, **kwargs):
        # Pretend two volumes started writing before terminate.
        (tmp_path / "playlist.7z.001").write_bytes(b"a")
        (tmp_path / "playlist.7z.002").write_bytes(b"b")
        return fake_proc

    with mock.patch("bot.archive.asyncio.create_subprocess_exec", side_effect=fake_exec):
        with pytest.raises(RuntimeError, match="cancelled"):
            asyncio.run(
                archive.pack_to_volumes(
                    [src], dest, volume_size_mb=1, cancellation=cancellation,
                )
            )

    # Partial volumes must be cleaned up.
    assert not (tmp_path / "playlist.7z.001").exists()
    assert not (tmp_path / "playlist.7z.002").exists()
```

- [ ] **Step 6.2: Run, expect FAIL**

```bash
pytest tests/test_archive.py -v -k "cancel"
```

- [ ] **Step 6.3: Modify `pack_to_volumes`**

In `bot/archive.py`, change the signature and body:

```python
async def pack_to_volumes(
    sources: Sequence[Path],
    dest_basename: Path,
    volume_size_mb: int,
    *,
    progress_cb: Callable[[str], Awaitable[None]] | None = None,
    cancellation: "JobCancellation | None" = None,
) -> list[Path]:
    """Pack ``sources`` into a 7z multi-volume archive at ``dest_basename``.

    When ``cancellation`` is provided, the spawned 7z process is attached
    to it so a /stop signal can terminate it. On cancellation the partial
    .7z.NNN volumes are removed and RuntimeError("cancelled") is raised.

    Resulting volumes are named ``<dest_basename>.7z.001``, ``.002`` etc.
    Returns the sorted list of created volume paths on success.
    """

    if not sources:
        raise ValueError("empty sources")

    archive_path = dest_basename.with_suffix(".7z")
    args = [
        "7z", "a", "-t7z", f"-v{volume_size_mb}m", "-mx0", "-mmt=on",
        str(archive_path),
        *[str(src) for src in sources],
    ]

    logging.info("Running 7z pack: %s", " ".join(args))
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    if cancellation is not None:
        cancellation.process = process

    try:
        if progress_cb is not None:
            await _stream_7z_progress(process, progress_cb, cancellation)

        # If user cancelled before we started reading stdout, terminate now.
        if cancellation is not None and cancellation.event.is_set():
            await _terminate_with_grace(process)
            _remove_partial_volumes(dest_basename)
            raise RuntimeError("cancelled")

        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            err = stderr.decode("utf-8", errors="replace")[:200]
            raise RuntimeError(f"7z failed (exit {process.returncode}): {err}")
    except asyncio.CancelledError:
        await _terminate_with_grace(process)
        _remove_partial_volumes(dest_basename)
        raise

    parent = dest_basename.parent
    prefix = f"{archive_path.name}."
    volumes = sorted(
        p for p in parent.iterdir()
        if p.name.startswith(prefix) and p.name[len(prefix):].isdigit()
    )
    logging.info("7z packed %d volume(s) for %s", len(volumes), archive_path)
    return volumes


async def _terminate_with_grace(process: asyncio.subprocess.Process) -> None:
    """SIGTERM → grace → SIGKILL fallback."""

    from bot.security_limits import JOB_TERMINATE_GRACE_SEC

    if process.returncode is not None:
        return
    try:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=JOB_TERMINATE_GRACE_SEC)
        except asyncio.TimeoutError:
            logging.warning("7z SIGTERM grace expired, sending SIGKILL")
            process.kill()
            await process.wait()
    except ProcessLookupError:
        pass


def _remove_partial_volumes(dest_basename: Path) -> None:
    """Delete any <dest>.7z.NNN files left after a cancelled pack."""

    archive_name = dest_basename.with_suffix(".7z").name
    prefix = f"{archive_name}."
    for entry in dest_basename.parent.iterdir():
        if entry.name.startswith(prefix) and entry.name[len(prefix):].isdigit():
            try:
                entry.unlink()
            except OSError as exc:
                logging.warning("Could not remove partial volume %s: %s", entry, exc)
```

Update `_stream_7z_progress` signature to accept the optional cancellation and break on event:

```python
async def _stream_7z_progress(
    process: asyncio.subprocess.Process,
    progress_cb: Callable[[str], Awaitable[None]],
    cancellation: "JobCancellation | None" = None,
) -> None:
    """Throttle 7z stdout updates to one progress_cb call every 2 seconds.

    When ``cancellation`` is provided and its event becomes set, the loop
    exits so the caller can terminate the subprocess.
    """

    if process.stdout is None:
        return

    last_update = 0.0
    loop = asyncio.get_running_loop()
    while True:
        if cancellation is not None and cancellation.event.is_set():
            return
        line = await process.stdout.readline()
        if not line:
            break
        now = loop.time()
        if now - last_update < 2.0:
            continue
        decoded = line.decode("utf-8", errors="replace").strip()
        if decoded:
            try:
                await progress_cb(decoded)
            except Exception as exc:
                logging.warning("Archive progress callback failed: %s", exc)
            last_update = now
```

- [ ] **Step 6.4: Run, expect PASS**

```bash
pytest tests/test_archive.py -v
```

- [ ] **Step 6.5: Commit**

```bash
git add bot/archive.py tests/test_archive.py
git commit -m "Wire cancellation into pack_to_volumes (terminate + cleanup partials)"
```

---

## Task 7: `execute_download` progress hook respects cancellation

**Files:**
- Modify: `/mnt/c/code/ytdown/bot/services/download_service.py`
- Modify: `/mnt/c/code/ytdown/tests/test_download_service.py`

- [ ] **Step 7.1: Failing test**

Append to `tests/test_download_service.py`:

```python
def test_execute_download_progress_hook_raises_on_cancel(tmp_path, monkeypatch):
    """When cancellation.event is set, the wrapped progress hook raises DownloadError."""
    import asyncio
    import yt_dlp

    from bot.jobs import JobCancellation
    from bot.services import download_service

    cancellation = JobCancellation(job_id="t", event=asyncio.Event())

    captured_hook = {}

    def fake_progress_factory(_chat_id):
        def real_hook(_data):
            return None
        return real_hook

    # Capture the wrapped hook by patching create_progress_hook to return a
    # known function that download_service then wraps.
    plan = mock.MagicMock()
    plan.url = "u"
    plan.duration_str = "0:30"
    plan.ydl_opts = {}
    plan.chat_download_path = str(tmp_path)
    plan.sanitized_title = "x"
    plan.output_path = str(tmp_path / "x")

    progress_state: dict = {}
    state_after = {}

    # Patch find_downloaded_file to keep things short.
    monkeypatch.setattr(download_service, "find_downloaded_file", lambda p: None)

    async def fake_download_call():
        # Look up the wrapped hook attached to ydl_opts['progress_hooks'].
        wrapped = plan.ydl_opts['progress_hooks'][0]
        captured_hook["fn"] = wrapped
        cancellation.event.set()
        # Calling wrapped hook should raise.
        with pytest.raises(yt_dlp.utils.DownloadError, match="cancelled"):
            wrapped({"status": "downloading", "downloaded_bytes": 1})
        raise FileNotFoundError("simulated finish")

    with mock.patch("yt_dlp.YoutubeDL") as ydl_mock:
        instance = ydl_mock.return_value
        instance.download = lambda urls: asyncio.get_event_loop().run_until_complete(fake_download_call())

        with pytest.raises(FileNotFoundError):
            asyncio.run(
                download_service.execute_download(
                    plan,
                    chat_id=1,
                    executor=mock.MagicMock(submit=lambda fn: type("F", (), {
                        "done": lambda self: True,
                        "result": fn,
                    })()),
                    progress_hook_factory=fake_progress_factory,
                    progress_state=progress_state,
                    status_callback=mock.AsyncMock(),
                    format_bytes=str,
                    format_eta=str,
                    cancellation=cancellation,
                )
            )

    assert "fn" in captured_hook
```

(The test is intentionally narrow — it only verifies that when `execute_download` builds `ydl_opts['progress_hooks']`, the wrapped hook honors the cancellation event by raising `yt_dlp.utils.DownloadError`. The full download flow is heavier than needed here.)

- [ ] **Step 7.2: Run, expect FAIL**

```bash
pytest tests/test_download_service.py -v -k "cancel"
```

- [ ] **Step 7.3: Modify `execute_download`**

In `bot/services/download_service.py`, change `execute_download` signature and add cancellation wrapping:

```python
async def execute_download(
    plan: DownloadPlan,
    *,
    chat_id: int,
    executor: Any,
    progress_hook_factory: Callable[[int], Callable[[dict[str, Any]], None]],
    progress_state: dict[int, dict[str, Any]],
    status_callback: Callable[[str], Any],
    format_bytes: Callable[[int | float | None], str],
    format_eta: Callable[[int | float | None], str],
    cancellation: "JobCancellation | None" = None,
) -> DownloadResult:
    """Run yt-dlp download and stream progress updates through a callback.

    When ``cancellation`` is provided, the progress hook raises
    yt_dlp.utils.DownloadError("cancelled") whenever the event is set.
    yt-dlp catches that and cleans up the .part file automatically.
    """

    ydl_opts = plan.ydl_opts.copy()

    base_hook = progress_hook_factory(chat_id)

    def hook(d):
        if cancellation is not None and cancellation.event.is_set():
            import yt_dlp
            raise yt_dlp.utils.DownloadError("cancelled by user")
        base_hook(d)

    ydl_opts['progress_hooks'] = [hook]
    progress_state[chat_id] = {'status': 'starting', 'updated': time.time()}

    loop = asyncio.get_event_loop()
    future = loop.run_in_executor(
        executor,
        lambda: yt_dlp.YoutubeDL(ydl_opts).download([plan.url]),
    )

    last_update = ""
    try:
        while not future.done():
            progress = progress_state.get(chat_id, {})
            if progress.get('status') == 'downloading':
                percent = progress.get('percent', '?%')
                downloaded = format_bytes(progress.get('downloaded', 0))
                total = format_bytes(progress.get('total', 0))
                speed = (
                    format_bytes(progress.get('speed', 0)) + "/s"
                    if progress.get('speed') else "?"
                )
                eta = format_eta(progress.get('eta'))

                status_text = (
                    f"Pobieranie: {percent}\n\n"
                    f"Pobrano: {downloaded} / {total}\n"
                    f"Prędkość: {speed}\n"
                    f"Pozostało: {eta}\n\n"
                    f"Czas trwania: {plan.duration_str}"
                )

                if status_text != last_update:
                    last_update = status_text
                    await status_callback(status_text)

            await asyncio.sleep(1)

        await future
    finally:
        progress_state.pop(chat_id, None)

    downloaded_file_path = find_downloaded_file(plan)
    if not downloaded_file_path:
        raise FileNotFoundError("downloaded file not found")

    file_size_mb = os.path.getsize(downloaded_file_path) / (1024 * 1024)
    return DownloadResult(file_path=downloaded_file_path, file_size_mb=file_size_mb)
```

- [ ] **Step 7.4: Run, expect PASS**

```bash
pytest tests/test_download_service.py -v
```

- [ ] **Step 7.5: Commit**

```bash
git add bot/services/download_service.py tests/test_download_service.py
git commit -m "Honor cancellation in execute_download progress hook"
```

---

## Task 8: `download_playlist_into` respects cancellation

**Files:**
- Modify: `/mnt/c/code/ytdown/bot/services/archive_service.py`
- Modify: `/mnt/c/code/ytdown/tests/test_archive_service.py`

- [ ] **Step 8.1: Failing test**

Append to `tests/test_archive_service.py`:

```python
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
```

- [ ] **Step 8.2: Run, expect FAIL**

```bash
pytest tests/test_archive_service.py -v -k "breaks_on_cancel"
```

- [ ] **Step 8.3: Modify `download_playlist_into`**

Change signature and add the event check at the top of the loop:

```python
async def download_playlist_into(
    workspace: Path,
    entries: list[dict],
    *,
    media_type: str,
    format_choice: str,
    executor: ThreadPoolExecutor,
    status_cb: Callable[[str], Awaitable[None]],
    cancellation: "JobCancellation | None" = None,
) -> tuple[list[Path], list[str]]:
    """Download every entry into workspace, keeping the files (no os.remove).

    When ``cancellation.event`` becomes set, the loop breaks early; titles
    of entries that didn't run get ``" (anulowano)"`` suffix in failed_titles.
    """

    downloaded: list[Path] = []
    failed: list[str] = []
    total = len(entries)

    for idx, entry in enumerate(entries, 1):
        title = entry.get("title", f"item_{idx}")
        if cancellation is not None and cancellation.event.is_set():
            for skipped in entries[idx - 1:]:
                failed.append(f"{skipped.get('title', '?')} (anulowano)")
            break
        await status_cb(f"[{idx}/{total}] Pobieranie: {title}...")
        try:
            path, size = await _download_one_into_workspace(
                entry,
                workspace,
                media_type=media_type,
                format_choice=format_choice,
                executor=executor,
            )
        except Exception as exc:
            logging.error("Archive download failed for %s: %s", title, exc)
            failed.append(title)
            continue

        if path is None:
            mb_str = f"{size:.0f} MB" if size is not None else "?"
            failed.append(f"{title} (za duzy: {mb_str})")
            continue

        downloaded.append(path)

    return downloaded, failed
```

- [ ] **Step 8.4: Run, expect PASS**

```bash
pytest tests/test_archive_service.py -v
```

- [ ] **Step 8.5: Commit**

```bash
git add bot/services/archive_service.py tests/test_archive_service.py
git commit -m "Honor cancellation in download_playlist_into loop"
```

---

## Task 9: `send_volumes` respects cancellation

**Files:**
- Modify: `/mnt/c/code/ytdown/bot/services/archive_service.py`
- Modify: `/mnt/c/code/ytdown/tests/test_archive_service.py`

- [ ] **Step 9.1: Failing test**

Append to `tests/test_archive_service.py`:

```python
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
```

- [ ] **Step 9.2: Run, expect FAIL**

```bash
pytest tests/test_archive_service.py -v -k "send_volumes_breaks"
```

- [ ] **Step 9.3: Modify `send_volumes`**

```python
async def send_volumes(
    bot,
    chat_id: int,
    volumes: list[Path],
    caption_prefix: str,
    use_mtproto: bool,
    *,
    start_index: int = 0,
    status_cb: Callable[[str], Awaitable[None]] | None = None,
    cancellation: "JobCancellation | None" = None,
) -> None:
    """Send 7z volumes [start_index:] to ``chat_id`` as documents.

    Volumes ≤ TELEGRAM_UPLOAD_LIMIT_MB go via Bot API; larger ones via
    MTProto. When cancellation.event is set the loop breaks before the
    next volume.
    """

    total = len(volumes)
    for idx in range(start_index, total):
        if cancellation is not None and cancellation.event.is_set():
            return
        volume = volumes[idx]
        size_mb = volume.stat().st_size / (1024 * 1024)
        caption = f"{caption_prefix} [{idx + 1}/{total}]"
        if status_cb is not None:
            await status_cb(f"Wysyłanie [{idx + 1}/{total}] ({size_mb:.0f} MB)...")

        if size_mb <= TELEGRAM_UPLOAD_LIMIT_MB:
            with open(volume, "rb") as handle:
                await bot.send_document(
                    chat_id=chat_id,
                    document=handle,
                    filename=volume.name,
                    caption=caption,
                    read_timeout=120,
                    write_timeout=120,
                )
        else:
            reason = mtproto_unavailability_reason()
            if reason is not None:
                raise RuntimeError(
                    f"Wolumen {volume.name} przekracza Bot API ({size_mb:.0f} MB), "
                    f"a MTProto jest niedostępny: {reason}"
                )
            ok = await send_document_mtproto(
                chat_id=chat_id,
                file_path=str(volume),
                caption=caption,
                file_name=volume.name,
            )
            if not ok:
                raise RuntimeError(f"Wysyłka {volume.name} przez MTProto nie powiodła się.")

        logging.info("Sent volume %d/%d: %s (%.1f MB)", idx + 1, total, volume.name, size_mb)
```

- [ ] **Step 9.4: Run, expect PASS**

```bash
pytest tests/test_archive_service.py -v
```

- [ ] **Step 9.5: Commit**

```bash
git add bot/services/archive_service.py tests/test_archive_service.py
git commit -m "Honor cancellation in send_volumes loop"
```

---

## Task 10: `execute_playlist_archive_flow` rejestruje job, captures partial state

**Files:**
- Modify: `/mnt/c/code/ytdown/bot/services/archive_service.py`
- Modify: `/mnt/c/code/ytdown/tests/test_archive_service.py`

- [ ] **Step 10.1: Failing tests**

Append to `tests/test_archive_service.py`:

```python
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
```

- [ ] **Step 10.2: Run, expect FAIL**

```bash
pytest tests/test_archive_service.py -v -k "execute_playlist_archive_flow"
```

- [ ] **Step 10.3: Modify `execute_playlist_archive_flow`**

In `bot/services/archive_service.py`, add imports near the top:

```python
from datetime import datetime as _datetime  # alias to avoid shadow
from bot.jobs import JobDescriptor, job_registry
from bot.session_store import (
    ArchivePartialState,
    ArchivedDeliveryState,
    archived_deliveries,
    partial_archive_workspaces,
    pending_archive_jobs,
)
```

(Adjust existing imports — `partial_archive_workspaces` and `ArchivePartialState` are new.)

Wrap `execute_playlist_archive_flow` body in a try/finally that registers/unregisters the job, propagates cancellation, and captures partial state on cancel. Replace the existing function body with:

```python
async def execute_playlist_archive_flow(
    update,
    context,
    *,
    chat_id: int,
    playlist: dict[str, Any],
    media_type: str,
    format_choice: str,
    executor: ThreadPoolExecutor,
) -> None:
    """End-to-end: workspace → download all → pack to 7z → send volumes.

    Registers a job with job_registry so /stop can cancel mid-flight.
    On cancel during download phase, saves an ArchivePartialState so the
    user can click [Spakuj co mam] from the cancel-status message.
    """

    if not is_7z_available():
        await _safe_status_edit(
            update,
            "Funkcja 7z niedostępna — administrator nie zainstalował p7zip-full.",
        )
        return

    use_mtproto = mtproto_unavailability_reason() is None
    volume_size_mb = volume_size_for(use_mtproto)

    title = playlist.get("title", "Playlista")
    entries = playlist.get("entries") or []
    total = len(entries)

    descriptor = JobDescriptor(
        job_id="",  # filled by registry
        chat_id=chat_id,
        kind="playlist_zip",
        label=f"Playlist 7z ({media_type} {format_choice}) — start",
        started_at=datetime.now(),
    )
    cancellation = job_registry.register(chat_id, descriptor)

    workspace = prepare_playlist_workspace(chat_id, title, prefix="pl")
    lock_path = workspace / ".lock"
    lock_path.touch()

    async def status(text: str) -> None:
        await _safe_status_edit(update, text)

    await status(
        f"Playlista → 7z ({media_type} {format_choice})\n"
        f"[0/{total}] Pobieranie..."
    )

    downloaded: list[Path] = []
    failed: list[str] = []
    try:
        job_registry.update_label(
            cancellation.job_id,
            f"Playlist 7z ({media_type} {format_choice}) — pobieranie [0/{total}]",
        )
        downloaded, failed = await download_playlist_into(
            workspace,
            entries,
            media_type=media_type,
            format_choice=format_choice,
            executor=executor,
            status_cb=status,
            cancellation=cancellation,
        )

        if cancellation.event.is_set():
            await _save_partial_state_after_cancel(
                update, chat_id, workspace, downloaded, title,
                media_type, format_choice, use_mtproto, total,
            )
            return

        if not downloaded:
            shutil.rmtree(workspace, ignore_errors=True)
            await status("Nie udało się pobrać żadnego elementu.")
            return

        job_registry.update_label(
            cancellation.job_id,
            f"Playlist 7z ({media_type} {format_choice}) — pakowanie",
        )
        await status(f"Pakowanie do 7z (vol_size={volume_size_mb} MB)...")
        slug = _build_slug(title)
        dest_basename = workspace / compute_archive_basename(
            f"{slug}_{media_type}_{format_choice}", datetime.now()
        )
        volumes = await pack_to_volumes(
            downloaded, dest_basename, volume_size_mb,
            cancellation=cancellation,
        )

        if cancellation.event.is_set():
            # User cancelled during pack — pack_to_volumes already cleaned partials.
            await _save_partial_state_after_cancel(
                update, chat_id, workspace, downloaded, title,
                media_type, format_choice, use_mtproto, total,
            )
            return

        caption_prefix = f"{title} ({media_type} {format_choice})"
        job_registry.update_label(
            cancellation.job_id,
            f"Playlist 7z ({media_type} {format_choice}) — wysyłka [0/{len(volumes)}]",
        )
        await status(f"Pakowanie OK: {len(volumes)} paczek. Wysyłanie...")
        await send_volumes(
            context.bot,
            chat_id=chat_id,
            volumes=volumes,
            caption_prefix=caption_prefix,
            use_mtproto=use_mtproto,
            status_cb=status,
            cancellation=cancellation,
        )

        delivery = ArchivedDeliveryState(
            workspace=workspace,
            volumes=volumes,
            caption_prefix=caption_prefix,
            use_mtproto=use_mtproto,
            created_at=datetime.now(),
        )
        token = register_archived_delivery(chat_id, delivery)

        summary_lines = [
            "Playlista zakończona.",
            f"Pobrano: {len(downloaded)}/{total}",
            f"Spakowano: {len(downloaded)} plików → {len(volumes)} paczek 7z",
            f"Wysłano: {len(volumes)}/{len(volumes)}"
            if not cancellation.event.is_set()
            else f"Wysłano: <{len(volumes)} (anulowano)",
            f"Folder zostanie usunięty po {PLAYLIST_ARCHIVE_RETENTION_MIN} min.",
        ]
        if failed:
            summary_lines.append("")
            summary_lines.append("Nieudane elementy:")
            for title_ in failed[:5]:
                summary_lines.append(f"  - {title_[:60]}")

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "Wyślij wszystkie paczki ponownie",
                callback_data=f"arc_resend_{token}_0",
            )],
            [InlineKeyboardButton("Usuń teraz", callback_data=f"arc_purge_{token}")],
        ])
        try:
            await update.callback_query.edit_message_text(
                "\n".join(summary_lines), reply_markup=keyboard,
            )
        except Exception as exc:
            logging.debug("summary edit failed: %s", exc)
    except Exception as exc:
        logging.error("Playlist archive flow failed: %s", exc)
        await status(f"Pakowanie/wysyłka nie powiodły się: {exc}")
    finally:
        try:
            lock_path.unlink()
        except OSError:
            pass
        job_registry.unregister(cancellation.job_id)


async def _save_partial_state_after_cancel(
    update,
    chat_id: int,
    workspace: Path,
    downloaded: list[Path],
    title: str,
    media_type: str,
    format_choice: str,
    use_mtproto: bool,
    total: int,
) -> None:
    """Persist ArchivePartialState and edit the status message with recovery buttons."""

    if not downloaded:
        # Nothing to recover — drop the workspace.
        shutil.rmtree(workspace, ignore_errors=True)
        try:
            await update.callback_query.edit_message_text(
                "⏹ Zatrzymano. Nie pobrano żadnego elementu."
            )
        except Exception:
            pass
        return

    state = ArchivePartialState(
        workspace=workspace,
        downloaded=list(downloaded),
        title=title,
        media_type=media_type,
        format_choice=format_choice,
        use_mtproto=use_mtproto,
        created_at=datetime.now(),
    )
    bucket = partial_archive_workspaces.get(chat_id) or {}
    token = secrets.token_hex(4)
    bucket[token] = state
    partial_archive_workspaces[chat_id] = bucket

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "Spakuj co mam", callback_data=f"arc_pack_partial_{token}",
        )],
        [InlineKeyboardButton(
            "Usuń teraz", callback_data=f"arc_purge_partial_{token}",
        )],
    ])
    try:
        await update.callback_query.edit_message_text(
            f"⏹ Zatrzymano. Pobrano {len(downloaded)}/{total} plików.\n"
            f"Workspace zostanie usunięty po {PLAYLIST_ARCHIVE_RETENTION_MIN} min.",
            reply_markup=keyboard,
        )
    except Exception as exc:
        logging.debug("partial-state edit failed: %s", exc)
```

Make sure `secrets` is imported at the top of the file (it already is for `register_pending_archive_job`).

- [ ] **Step 10.4: Run, expect PASS**

```bash
pytest tests/test_archive_service.py -v
```

- [ ] **Step 10.5: Commit**

```bash
git add bot/services/archive_service.py tests/test_archive_service.py
git commit -m "Register playlist archive flow as job; capture partial state on cancel"
```

---

## Task 11: `execute_partial_archive_flow` + callbacks `arc_pack_partial_*` / `arc_purge_partial_*`

**Files:**
- Modify: `/mnt/c/code/ytdown/bot/services/archive_service.py`
- Modify: `/mnt/c/code/ytdown/bot/handlers/download_callbacks.py`
- Modify: `/mnt/c/code/ytdown/tests/test_archive_service.py`

- [ ] **Step 11.1: Failing test**

Append to `tests/test_archive_service.py`:

```python
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
```

- [ ] **Step 11.2: Run, expect FAIL**

```bash
pytest tests/test_archive_service.py -v -k "execute_partial_archive_flow"
```

- [ ] **Step 11.3: Add `execute_partial_archive_flow` to `bot/services/archive_service.py`**

```python
async def execute_partial_archive_flow(
    update,
    context,
    *,
    chat_id: int,
    token: str,
) -> None:
    """Pack and send a previously-cancelled playlist's downloaded files.

    Triggered by the [Spakuj co mam] button on a cancel-status message.
    Reads ArchivePartialState from session_store, registers a fresh
    archive_pack job, and runs pack_to_volumes + send_volumes.
    """

    bucket = partial_archive_workspaces.get(chat_id) or {}
    state = bucket.get(token)
    if state is None:
        await _safe_status_edit(update, "Sesja wygasła.")
        return

    if not is_7z_available():
        await _safe_status_edit(
            update,
            "Funkcja 7z niedostępna — administrator nie zainstalował p7zip-full.",
        )
        return

    use_mtproto = mtproto_unavailability_reason() is None
    volume_size_mb = volume_size_for(use_mtproto)

    descriptor = JobDescriptor(
        job_id="", chat_id=chat_id, kind="archive_pack",
        label=f"Pakowanie częściowej playlisty ({state.title})",
        started_at=datetime.now(),
    )
    cancellation = job_registry.register(chat_id, descriptor)

    async def status(text: str) -> None:
        await _safe_status_edit(update, text)

    try:
        await status(f"Pakowanie do 7z (vol_size={volume_size_mb} MB)...")
        slug = _build_slug(state.title)
        dest_basename = state.workspace / compute_archive_basename(
            f"{slug}_{state.media_type}_{state.format_choice}", datetime.now()
        )
        volumes = await pack_to_volumes(
            state.downloaded, dest_basename, volume_size_mb,
            cancellation=cancellation,
        )

        if cancellation.event.is_set():
            await status("⏹ Zatrzymano w trakcie pakowania.")
            return

        caption_prefix = f"{state.title} ({state.media_type} {state.format_choice})"
        job_registry.update_label(
            cancellation.job_id,
            f"Wysyłka częściowej playlisty [0/{len(volumes)}]",
        )
        await status(f"Pakowanie OK: {len(volumes)} paczek. Wysyłanie...")
        await send_volumes(
            context.bot, chat_id=chat_id, volumes=volumes,
            caption_prefix=caption_prefix, use_mtproto=use_mtproto,
            status_cb=status, cancellation=cancellation,
        )

        delivery = ArchivedDeliveryState(
            workspace=state.workspace, volumes=volumes,
            caption_prefix=caption_prefix, use_mtproto=use_mtproto,
            created_at=datetime.now(),
        )
        delivery_token = register_archived_delivery(chat_id, delivery)

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "Wyślij wszystkie paczki ponownie",
                callback_data=f"arc_resend_{delivery_token}_0",
            )],
            [InlineKeyboardButton(
                "Usuń teraz",
                callback_data=f"arc_purge_{delivery_token}",
            )],
        ])
        await update.callback_query.edit_message_text(
            f"Częściowa playlista wysłana w {len(volumes)} paczkach.",
            reply_markup=keyboard,
        )
    except Exception as exc:
        logging.error("Partial archive flow failed: %s", exc)
        await status(f"Pakowanie/wysyłka nie powiodły się: {exc}")
    finally:
        # Consume partial state regardless of outcome.
        bucket.pop(token, None)
        if not bucket:
            partial_archive_workspaces.pop(chat_id, None)
        else:
            partial_archive_workspaces[chat_id] = bucket
        job_registry.unregister(cancellation.job_id)
```

- [ ] **Step 11.4: Wire `arc_pack_partial_*` and `arc_purge_partial_*` in `bot/handlers/download_callbacks.py`**

Inside `handle_archive_callback`, before the existing `arc_resend_` branch, add:

```python
    if data.startswith("arc_pack_partial_"):
        token = data[len("arc_pack_partial_"):]
        from bot.services.archive_service import execute_partial_archive_flow
        await execute_partial_archive_flow(
            update, context, chat_id=chat_id, token=token,
        )
        return

    if data.startswith("arc_purge_partial_"):
        token = data[len("arc_purge_partial_"):]
        await _handle_arc_purge_partial(update, chat_id, token)
        return
```

Add the helper:

```python
async def _handle_arc_purge_partial(update, chat_id: int, token: str) -> None:
    from bot.session_store import partial_archive_workspaces

    bucket = partial_archive_workspaces.get(chat_id) or {}
    state = bucket.pop(token, None)
    if not bucket:
        partial_archive_workspaces.pop(chat_id, None)
    else:
        partial_archive_workspaces[chat_id] = bucket
    if state is None:
        try:
            await update.callback_query.edit_message_text("Sesja wygasła.")
        except Exception:
            pass
        return
    if state.workspace.exists():
        shutil.rmtree(state.workspace, ignore_errors=True)
    try:
        await update.callback_query.edit_message_text("Folder usunięty.")
    except Exception as exc:
        logging.debug("arc_purge_partial edit failed: %s", exc)
```

- [ ] **Step 11.5: Run, expect PASS**

```bash
pytest tests/test_archive_service.py tests/test_callback_download_handlers.py -v
```

- [ ] **Step 11.6: Commit**

```bash
git add bot/services/archive_service.py bot/handlers/download_callbacks.py tests/test_archive_service.py
git commit -m "Add execute_partial_archive_flow and arc_pack_partial_/purge_partial_ callbacks"
```

---

## Task 12: Legacy `download_playlist` (per-item-send) registers job

**Files:**
- Modify: `/mnt/c/code/ytdown/bot/handlers/playlist_callbacks.py`
- Modify: `/mnt/c/code/ytdown/tests/test_playlist.py`

- [ ] **Step 12.1: Failing test**

Append to `tests/test_playlist.py`:

```python
def test_legacy_download_playlist_breaks_on_cancel(monkeypatch):
    import asyncio
    from bot.handlers import playlist_callbacks
    from bot.jobs import JobRegistry
    from bot.session_store import session_store, user_playlist_data

    session_store.reset()
    user_playlist_data[42] = {
        "title": "Pl",
        "entries": [
            {"url": "u1", "title": "a"},
            {"url": "u2", "title": "b"},
            {"url": "u3", "title": "c"},
        ],
    }
    test_registry = JobRegistry()
    monkeypatch.setattr(playlist_callbacks, "job_registry", test_registry)

    iter_count = {"n": 0}

    async def fake_dl(*args, **kwargs):
        iter_count["n"] += 1
        if iter_count["n"] == 2:
            # Cancel after 2nd iteration starts.
            jobs = test_registry.list_for_chat(42)
            test_registry.cancel(jobs[0].job_id, reason="t")

    monkeypatch.setattr(
        playlist_callbacks, "_download_single_playlist_item", fake_dl,
    )

    update = mock.MagicMock()
    update.effective_chat.id = 42
    update.callback_query = mock.MagicMock()
    update.callback_query.edit_message_text = mock.AsyncMock()
    context = mock.MagicMock()
    context.bot.send_message = mock.AsyncMock()

    asyncio.run(
        playlist_callbacks.download_playlist(
            update, context, "pl_dl_audio_mp3",
        )
    )

    # Job unregistered.
    assert test_registry.list_for_chat(42) == []
    # Loop broke after 2nd iteration; 3rd not invoked.
    assert iter_count["n"] == 2
    session_store.reset()
```

- [ ] **Step 12.2: Run, expect FAIL**

```bash
pytest tests/test_playlist.py -v -k "legacy_download_playlist_breaks_on_cancel"
```

- [ ] **Step 12.3: Modify `download_playlist`**

Add at top of `bot/handlers/playlist_callbacks.py`:

```python
from datetime import datetime
from bot.jobs import JobDescriptor, job_registry
```

In `download_playlist`, wrap the existing loop with register/unregister:

```python
async def download_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE, callback_data: str):
    query = update.callback_query
    chat_id = update.effective_chat.id

    playlist = _get_session_value(context, chat_id, "playlist_data", user_playlist_data)
    if not playlist:
        await query.edit_message_text("Sesja playlisty wygasła. Wyślij link ponownie.")
        return

    entries = playlist["entries"]
    choice = parse_playlist_download_choice(callback_data)
    media_type = choice.media_type
    format_choice = choice.format_choice

    total = len(entries)
    succeeded = 0
    failed_titles = []

    descriptor = JobDescriptor(
        job_id="",
        chat_id=chat_id,
        kind="playlist_legacy",
        label=f"Playlist ({media_type} {format_choice}) [0/{total}]",
        started_at=datetime.now(),
    )
    cancellation = job_registry.register(chat_id, descriptor)

    await query.edit_message_text(
        f"Rozpoczynam pobieranie playlisty ({total} filmów)...\n"
        f"Format: {media_type} {format_choice}"
    )

    try:
        for i, entry in enumerate(entries, 1):
            if cancellation.event.is_set():
                break
            entry_url = entry["url"]
            entry_title = entry.get("title", f"Film {i}")
            job_registry.update_label(
                cancellation.job_id,
                f"Playlist ({media_type} {format_choice}) [{i}/{total}]",
            )

            try:
                status_msg = await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"[{i}/{total}] Pobieranie: {entry_title}...",
                )
                await _download_single_playlist_item(
                    context, chat_id, entry_url, entry_title,
                    media_type, format_choice, status_msg,
                )
                succeeded += 1
            except Exception as exc:
                failed_titles.append(entry_title)
                logging.error("Playlist item %d/%d failed: %s", i, total, exc)
                try:
                    await status_msg.edit_text(
                        f"[{i}/{total}] Błąd: {entry_title}\n{str(exc)[:100]}"
                    )
                except Exception:
                    pass

            if i < total and not cancellation.event.is_set():
                await asyncio.sleep(1)

        failed = len(failed_titles)
        if cancellation.event.is_set():
            summary = (
                f"⏹ Zatrzymano playlistę. Pobrano {succeeded}/{total} "
                f"(pliki usunięte).\n"
            )
        else:
            summary = f"Playlista zakończona!\n\nPobrano: {succeeded}/{total}\n"
        if failed:
            summary += f"Błędy: {failed}\n"
            for title in failed_titles[:5]:
                summary += f"  - {title[:40]}\n"

        await context.bot.send_message(chat_id=chat_id, text=summary)
        _clear_session_value(context, chat_id, "playlist_data", user_playlist_data)
    finally:
        job_registry.unregister(cancellation.job_id)
```

- [ ] **Step 12.4: Run, expect PASS**

```bash
pytest tests/test_playlist.py -v
```

- [ ] **Step 12.5: Commit**

```bash
git add bot/handlers/playlist_callbacks.py tests/test_playlist.py
git commit -m "Register legacy download_playlist as job and respect cancellation"
```

---

## Task 13: `download_file` registers job and propagates cancellation

**Files:**
- Modify: `/mnt/c/code/ytdown/bot/handlers/download_callbacks.py`
- Modify: `/mnt/c/code/ytdown/tests/test_callback_download_handlers.py`

- [ ] **Step 13.1: Failing test**

Append to `tests/test_callback_download_handlers.py`:

```python
def test_download_file_registers_and_unregisters_job(tmp_path, monkeypatch):
    import asyncio
    from bot.handlers import download_callbacks
    from bot.jobs import JobRegistry
    from bot.session_store import session_store

    session_store.reset()
    test_registry = JobRegistry()
    monkeypatch.setattr(download_callbacks, "job_registry", test_registry)

    seen_during = {}

    async def fake_status(*args, **kwargs):
        seen_during["count"] = len(test_registry.list_for_chat(7))

    # Patch prepare_download_plan to short-circuit by returning None.
    monkeypatch.setattr(
        download_callbacks, "prepare_download_plan", lambda **kw: None,
    )

    update = mock.MagicMock()
    update.callback_query = mock.MagicMock()
    update.callback_query.edit_message_text = fake_status
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
```

- [ ] **Step 13.2: Run, expect FAIL**

```bash
pytest tests/test_callback_download_handlers.py -v -k "download_file_registers"
```

- [ ] **Step 13.3: Modify `download_file`**

Import at top of `bot/handlers/download_callbacks.py`:

```python
from bot.jobs import JobDescriptor, job_registry
```

In `download_file`, register the job before any work and wrap entire body in `try/finally`:

Find:
```python
async def download_file(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    type,
    format,
    url,
    transcribe=False,
    summary=False,
    summary_type=None,
    use_format_id=False,
    audio_quality="192",
):
    media_type = type
    query = update.callback_query
    chat_id = update.effective_chat.id
    title = "Unknown"
    success_recorded = False
```

Replace with (everything below up to the function's existing logic continues; here we wrap):

```python
async def download_file(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    type,
    format,
    url,
    transcribe=False,
    summary=False,
    summary_type=None,
    use_format_id=False,
    audio_quality="192",
):
    media_type = type
    query = update.callback_query
    chat_id = update.effective_chat.id
    title = "Unknown"
    success_recorded = False

    descriptor = JobDescriptor(
        job_id="",
        chat_id=chat_id,
        kind="single_dl",
        label=f"Pojedynczy plik ({media_type} {format}) — pobieranie",
        started_at=datetime.now(),
    )
    cancellation = job_registry.register(chat_id, descriptor)

    try:
        # ... existing body of download_file unchanged ...
    finally:
        job_registry.unregister(cancellation.job_id)
```

The body of `download_file` is large; the change is mechanical — wrap the existing body in `try:` after the new `register` call and add `finally: job_registry.unregister(...)` at the very end.

Pass `cancellation=cancellation` to:
- `await execute_download(..., cancellation=cancellation)` — TWO call sites if any.

- [ ] **Step 13.4: Run, expect PASS**

```bash
pytest tests/test_callback_download_handlers.py -v
```

- [ ] **Step 13.5: Commit**

```bash
git add bot/handlers/download_callbacks.py tests/test_callback_download_handlers.py
git commit -m "Register single-file download_file as job and propagate cancellation"
```

---

## Task 14: Transcription pipeline + summary respect cancellation

**Files:**
- Modify: `/mnt/c/code/ytdown/bot/transcription_pipeline.py`
- Modify: `/mnt/c/code/ytdown/bot/transcription_providers.py`
- Modify: `/mnt/c/code/ytdown/bot/services/transcription_service.py`
- Modify: `/mnt/c/code/ytdown/tests/test_transcription.py`

- [ ] **Step 14.1: Failing test**

Append to `tests/test_transcription.py` (or whichever module covers `transcribe_mp3_file`):

```python
def test_transcribe_mp3_file_breaks_on_cancel(tmp_path, monkeypatch):
    """When cancellation event is set between chunks, no further chunks process."""

    import asyncio
    from bot import transcription_pipeline
    from bot.jobs import JobCancellation

    cancellation = JobCancellation(job_id="t", event=asyncio.Event())
    cancellation.event.set()

    monkeypatch.setattr(
        transcription_pipeline, "split_mp3", lambda *a, **kw: [tmp_path / "p1.mp3"]
    )
    monkeypatch.setattr(
        transcription_pipeline, "transcribe_audio",
        lambda *a, **kw: pytest.fail("must not call API after cancel"),
    )

    result = transcription_pipeline.transcribe_mp3_file(
        str(tmp_path / "src.mp3"),
        api_key="x",
        output_dir=str(tmp_path),
        cancellation=cancellation,
    )

    assert result is None or "anulowano" in str(result).lower()
```

- [ ] **Step 14.2: Run, expect FAIL**

```bash
pytest tests/test_transcription.py -v -k "breaks_on_cancel"
```

- [ ] **Step 14.3: Modify `transcribe_mp3_file`**

In `bot/transcription_pipeline.py`, add `cancellation` to the signature of `transcribe_mp3_file` and check the event at the top of the loop:

```python
def transcribe_mp3_file(
    file_path,
    api_key,
    output_dir=None,
    language=None,
    prompt=None,
    *,
    cancellation: "JobCancellation | None" = None,
):
    """... existing docstring ..."""

    # ... existing setup ...

    for index, part_path in enumerate(part_files):
        if cancellation is not None and cancellation.event.is_set():
            logging.info("Transcription cancelled before part %s", index + 1)
            return None
        # ... existing per-part logic ...
```

In `bot/services/transcription_service.py`, the wrapper `run_transcription_with_progress` accepts `cancellation` and forwards it:

```python
async def run_transcription_with_progress(
    *,
    source_path: str,
    output_dir: str,
    executor: Any,
    status_callback: Callable[[str], Any],
    cancellation: "JobCancellation | None" = None,
) -> str | None:
    """... existing docstring ..."""

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        executor,
        lambda: transcribe_mp3_file(
            source_path,
            api_key=...,        # existing
            output_dir=output_dir,
            cancellation=cancellation,
        ),
    )
```

In `bot/transcription_providers.py`, `generate_summary` is sync (HTTP call to Anthropic). Wrap call site in `download_callbacks` so it can be cancelled at task level — Task 13 already wraps `download_file` in registration; we just pass `cancellation` through `generate_summary_artifact` like:

```python
async def generate_summary_artifact(
    *,
    transcript_text: str,
    summary_type: str,
    title: str,
    sanitized_title: str,
    output_dir: str,
    executor: Any,
    cancellation: "JobCancellation | None" = None,
):
    """... existing ..."""

    if cancellation is not None and cancellation.event.is_set():
        return None

    loop = asyncio.get_event_loop()
    fut = loop.run_in_executor(executor, _do_summary, ...)
    if cancellation is not None:
        cancellation.pyrogram_task = asyncio.ensure_future(fut)
        try:
            return await cancellation.pyrogram_task
        except asyncio.CancelledError:
            return None
    return await fut
```

(Use `pyrogram_task` slot generically — it's a `asyncio.Task` reference.)

- [ ] **Step 14.4: Run, expect PASS**

```bash
pytest tests/test_transcription.py -v
```

- [ ] **Step 14.5: Commit**

```bash
git add bot/transcription_pipeline.py bot/transcription_providers.py bot/services/transcription_service.py tests/test_transcription.py
git commit -m "Propagate cancellation through transcription pipeline and summary"
```

---

## Task 15: MTProto upload tasks attach to `cancellation.pyrogram_task`

**Files:**
- Modify: `/mnt/c/code/ytdown/bot/mtproto.py`
- Modify: `/mnt/c/code/ytdown/tests/test_mtproto.py`

- [ ] **Step 15.1: Failing test**

Append to `tests/test_mtproto.py`:

```python
def test_send_video_mtproto_attaches_task_to_cancellation(tmp_path, monkeypatch):
    import asyncio
    from bot import mtproto
    from bot.jobs import JobCancellation

    src = tmp_path / "vid.mp4"
    src.write_bytes(b"x" * 1024)

    values = {
        "TELEGRAM_API_ID": "12345",
        "TELEGRAM_API_HASH": "abc",
        "TELEGRAM_BOT_TOKEN": "token",
    }
    monkeypatch.setattr(
        mtproto, "get_runtime_value",
        lambda key, default="": values.get(key, default),
    )

    captured = {}

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False
        async def send_video(self, **kwargs):
            # Snapshot cancellation.pyrogram_task during upload.
            captured["task"] = cancellation.pyrogram_task

    monkeypatch.setattr(mtproto, "_build_client", lambda *a, **kw: FakeClient())

    cancellation = JobCancellation(job_id="t", event=asyncio.Event())

    asyncio.run(
        mtproto.send_video_mtproto(
            chat_id=42, file_path=str(src), caption="x",
            cancellation=cancellation,
        )
    )

    assert captured["task"] is not None
    assert isinstance(captured["task"], asyncio.Task)
```

- [ ] **Step 15.2: Run, expect FAIL**

```bash
pytest tests/test_mtproto.py -v -k "attaches_task"
```

- [ ] **Step 15.3: Modify `send_video_mtproto` (and analogously `send_audio_mtproto`, `send_document_mtproto`)**

In `bot/mtproto.py`, change each `send_*_mtproto` to accept `cancellation` and wrap the `await client.send_*` in `asyncio.ensure_future` attached to cancellation:

```python
async def send_video_mtproto(
    chat_id: int,
    file_path: str,
    caption: str | None = None,
    thumb_path: str | None = None,
    *,
    cancellation: "JobCancellation | None" = None,
) -> bool:
    """Send a video file via MTProto (up to 2 GB).

    When cancellation is provided, the underlying upload task is attached
    so /stop can cancel it.
    """

    try:
        from pyrogram import Client  # noqa: F401
    except ImportError:
        logging.error("pyrogram not installed — cannot send large video")
        return False

    api_id = get_runtime_value("TELEGRAM_API_ID", "")
    api_hash = get_runtime_value("TELEGRAM_API_HASH", "")
    if not api_id or not api_hash:
        logging.error("TELEGRAM_API_ID/TELEGRAM_API_HASH not configured")
        return False

    api_id_int = _parse_api_id(api_id)
    if api_id_int is None:
        return False

    client = _build_client(chat_id, "send_video", api_id_int, api_hash)

    try:
        async with client:
            coro = client.send_video(
                chat_id=chat_id,
                video=file_path,
                caption=caption,
                thumb=thumb_path,
            )
            if cancellation is not None:
                task = asyncio.ensure_future(coro)
                cancellation.pyrogram_task = task
                try:
                    await task
                except asyncio.CancelledError:
                    return False
            else:
                await coro
            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            logging.info(
                "MTProto send_video OK: %s (%.1f MB) to chat %d",
                os.path.basename(file_path), file_size_mb, chat_id,
            )
            return True
    except Exception as e:
        logging.error("MTProto send_video failed: %s", e)
        return False
```

Repeat the same wrapper in `send_audio_mtproto` and `send_document_mtproto`.

- [ ] **Step 15.4: Run, expect PASS**

```bash
pytest tests/test_mtproto.py -v
```

- [ ] **Step 15.5: Commit**

```bash
git add bot/mtproto.py tests/test_mtproto.py
git commit -m "Attach pyrogram upload task to cancellation in MTProto senders"
```

---

## Task 16: Komenda `/stop` + callbacks

**Files:**
- Modify: `/mnt/c/code/ytdown/bot/telegram_commands.py`
- Modify: `/mnt/c/code/ytdown/bot/telegram_callbacks.py`
- Modify: `/mnt/c/code/ytdown/tests/test_telegram_commands.py`

- [ ] **Step 16.1: Failing tests**

Append to `tests/test_telegram_commands.py`:

```python
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
    from bot import telegram_callbacks
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
```

- [ ] **Step 16.2: Run, expect FAIL**

```bash
pytest tests/test_telegram_commands.py -v -k "stop"
```

- [ ] **Step 16.3: Add `/stop` to `bot/telegram_commands.py`**

Add imports:

```python
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from bot.jobs import job_registry
```

Add command + callback handler:

```python
async def stop_command(update, context) -> None:
    """List running jobs in this chat with cancel buttons."""

    chat_id = update.effective_chat.id
    descriptors = job_registry.list_for_chat(chat_id)

    if not descriptors:
        await update.effective_message.reply_text("Brak aktywnych operacji.")
        return

    lines = [f"Aktywne operacje ({len(descriptors)}):", ""]
    keyboard_rows: list[list[InlineKeyboardButton]] = []
    for idx, descriptor in enumerate(descriptors, 1):
        age_min = max(
            0,
            int((datetime.now() - descriptor.started_at).total_seconds() // 60),
        )
        lines.append(f"{idx}. {descriptor.label} ({age_min} min)")
        keyboard_rows.append([InlineKeyboardButton(
            f"Zatrzymaj {idx}", callback_data=f"stop_{descriptor.job_id}",
        )])
    keyboard_rows.append([InlineKeyboardButton(
        "Zatrzymaj wszystkie", callback_data="stop_all",
    )])
    keyboard_rows.append([
        InlineKeyboardButton("Odśwież", callback_data="stop_refresh"),
        InlineKeyboardButton("Anuluj listę", callback_data="stop_dismiss"),
    ])

    await update.effective_message.reply_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(keyboard_rows),
    )


async def handle_stop_callback(update, context, data: str) -> None:
    """Dispatch stop_<id>, stop_all, stop_refresh, stop_dismiss."""

    chat_id = update.effective_chat.id

    if data == "stop_all":
        descriptors = job_registry.list_for_chat(chat_id)
        cancelled = 0
        for descriptor in descriptors:
            if await job_registry.cancel_async(descriptor.job_id, "user via /stop"):
                cancelled += 1
        try:
            await update.callback_query.edit_message_text(
                f"Wysłano sygnał zatrzymania do {cancelled} operacji."
            )
        except Exception:
            pass
        return

    if data == "stop_refresh":
        # Re-render the list message with current state.
        descriptors = job_registry.list_for_chat(chat_id)
        if not descriptors:
            try:
                await update.callback_query.edit_message_text("Brak aktywnych operacji.")
            except Exception:
                pass
            return
        # Reuse stop_command rendering by editing instead of reply.
        lines = [f"Aktywne operacje ({len(descriptors)}):", ""]
        keyboard_rows: list[list[InlineKeyboardButton]] = []
        for idx, descriptor in enumerate(descriptors, 1):
            age_min = max(
                0,
                int((datetime.now() - descriptor.started_at).total_seconds() // 60),
            )
            lines.append(f"{idx}. {descriptor.label} ({age_min} min)")
            keyboard_rows.append([InlineKeyboardButton(
                f"Zatrzymaj {idx}", callback_data=f"stop_{descriptor.job_id}",
            )])
        keyboard_rows.append([InlineKeyboardButton(
            "Zatrzymaj wszystkie", callback_data="stop_all",
        )])
        keyboard_rows.append([
            InlineKeyboardButton("Odśwież", callback_data="stop_refresh"),
            InlineKeyboardButton("Anuluj listę", callback_data="stop_dismiss"),
        ])
        try:
            await update.callback_query.edit_message_text(
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup(keyboard_rows),
            )
        except Exception:
            pass
        return

    if data == "stop_dismiss":
        try:
            await update.callback_query.edit_message_text("Lista zamknięta.")
        except Exception:
            pass
        return

    if data.startswith("stop_"):
        job_id = data[len("stop_"):]
        ok = await job_registry.cancel_async(job_id, reason="user via /stop")
        text = (
            "Wysłano sygnał zatrzymania. Czekam na potwierdzenie..."
            if ok else "Operacja już zakończona."
        )
        try:
            await update.callback_query.edit_message_text(text)
        except Exception:
            pass
```

Register the `/stop` command handler in the bot bootstrap. Find the function that wires `application.add_handler(CommandHandler(...))` (in `bot/telegram_commands.py` or wherever bootstrap lives) and add:

```python
application.add_handler(CommandHandler("stop", stop_command))
```

In `bot/telegram_callbacks.py`, in the main router (after `arc_*`), add:

```python
    if data.startswith("stop_") or data == "stop_all":
        from bot.telegram_commands import handle_stop_callback
        await handle_stop_callback(update, context, data)
        return
```

- [ ] **Step 16.4: Run, expect PASS**

```bash
pytest tests/test_telegram_commands.py tests/test_telegram_integration.py -v
```

- [ ] **Step 16.5: Commit**

```bash
git add bot/telegram_commands.py bot/telegram_callbacks.py tests/test_telegram_commands.py
git commit -m "Add /stop command and stop_* callbacks"
```

---

## Task 17: `cleanup.py` purges dead jobs and partial workspaces

**Files:**
- Modify: `/mnt/c/code/ytdown/bot/cleanup.py`
- Modify: `/mnt/c/code/ytdown/tests/test_cleanup.py`

- [ ] **Step 17.1: Failing tests**

Append to `tests/test_cleanup.py`:

```python
def test_purge_dead_jobs_called_in_periodic_cleanup(monkeypatch):
    """periodic_cleanup invokes job_registry.purge_dead with 6h threshold."""

    from datetime import timedelta
    from bot import cleanup
    from bot.jobs import JobRegistry

    test_registry = JobRegistry()
    monkeypatch.setattr(cleanup, "job_registry", test_registry)
    captured = {}

    def fake_purge(threshold):
        captured["threshold"] = threshold
        return 0

    monkeypatch.setattr(test_registry, "purge_dead", fake_purge)

    # Drive one iteration of the loop body without sleeping for an hour.
    cleanup._purge_dead_jobs(retention_hours=6)

    assert captured["threshold"] == timedelta(hours=6)


def test_purge_partial_archive_workspaces_removes_old(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    from pathlib import Path
    from bot import cleanup
    from bot.session_store import (
        ArchivePartialState,
        partial_archive_workspaces,
        session_store,
    )

    session_store.reset()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    state = ArchivePartialState(
        workspace=workspace, downloaded=[], title="x",
        media_type="audio", format_choice="mp3", use_mtproto=False,
        created_at=datetime.now() - timedelta(hours=2),
    )
    partial_archive_workspaces[1] = {"old": state}

    cleanup._purge_partial_archive_workspaces(retention_min=60)

    assert partial_archive_workspaces.get(1, {}).get("old") is None
    session_store.reset()
```

- [ ] **Step 17.2: Run, expect FAIL**

```bash
pytest tests/test_cleanup.py -v -k "purge"
```

- [ ] **Step 17.3: Add to `bot/cleanup.py`**

Add imports near the top:

```python
from bot.jobs import job_registry
from bot.security_limits import JOB_DEAD_AGE_HOURS, PLAYLIST_ARCHIVE_RETENTION_MIN
```

Add helper functions (place near the existing `_purge_archive_workspaces`):

```python
def _purge_dead_jobs(retention_hours: int) -> int:
    """Remove zombie entries from JobRegistry. Logs each as warning."""

    return job_registry.purge_dead(timedelta(hours=retention_hours))


def _purge_partial_archive_workspaces(retention_min: int) -> int:
    """Drop partial_archive_workspaces entries older than retention_min."""

    from bot.session_store import partial_archive_workspaces

    cutoff = datetime.now() - timedelta(minutes=retention_min)
    removed = 0
    for chat_id in list(partial_archive_workspaces):
        bucket = partial_archive_workspaces.get(chat_id) or {}
        for token in list(bucket):
            state = bucket[token]
            if state.created_at >= cutoff:
                continue
            bucket.pop(token, None)
            removed += 1
        if not bucket:
            partial_archive_workspaces.pop(chat_id, None)
        else:
            partial_archive_workspaces[chat_id] = bucket
    return removed
```

In `periodic_cleanup`, after the existing `_purge_archived_deliveries(...)` call, add:

```python
            _purge_dead_jobs(JOB_DEAD_AGE_HOURS)
            _purge_partial_archive_workspaces(PLAYLIST_ARCHIVE_RETENTION_MIN)
```

- [ ] **Step 17.4: Run, expect PASS**

```bash
pytest tests/test_cleanup.py -v
```

- [ ] **Step 17.5: Commit**

```bash
git add bot/cleanup.py tests/test_cleanup.py
git commit -m "Periodically purge dead jobs and partial archive workspaces"
```

---

## Task 18: Manual E2E checklist

Ten task nie ma testów automatycznych — ręczna lista kontrolna do wykonania przed PR.

- [ ] **Step 18.1: Środowisko + start bota**

```bash
source /home/pi/venv/bin/activate
cd /mnt/c/code/ytdown
python main.py
```

Sprawdź w logach `archive_available=True` i brak crash przy starcie.

- [ ] **Step 18.2: `/stop` przy braku operacji**

Wyślij `/stop` w czacie → odpowiedź `"Brak aktywnych operacji."`.

- [ ] **Step 18.3: Cancel playlisty 7z w trakcie pobierania**

Wyślij URL playlisty 30 elementów → klik `Pobierz wszystkie — Audio MP3 jako 7z` → po 5 elementach `/stop` → `[Zatrzymaj 1]`. Oczekiwane:
- Status edytuje się na `"⏹ Zatrzymano. Pobrano 5/30..."` z przyciskami `[Spakuj co mam]` i `[Usuń teraz]`.
- Klik `[Spakuj co mam]` pakuje 5 plików w 7z i wysyła paczki.

- [ ] **Step 18.4: Cancel pakowania 7z**

Playlist z plikami sumarycznie ~2 GB, klik 7z. Czekaj aż status zmieni się na `"Pakowanie..."`. `/stop` → `[Zatrzymaj 1]`. Oczekiwane:
- Status `"⏹ Zatrzymano w trakcie pakowania."`.
- Brak plików `.7z.001` w `downloads/<chat_id>/pl_*/`.
- Source files (z fazy pobierania) zachowane do retencji 60 min.

- [ ] **Step 18.5: Cancel wysyłki wolumenów**

Playlist która produkuje 8 wolumenów MTProto. `/stop` po wysłaniu 3 → klik. Oczekiwane:
- 3 wolumeny w czacie pozostają.
- Status z przyciskiem `[Wznów od 4]` (wykorzystuje istniejący `arc_resend_<token>_3`).

- [ ] **Step 18.6: Cancel transkrypcji**

Wyślij audio 30+ min, klik `Transkrybuj`. Czekaj na pierwszą iterację. `/stop` → `[Zatrzymaj 1]`. Oczekiwane:
- Po dokończeniu bieżącego chunku (~30 s) bot edytuje status na `"⏹ Zatrzymano transkrypcję."`.

- [ ] **Step 18.7: Cancel summary**

Wyślij audio z transkrypcją + summary, klik. Po ukończeniu transkrypcji, gdy status pokazuje `"Generuję podsumowanie..."` → `/stop` → klik. Oczekiwane:
- Status `"⏹ Zatrzymano podsumowanie. Transkrypcja jest dostępna."`.

- [ ] **Step 18.8: `/stop` z dwoma równoległymi jobami**

Puść playlistę i drugi single-file. `/stop` listuje oba. `[Zatrzymaj wszystkie]` → oba zatrzymane.

- [ ] **Step 18.9: Zombie cleanup**

Żaden test sieciowy — tylko sprawdzenie logiki. Manualnie wywołaj w shell-u Python:
```python
from bot.jobs import job_registry, JobDescriptor
from datetime import datetime, timedelta
descriptor = JobDescriptor(job_id="", chat_id=0, kind="single_dl",
                            label="z", started_at=datetime.now() - timedelta(hours=7))
job_registry.register(0, descriptor)
from bot.cleanup import _purge_dead_jobs
removed = _purge_dead_jobs(retention_hours=6)
print(removed)  # expected: 1
```

- [ ] **Step 18.10: Pull request**

Po wszystkich krokach pozytywnych:

```bash
git push origin develop
gh pr create --base main --head develop --title "Cancel command (/stop) for long-running operations" --body "..."
```

---

## Self-review (autora planu)

1. **Spec coverage:**
   - Sekcja 2 (decyzje produktowe) — Tasks 1, 2, 3, 16.
   - Sekcja 3 (architektura) — Tasks 2, 3, 4, 5.
   - Sekcja 4 (flow) — Tasks 10, 11, 16.
   - Sekcja 5 (konfiguracja, error handling) — Tasks 1, 6, 17.
   - Sekcja 6 (testy) — testy w każdym z tasków.
   - Sekcja 7 (poza scope) — niezaadresowane (zgodnie z założeniem).

2. **Placeholder scan:** Brak "TBD"/"TODO". Komentarz w Task 13.3 mówi "everything below up to the function's existing logic continues; here we wrap" — to instrukcja dla wykonawcy, nie placeholder. Zostawione celowo.

3. **Type consistency:** `JobCancellation`, `JobDescriptor`, `JobRegistry`, `ArchivePartialState`, `partial_archive_workspaces`, `job_registry`, kind values (`"playlist_legacy"`, `"playlist_zip"`, `"single_dl"`, `"transcription"`, `"summary"`, `"archive_pack"`, `"archive_send"`) używane spójnie.

4. **Kolejność:** Task 2/3/4 (jobs.py) przed 6/7/8/9/10/11 (konsumenty). Task 5 (session_store) przed 10/11 (gdzie `ArchivePartialState` jest używane). Task 16 (`/stop` handler) po wszystkich konsumentach. Task 17 (cleanup) po jobs i session_store. Spójne.

---

## Wybór trybu wykonania

Plan kompletny, zapisany w `docs/superpowers/plans/2026-05-03-cancel-operations-plan.md`. Dwie opcje wykonania:

1. **Subagent-driven (rekomendowane)** — dispatcher świeżego subagenta per task, dwustopniowy review, szybka iteracja. Wymagany sub-skill: `superpowers:subagent-driven-development`.

2. **Inline execution** — wykonujemy taski w obecnej sesji z checkpointami. Wymagany sub-skill: `superpowers:executing-plans`.

Który tryb wybierasz?
