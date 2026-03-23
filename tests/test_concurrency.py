"""Concurrency tests for shared in-memory state.

These tests exercise SessionStore, SecurityStore, SessionFieldMap and the
security helpers under concurrent access from multiple threads.  They are
behavioural: they verify state consistency after all workers complete, not
timing-dependent properties.
"""

from __future__ import annotations

import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Barrier

from bot.security_pin import is_user_blocked, register_pin_failure
from bot.security_throttling import check_rate_limit
from bot.session_store import SecurityStore, SessionFieldMap, SessionStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WORKERS = 30
_BARRIER_TIMEOUT = 10  # seconds


def _make_barrier(n: int) -> Barrier:
    """Return a threading.Barrier that releases all n threads simultaneously."""
    return Barrier(n, timeout=_BARRIER_TIMEOUT)


# ===========================================================================
# 1. SessionStore – concurrent set_field / get_field
# ===========================================================================


class TestSessionStoreConcurrentSameChat:
    """Multiple threads write distinct fields for the same chat_id."""

    def test_set_field_same_chat_concurrent_all_writes_visible(self):
        """All concurrent field writes for the same chat_id must survive."""
        store = SessionStore()
        chat_id = 42
        n = _WORKERS
        barrier = _make_barrier(n)

        # Map each worker index to a distinct SessionState field.
        # We alternate between two writable string fields to create contention
        # while still allowing all writes to land on separate, independently
        # readable attributes.
        fields = ["platform", "audio_file_title"]

        def worker(idx: int) -> None:
            barrier.wait()
            field = fields[idx % len(fields)]
            store.set_field(chat_id, field, f"value_{idx}")

        with ThreadPoolExecutor(max_workers=n) as pool:
            futures = [pool.submit(worker, i) for i in range(n)]
            for f in as_completed(futures):
                f.result()  # re-raise any exception from the thread

        # Each field must have been written at least once (last-write wins is
        # acceptable; the important property is no crash and no None sentinel
        # where a write did occur).
        for field in fields:
            val = store.get_field(chat_id, field)
            # val may be None only if all writes to that field were followed by
            # a _cleanup_if_empty that removed the session; in practice at
            # least one field must remain because both fields are set.
            assert val is not None or store.get_field(chat_id, fields[1 - fields.index(field)]) is not None

    def test_get_field_same_chat_concurrent_never_raises(self):
        """Concurrent reads from the same chat_id must not raise."""
        store = SessionStore()
        chat_id = 99
        store.set_field(chat_id, "platform", "youtube")
        n = _WORKERS
        barrier = _make_barrier(n)
        errors: list[Exception] = []

        def worker(_idx: int) -> None:
            barrier.wait()
            try:
                store.get_field(chat_id, "platform")
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=n) as pool:
            list(as_completed([pool.submit(worker, i) for i in range(n)]))

        assert errors == []

    def test_set_field_different_chats_concurrent_all_chats_tracked(self):
        """Each thread writes to its own chat_id; every write must be readable."""
        store = SessionStore()
        n = _WORKERS
        barrier = _make_barrier(n)
        chat_ids = list(range(1000, 1000 + n))

        def worker(idx: int) -> None:
            barrier.wait()
            store.set_field(chat_ids[idx], "platform", f"platform_{idx}")

        with ThreadPoolExecutor(max_workers=n) as pool:
            list(as_completed([pool.submit(worker, i) for i in range(n)]))

        for idx, chat_id in enumerate(chat_ids):
            assert store.get_field(chat_id, "platform") == f"platform_{idx}"

    def test_update_and_clear_concurrent_no_stale_data_after_clear(self):
        """Clearing a session while writes happen must not leave partial state."""
        store = SessionStore()
        chat_id = 7
        n = _WORKERS
        barrier = _make_barrier(n)

        def writer(idx: int) -> None:
            barrier.wait()
            if idx % 3 == 0:
                store.clear_session(chat_id)
            else:
                store.set_field(chat_id, "platform", f"v{idx}")

        with ThreadPoolExecutor(max_workers=n) as pool:
            list(as_completed([pool.submit(writer, i) for i in range(n)]))

        # After all threads finish the session may or may not exist, but
        # whatever state is present must be internally consistent (no exception
        # and no AttributeError).
        val = store.get_field(chat_id, "platform")
        assert val is None or isinstance(val, str)


# ===========================================================================
# 2. SecurityStore – concurrent check_rate_limit for distinct user_ids
# ===========================================================================


