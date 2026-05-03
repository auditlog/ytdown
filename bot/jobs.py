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
from dataclasses import dataclass
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


# Global singleton consumed by handlers/services.
job_registry = JobRegistry()
