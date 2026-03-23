"""Tests for bot.security_authorization.manage_authorized_user."""

from __future__ import annotations

import threading
import time

import pytest

import bot.security_authorization as auth_mod
from bot.security_authorization import manage_authorized_user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stubs(initial: set | None = None):
    """Return (store, add_stub, remove_stub) for monkeypatching."""
    store = set(initial or [])

    def add_stub(user_id):
        if user_id in store:
            return False
        store.add(user_id)
        return True

    def remove_stub(user_id):
        if user_id not in store:
            return False
        store.discard(user_id)
        return True

    return store, add_stub, remove_stub


# ---------------------------------------------------------------------------
# Basic add / remove behaviour
# ---------------------------------------------------------------------------

def test_add_new_user_returns_true(monkeypatch):
    store, add_stub, remove_stub = _make_stubs()
    monkeypatch.setattr(auth_mod, "add_runtime_authorized_user", add_stub)
    monkeypatch.setattr(auth_mod, "remove_runtime_authorized_user", remove_stub)

    result = manage_authorized_user(1001, "add")

    assert result is True
    assert 1001 in store


def test_add_user_persists_in_store(monkeypatch):
    store, add_stub, remove_stub = _make_stubs()
    monkeypatch.setattr(auth_mod, "add_runtime_authorized_user", add_stub)
    monkeypatch.setattr(auth_mod, "remove_runtime_authorized_user", remove_stub)

    manage_authorized_user(2001, "add")

    assert 2001 in store


def test_remove_existing_user_returns_true(monkeypatch):
    store, add_stub, remove_stub = _make_stubs(initial={3001})
    monkeypatch.setattr(auth_mod, "add_runtime_authorized_user", add_stub)
    monkeypatch.setattr(auth_mod, "remove_runtime_authorized_user", remove_stub)

    result = manage_authorized_user(3001, "remove")

    assert result is True
    assert 3001 not in store


def test_remove_user_absent_from_store(monkeypatch):
    store, add_stub, remove_stub = _make_stubs()
    monkeypatch.setattr(auth_mod, "add_runtime_authorized_user", add_stub)
    monkeypatch.setattr(auth_mod, "remove_runtime_authorized_user", remove_stub)

    result = manage_authorized_user(3001, "remove")

    # Function still returns True even when user was not present
    assert result is True
    assert 3001 not in store


def test_unknown_action_returns_false(monkeypatch):
    store, add_stub, remove_stub = _make_stubs()
    monkeypatch.setattr(auth_mod, "add_runtime_authorized_user", add_stub)
    monkeypatch.setattr(auth_mod, "remove_runtime_authorized_user", remove_stub)

    result = manage_authorized_user(9999, "grant")

    assert result is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_add_already_authorized_user_returns_true(monkeypatch):
    """Adding an already-present user is idempotent and still returns True."""
    store, add_stub, remove_stub = _make_stubs(initial={4001})
    monkeypatch.setattr(auth_mod, "add_runtime_authorized_user", add_stub)
    monkeypatch.setattr(auth_mod, "remove_runtime_authorized_user", remove_stub)

    result = manage_authorized_user(4001, "add")

    assert result is True
    # User appears exactly once (set semantics guaranteed by stub)
    assert store == {4001}


def test_add_already_authorized_user_does_not_duplicate(monkeypatch):
    """Store must not gain extra entries when the same user is added twice."""
    store, add_stub, remove_stub = _make_stubs()
    monkeypatch.setattr(auth_mod, "add_runtime_authorized_user", add_stub)
    monkeypatch.setattr(auth_mod, "remove_runtime_authorized_user", remove_stub)

    manage_authorized_user(5001, "add")
    manage_authorized_user(5001, "add")

    assert store == {5001}


def test_remove_non_existent_user_does_not_raise(monkeypatch):
    """Removing a user who was never added must not raise and must return True."""
    store, add_stub, remove_stub = _make_stubs()
    monkeypatch.setattr(auth_mod, "add_runtime_authorized_user", add_stub)
    monkeypatch.setattr(auth_mod, "remove_runtime_authorized_user", remove_stub)

    result = manage_authorized_user(6001, "remove")

    assert result is True


def test_add_then_remove_leaves_empty_store(monkeypatch):
    store, add_stub, remove_stub = _make_stubs()
    monkeypatch.setattr(auth_mod, "add_runtime_authorized_user", add_stub)
    monkeypatch.setattr(auth_mod, "remove_runtime_authorized_user", remove_stub)

    manage_authorized_user(7001, "add")
    manage_authorized_user(7001, "remove")

    assert 7001 not in store


def test_exception_in_stub_returns_false(monkeypatch):
    """If the underlying helper raises, manage_authorized_user must return False."""

    def bad_add(user_id):
        raise RuntimeError("disk full")

    monkeypatch.setattr(auth_mod, "add_runtime_authorized_user", bad_add)
    monkeypatch.setattr(auth_mod, "remove_runtime_authorized_user", lambda uid: True)

    result = manage_authorized_user(8001, "add")

    assert result is False


# ---------------------------------------------------------------------------
# Thread-safety: concurrent adds and removes must not corrupt state
# ---------------------------------------------------------------------------

def test_concurrent_adds_do_not_corrupt_store(monkeypatch):
    """Multiple threads calling add for different users all succeed."""
    store, add_stub, remove_stub = _make_stubs()
    monkeypatch.setattr(auth_mod, "add_runtime_authorized_user", add_stub)
    monkeypatch.setattr(auth_mod, "remove_runtime_authorized_user", remove_stub)

    user_ids = list(range(10_000, 10_050))
    results = []
    lock = threading.Lock()

    def add_user(uid):
        r = manage_authorized_user(uid, "add")
        with lock:
            results.append(r)

    threads = [threading.Thread(target=add_user, args=(uid,)) for uid in user_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(results), "Every add must return True"
    assert store == set(user_ids), "All users must be in the store exactly once"


def test_concurrent_removes_do_not_corrupt_store(monkeypatch):
    """Multiple threads removing distinct users all leave a consistent store."""
    user_ids = list(range(20_000, 20_050))
    store, add_stub, remove_stub = _make_stubs(initial=set(user_ids))
    monkeypatch.setattr(auth_mod, "add_runtime_authorized_user", add_stub)
    monkeypatch.setattr(auth_mod, "remove_runtime_authorized_user", remove_stub)

    results = []
    lock = threading.Lock()

    def remove_user(uid):
        r = manage_authorized_user(uid, "remove")
        with lock:
            results.append(r)

    threads = [threading.Thread(target=remove_user, args=(uid,)) for uid in user_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(results), "Every remove must return True"
    assert store == set(), "Store must be empty after removing all users"


def test_concurrent_mixed_add_remove_no_corruption(monkeypatch):
    """Interleaved add and remove calls from many threads leave a valid store."""
    store, add_stub, remove_stub = _make_stubs()
    monkeypatch.setattr(auth_mod, "add_runtime_authorized_user", add_stub)
    monkeypatch.setattr(auth_mod, "remove_runtime_authorized_user", remove_stub)

    errors = []
    lock = threading.Lock()

    def worker(uid):
        try:
            manage_authorized_user(uid, "add")
            manage_authorized_user(uid, "remove")
            manage_authorized_user(uid, "add")
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(uid,)) for uid in range(30_000, 30_030)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"No exceptions expected, got: {errors}"
    # After add/remove/add each user ends up in the store exactly once
    assert store == set(range(30_000, 30_030))