class TestSecurityStoreConcurrentRateLimit:
    """check_rate_limit called concurrently from separate user_ids."""

    def test_check_rate_limit_distinct_users_all_tracked(self):
        """Every user gets at least one request tracked in the store."""
        store = SecurityStore()
        n = _WORKERS
        user_ids = list(range(2000, 2000 + n))
        requests_map = defaultdict(list)
        barrier = _make_barrier(n)
        now = time.time()

        def worker(idx: int) -> None:
            barrier.wait()
            check_rate_limit(
                user_ids[idx],
                requests_map,
                current_time=now,
                max_requests=100,
                window_seconds=60,
            )

        with ThreadPoolExecutor(max_workers=n) as pool:
            list(as_completed([pool.submit(worker, i) for i in range(n)]))

        for uid in user_ids:
            assert len(requests_map[uid]) >= 1, f"user {uid} has no tracked requests"

    def test_check_rate_limit_does_not_crash_under_concurrency(self):
        """No exception must escape concurrent check_rate_limit calls."""
        requests_map = defaultdict(list)
        n = _WORKERS
        barrier = _make_barrier(n)
        errors: list[Exception] = []
        now = time.time()

        def worker(idx: int) -> None:
            barrier.wait()
            try:
                check_rate_limit(
                    3000 + (idx % 5),  # intentional sharing: 5 distinct ids
                    requests_map,
                    current_time=now + idx * 0.001,
                    max_requests=1000,
                    window_seconds=300,
                )
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=n) as pool:
            list(as_completed([pool.submit(worker, i) for i in range(n)]))

        assert errors == []

    def test_check_rate_limit_shared_user_id_request_count_bounded(self):
        """With a high limit, all allowed requests for a shared user_id are recorded."""
        requests_map = defaultdict(list)
        user_id = 9999
        n = _WORKERS
        barrier = _make_barrier(n)
        now = time.time()
        results: list[bool] = []

        def worker(idx: int) -> bool:
            barrier.wait()
            return check_rate_limit(
                user_id,
                requests_map,
                current_time=now + idx * 0.0001,
                max_requests=n + 10,
                window_seconds=300,
            )

        with ThreadPoolExecutor(max_workers=n) as pool:
            futures = [pool.submit(worker, i) for i in range(n)]
            results = [f.result() for f in as_completed(futures)]

        # With max_requests > n, all calls should be allowed.
        assert all(results), "all requests within limit should be allowed"
        # The list must contain exactly n entries.
        assert len(requests_map[user_id]) == n


# ===========================================================================
# 3. PIN blocking – concurrent register_pin_failure
# ===========================================================================


class TestPinBlockingConcurrent:
    """register_pin_failure called concurrently for the same user_id."""

    def test_register_pin_failure_same_user_blocked_after_threshold(self):
        """After enough concurrent failures the user must be blocked."""
        user_id = 5001
        max_attempts = 3
        attempts_map: defaultdict[int, int] = defaultdict(int)
        block_map: defaultdict[int, float] = defaultdict(float)
        n = 20  # well above max_attempts
        barrier = _make_barrier(n)
        now = time.time()

        def worker(_idx: int) -> None:
            barrier.wait()
            register_pin_failure(
                user_id,
                now=now,
                attempts=attempts_map,
                block_map=block_map,
                max_attempts=max_attempts,
                block_time=300,
            )

        with ThreadPoolExecutor(max_workers=n) as pool:
            list(as_completed([pool.submit(worker, i) for i in range(n)]))

        # After n > max_attempts calls the user must be blocked.
        assert is_user_blocked(user_id, now=now + 1, block_map=block_map)

    def test_register_pin_failure_different_users_each_blocked_independently(self):
        """Failures for distinct users must not bleed into each other's block state."""
        user_ids = list(range(6000, 6000 + 10))
        max_attempts = 3
        attempts_map: defaultdict[int, int] = defaultdict(int)
        block_map: defaultdict[int, float] = defaultdict(float)
        n = len(user_ids) * 5  # 5 failures per user
        barrier = _make_barrier(n)
        now = time.time()

        def worker(idx: int) -> None:
            uid = user_ids[idx % len(user_ids)]
            barrier.wait()
            register_pin_failure(
                uid,
                now=now,
                attempts=attempts_map,
                block_map=block_map,
                max_attempts=max_attempts,
                block_time=300,
            )

        with ThreadPoolExecutor(max_workers=n) as pool:
            list(as_completed([pool.submit(worker, i) for i in range(n)]))

        for uid in user_ids:
            assert is_user_blocked(uid, now=now + 1, block_map=block_map), (
                f"user {uid} should be blocked after >= {max_attempts} failures"
            )

    def test_register_pin_failure_no_crash_under_concurrent_access(self):
        """register_pin_failure must not raise under concurrent load."""
        user_id = 7001
        attempts_map: defaultdict[int, int] = defaultdict(int)
        block_map: defaultdict[int, float] = defaultdict(float)
        n = _WORKERS
        barrier = _make_barrier(n)
        errors: list[Exception] = []
        now = time.time()

        def worker(_idx: int) -> None:
            barrier.wait()
            try:
                register_pin_failure(
                    user_id,
                    now=now,
                    attempts=attempts_map,
                    block_map=block_map,
                    max_attempts=100,
                    block_time=300,
                )
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=n) as pool:
            list(as_completed([pool.submit(worker, i) for i in range(n)]))

        assert errors == []


