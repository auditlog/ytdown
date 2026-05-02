"""Unit tests for bot.archive low-level helpers."""

from __future__ import annotations

from datetime import datetime
from unittest import mock

import pytest


def test_volume_size_for_mtproto_returns_mtproto_constant():
    from bot import archive
    from bot.security_limits import MTPROTO_VOLUME_SIZE_MB

    assert archive.volume_size_for(use_mtproto=True) == MTPROTO_VOLUME_SIZE_MB


def test_volume_size_for_botapi_returns_botapi_constant():
    from bot import archive
    from bot.security_limits import BOTAPI_VOLUME_SIZE_MB

    assert archive.volume_size_for(use_mtproto=False) == BOTAPI_VOLUME_SIZE_MB


def test_volume_size_for_mtproto_larger_than_botapi():
    from bot.archive import volume_size_for

    assert volume_size_for(use_mtproto=True) > volume_size_for(use_mtproto=False)


def test_transliterate_to_ascii_replaces_polish_letters():
    from bot.archive import transliterate_to_ascii

    assert transliterate_to_ascii("Pączki ąęłżźćń") == "Paczki aelzzcn"


def test_transliterate_to_ascii_preserves_safe_characters():
    from bot.archive import transliterate_to_ascii

    assert transliterate_to_ascii("Hello world - 2026!") == "Hello world - 2026!"


def test_transliterate_to_ascii_handles_uppercase():
    from bot.archive import transliterate_to_ascii

    assert transliterate_to_ascii("ŻÓŁW") == "ZOLW"


def test_compute_archive_basename_format():
    from bot.archive import compute_archive_basename

    ts = datetime(2026, 5, 2, 14, 5, 33)
    assert compute_archive_basename("playlist", ts) == "playlist_20260502-140533"


def test_is_7z_available_when_present():
    from bot import archive

    with mock.patch("bot.archive.shutil.which", return_value="/usr/bin/7z"):
        assert archive.is_7z_available() is True


def test_is_7z_available_when_absent():
    from bot import archive

    with mock.patch("bot.archive.shutil.which", return_value=None):
        assert archive.is_7z_available() is False