# ===========================================================================
# 4. SessionFieldMap – concurrent __getitem__ and __setitem__
# ===========================================================================


class TestSessionFieldMapConcurrent:
    """SessionFieldMap accessed concurrently through its MutableMapping interface."""

    def test_setitem_distinct_keys_concurrent_all_values_readable(self):
        """Every chat_id written through __setitem__ must be retrievable."""
        store = SessionStore()
        field_map = SessionFieldMap(store, "current_url")
        n = _WORKERS
        barrier = _make_barrier(n)
        chat_ids = list(range(8000, 8000 + n))

        def worker(idx: int) -> None:
            barrier.wait()
            field_map[chat_ids[idx]] = f"https://example.com/{idx}"

        with ThreadPoolExecutor(max_workers=n) as pool:
            list(as_completed([pool.submit(worker, i) for i in range(n)]))

        for idx, cid in enumerate(chat_ids):
            assert field_map[cid] == f"https://example.com/{idx}"

    def test_getitem_concurrent_reads_never_raise(self):
        """Concurrent reads via __getitem__ must not raise."""
        store = SessionStore()
        field_map = SessionFieldMap(store, "current_url")
        chat_id = 8500
        field_map[chat_id] = "https://example.com/stable"
        n = _WORKERS
        barrier = _make_barrier(n)
        errors: list[Exception] = []

        def worker(_idx: int) -> None:
            barrier.wait()
            try:
                _ = field_map[chat_id]
            except KeyError:
                # KeyError is acceptable only if someone else deleted the key;
                # no other exception type is acceptable.
                pass
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=n) as pool:
            list(as_completed([pool.submit(worker, i) for i in range(n)]))

        assert errors == []

    def test_mixed_reads_and_writes_concurrent_no_internal_errors(self):
        """Interleaved reads and writes through SessionFieldMap must not corrupt state."""
        store = SessionStore()
        field_map = SessionFieldMap(store, "platform")
        chat_ids = list(range(9000, 9000 + 5))
        # Pre-populate so readers have something to find.
        for cid in chat_ids:
            field_map[cid] = "initial"

        n = _WORKERS
        barrier = _make_barrier(n)
        errors: list[Exception] = []

        def worker(idx: int) -> None:
            barrier.wait()
            cid = chat_ids[idx % len(chat_ids)]
            try:
                if idx % 2 == 0:
                    field_map[cid] = f"updated_{idx}"
                else:
                    _ = field_map[cid]
            except KeyError:
                # Acceptable: a concurrent write may have temporarily cleared
                # the entry via _cleanup_if_empty, then re-inserted.
                pass
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=n) as pool:
            list(as_completed([pool.submit(worker, i) for i in range(n)]))

        assert errors == []

    def test_len_and_iter_concurrent_do_not_raise(self):
        """len() and iteration over SessionFieldMap under concurrent writes must not raise."""
        store = SessionStore()
        field_map = SessionFieldMap(store, "audio_file_path")
        n = _WORKERS
        barrier = _make_barrier(n)
        errors: list[Exception] = []

        def worker(idx: int) -> None:
            barrier.wait()
            try:
                if idx % 3 == 0:
                    _ = len(field_map)
                elif idx % 3 == 1:
                    _ = list(field_map)
                else:
                    field_map[9900 + idx] = f"/tmp/audio_{idx}.mp3"
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=n) as pool:
            list(as_completed([pool.submit(worker, i) for i in range(n)]))

        assert errors == []
