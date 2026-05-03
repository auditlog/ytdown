# Plan implementacji: Tryb „pobierz playlistę / duży plik jako 7z"

> **Dla agentic worker:** WYMAGANY SUB-SKILL — `superpowers:subagent-driven-development` (rekomendowany) lub `superpowers:executing-plans`. Kroki używają składni checkbox `- [ ]`.

**Cel:** Dodać do bota Telegram (`/mnt/c/code/ytdown`) tryb pobrania całej playlisty YouTube / pojedynczego dużego pliku jako multi-volume archiwum 7z, mieszczące się w limicie wysyłki Telegrama (1900 MB z MTProto, 49 MB bez).

**Architektura:** Niskopoziomowy wrapper na `7z` CLI (`bot/archive.py`) odseparowany od warstwy Telegrama. Serwis orkiestracji (`bot/services/archive_service.py`) łączy pobieranie, pakowanie, wysyłkę wolumenów i obsługę retry/purge. UI: dodatkowe przyciski „… jako 7z" w menu playlisty + fallback `[Wyślij jako 7z] [Anuluj]` dla pojedynczych plików > limit.

**Tech Stack:** Python 3.11+, `asyncio.create_subprocess_exec`, `7z` (p7zip-full 26.00) — już zainstalowany, `pyrogram` (już używane), `python-telegram-bot`, `pytest` + `pytest-asyncio` (jeśli istnieje, inaczej `unittest.mock.AsyncMock`).

**Branża:** wszystkie taski commitujemy bezpośrednio na `develop` (zgodnie z polityką repo: nigdy direct na `main`, zawsze `develop` → PR).

**Spec referencyjny:** `docs/superpowers/specs/2026-05-02-playlist-zip-download-design.md` (commit `cc53dad`).

---

## Mapa plików

| Plik | Akcja | Odpowiedzialność |
|---|---|---|
| `bot/security_limits.py` | modify | Nowe stałe rozmiarów wolumenów + retencja. |
| `bot/archive.py` | **create** | Wrapper na `7z` CLI: helpers + `pack_to_volumes`. |
| `bot/mtproto.py` | modify | Dodać `send_document_mtproto`. |
| `bot/session_store.py` | modify | Pola `pending_archive_jobs`, `archived_deliveries` + mapy. |
| `bot/services/archive_service.py` | **create** | Orkiestracja: workspace → download → pack → send. |
| `bot/services/playlist_service.py` | modify | Parser callback + nowe przyciski. |
| `bot/handlers/playlist_callbacks.py` | modify | Dispatch dla `pl_zip_dl_*`. |
| `bot/handlers/download_callbacks.py` | modify | Dispatch dla `arc_*` + fallback po pobraniu. |
| `bot/runtime.py` | modify | Flag `archive_available` w `AppRuntime`. |
| `bot/cleanup.py` | modify | Cleanup workspace’ów `pl_*`/`big_*` i pending jobs. |
| `tests/test_archive.py` | **create** | Unit + 1 integration test dla `bot/archive.py`. |
| `tests/test_archive_service.py` | **create** | Unit dla `bot/services/archive_service.py`. |
| `tests/test_playlist_service.py` | modify | Testy parsera + nowych przycisków. |
| `tests/test_callback_download_handlers.py` | modify | Testy `arc_*` i fallback po pobraniu. |
| `tests/test_playlist.py` | modify | Test handler-level: `pl_zip_dl_*` deleguje. |
| `tests/test_cleanup.py` | modify | Testy purge workspace’ów + pending jobs. |
| `tests/test_mtproto.py` | modify | Testy `send_document_mtproto`. |

---

## Task 1: Stałe w `bot/security_limits.py`

**Files:**
- Modify: `bot/security_limits.py`
- Test: `tests/test_security_unit.py`

- [ ] **Step 1.1: Dodać failing test dla obecności i typów stałych**

Dopisz na końcu `tests/test_security_unit.py`:

```python
def test_archive_volume_size_constants_defined():
    from bot import security_limits

    assert security_limits.MTPROTO_VOLUME_SIZE_MB == 1900
    assert security_limits.BOTAPI_VOLUME_SIZE_MB == 49
    assert security_limits.BOTAPI_VOLUME_SIZE_MB < security_limits.TELEGRAM_UPLOAD_LIMIT_MB


def test_archive_item_size_limit_defined():
    from bot import security_limits

    assert security_limits.MAX_ARCHIVE_ITEM_SIZE_MB == 10240
    assert security_limits.MAX_ARCHIVE_ITEM_SIZE_MB > security_limits.MAX_FILE_SIZE_MB


def test_playlist_archive_retention_defined():
    from bot import security_limits

    assert security_limits.PLAYLIST_ARCHIVE_RETENTION_MIN == 60
```

- [ ] **Step 1.2: Uruchomić test — oczekiwany FAIL (`AttributeError`)**

```bash
source /home/pi/venv/bin/activate
pytest tests/test_security_unit.py::test_archive_volume_size_constants_defined -v
```

Oczekiwany wynik: `AttributeError: module 'bot.security_limits' has no attribute 'MTPROTO_VOLUME_SIZE_MB'`.

- [ ] **Step 1.3: Dodać stałe w `bot/security_limits.py`**

Dopisz na końcu `bot/security_limits.py`:

```python
# Maximum number of playlist items to download (default / expanded)
MAX_PLAYLIST_ITEMS = 10
MAX_PLAYLIST_ITEMS_EXPANDED = 50

# 7z archive volume size depending on transport.
# MTProto bot upload caps single message at ~2 GB; we leave 100 MB margin
# for 7z header overhead and per-message metadata.
MTPROTO_VOLUME_SIZE_MB = 1900

# Bot API upload caps at 50 MB; 49 MB volume keeps slack for the wrapper.
BOTAPI_VOLUME_SIZE_MB = 49

# Per-item size cap for playlist archive mode. Playlist 7z mode allows
# items larger than MAX_FILE_SIZE_MB because the file never has to fit a
# single Telegram message — it will be split into volumes.
MAX_ARCHIVE_ITEM_SIZE_MB = 10240

# How long workspaces (pl_*/big_*) and pending archive jobs survive
# after success, so the user can resend a single failed volume without
# having to re-download the whole playlist.
PLAYLIST_ARCHIVE_RETENTION_MIN = 60
```

(Pierwsze dwa wpisy — `MAX_PLAYLIST_ITEMS` / `MAX_PLAYLIST_ITEMS_EXPANDED` — już istnieją w pliku; zachowaj je niezmienione, dopisz nowe stałe poniżej.)

- [ ] **Step 1.4: Uruchomić testy ponownie — oczekiwany PASS**

```bash
pytest tests/test_security_unit.py -v
```

- [ ] **Step 1.5: Commit**

```bash
git add bot/security_limits.py tests/test_security_unit.py
git commit -m "Add archive size and retention constants for 7z mode"
```

---

## Task 2: Helper-y w `bot/archive.py` (volume_size_for, transliteracja, basename, detekcja 7z)

**Files:**
- Create: `bot/archive.py`
- Test: `tests/test_archive.py` (create)

- [ ] **Step 2.1: Utworzyć `tests/test_archive.py` z failing testami**

```python
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
```

- [ ] **Step 2.2: Uruchomić testy — oczekiwany FAIL (brak modułu)**

```bash
pytest tests/test_archive.py -v
```

Oczekiwany: `ModuleNotFoundError: No module named 'bot.archive'`.

- [ ] **Step 2.3: Utworzyć `bot/archive.py` z helperami**

```python
"""Low-level wrapper around the system 7z binary.

This module knows nothing about Telegram, sessions, or downloads. It only
shells out to 7z, normalizes its output, and exposes a few naming/sizing
helpers used by bot.services.archive_service.
"""

from __future__ import annotations

import logging
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path

from bot.security_limits import BOTAPI_VOLUME_SIZE_MB, MTPROTO_VOLUME_SIZE_MB


def volume_size_for(use_mtproto: bool) -> int:
    """Volume size for 7z multi-volume archives, in MiB.

    MTProto allows ~2 GB per message, Bot API caps at 50 MB; we leave a
    margin in both cases so the resulting volume always slots in.
    """

    return MTPROTO_VOLUME_SIZE_MB if use_mtproto else BOTAPI_VOLUME_SIZE_MB


def transliterate_to_ascii(text: str) -> str:
    """Best-effort ASCII transliteration of Polish/diacritic characters.

    NFKD decomposes characters like 'ą' into 'a' + combining ogonek, and
    we drop the combining marks. The handful of Polish letters that NFKD
    does not decompose (notably 'ł' / 'Ł') are mapped explicitly.
    Used for naming 7z volumes shipped to a Windows host.
    """

    explicit_map = {
        "ł": "l",
        "Ł": "L",
    }
    translated = "".join(explicit_map.get(ch, ch) for ch in text)
    decomposed = unicodedata.normalize("NFKD", translated)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def compute_archive_basename(slug: str, ts: datetime) -> str:
    """Deterministic prefix for archive volumes: <slug>_<YYYYMMDD-HHMMSS>."""

    return f"{slug}_{ts.strftime('%Y%m%d-%H%M%S')}"


def is_7z_available() -> bool:
    """Return True when the 7z binary is present in PATH."""

    return shutil.which("7z") is not None
```

- [ ] **Step 2.4: Uruchomić testy ponownie — oczekiwany PASS**

```bash
pytest tests/test_archive.py -v
```

- [ ] **Step 2.5: Commit**

```bash
git add bot/archive.py tests/test_archive.py
git commit -m "Add archive helpers: volume sizing, ASCII translit, basename, 7z detection"
```

---

## Task 3: `pack_to_volumes` w `bot/archive.py`

**Files:**
- Modify: `bot/archive.py`
- Test: `tests/test_archive.py` (extend)

- [ ] **Step 3.1: Dodać failing testy dla `pack_to_volumes`**

Dopisz w `tests/test_archive.py`:

```python
import asyncio


def test_pack_to_volumes_raises_on_empty_sources():
    from bot.archive import pack_to_volumes

    with pytest.raises(ValueError, match="empty sources"):
        asyncio.run(pack_to_volumes([], Path("/tmp/x"), volume_size_mb=10))


def test_pack_to_volumes_invokes_7z_with_correct_args(tmp_path):
    from bot import archive

    src1 = tmp_path / "a.bin"
    src1.write_bytes(b"x")
    src2 = tmp_path / "b.bin"
    src2.write_bytes(b"y")
    dest = tmp_path / "out_archive"

    completed = mock.AsyncMock()
    completed.communicate = mock.AsyncMock(return_value=(b"", b""))
    completed.returncode = 0

    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        # Simulate 7z producing two volumes.
        (tmp_path / "out_archive.7z.001").write_bytes(b"a")
        (tmp_path / "out_archive.7z.002").write_bytes(b"b")
        return completed

    with mock.patch("bot.archive.asyncio.create_subprocess_exec", side_effect=fake_exec):
        result = asyncio.run(
            archive.pack_to_volumes([src1, src2], dest, volume_size_mb=42)
        )

    assert captured["args"][:6] == (
        "7z",
        "a",
        "-t7z",
        "-v42m",
        "-mx0",
        "-mmt=on",
    )
    assert str(dest.with_suffix(".7z")) in captured["args"]
    assert str(src1) in captured["args"]
    assert str(src2) in captured["args"]
    assert result == [tmp_path / "out_archive.7z.001", tmp_path / "out_archive.7z.002"]


def test_pack_to_volumes_returns_sorted_volume_paths(tmp_path):
    from bot import archive

    src = tmp_path / "a.bin"
    src.write_bytes(b"x")
    dest = tmp_path / "playlist"

    completed = mock.AsyncMock()
    completed.communicate = mock.AsyncMock(return_value=(b"", b""))
    completed.returncode = 0

    async def fake_exec(*args, **kwargs):
        # Volumes intentionally created out of order on disk.
        for i in (3, 1, 2):
            (tmp_path / f"playlist.7z.00{i}").write_bytes(b"z")
        return completed

    with mock.patch("bot.archive.asyncio.create_subprocess_exec", side_effect=fake_exec):
        result = asyncio.run(archive.pack_to_volumes([src], dest, volume_size_mb=10))

    assert [p.name for p in result] == [
        "playlist.7z.001",
        "playlist.7z.002",
        "playlist.7z.003",
    ]


def test_pack_to_volumes_raises_when_7z_exits_nonzero(tmp_path):
    from bot import archive

    src = tmp_path / "a.bin"
    src.write_bytes(b"x")

    completed = mock.AsyncMock()
    completed.communicate = mock.AsyncMock(return_value=(b"", b"E_NO_DISK_SPACE\n"))
    completed.returncode = 2

    async def fake_exec(*args, **kwargs):
        return completed

    with mock.patch("bot.archive.asyncio.create_subprocess_exec", side_effect=fake_exec):
        with pytest.raises(RuntimeError, match="7z failed"):
            asyncio.run(
                archive.pack_to_volumes([src], tmp_path / "x", volume_size_mb=1)
            )


def test_pack_to_volumes_real_7z_small_volumes(tmp_path):
    """Integration test: spawn the real 7z binary on a small file."""

    import shutil as _shutil
    if _shutil.which("7z") is None:
        pytest.skip("7z binary not available on this host")

    from bot import archive

    src = tmp_path / "data.bin"
    # 3 MB content, with -v1m volumes -> at least 3 volumes.
    src.write_bytes(b"Z" * (3 * 1024 * 1024))
    dest = tmp_path / "intg"

    result = asyncio.run(archive.pack_to_volumes([src], dest, volume_size_mb=1))

    assert len(result) >= 3
    assert all(p.exists() for p in result)
    assert all(p.name.startswith("intg.7z.") for p in result)
```

- [ ] **Step 3.2: Uruchomić testy — oczekiwany FAIL (`AttributeError: pack_to_volumes`)**

```bash
pytest tests/test_archive.py -v -k "pack_to_volumes"
```

- [ ] **Step 3.3: Implementacja `pack_to_volumes`**

Dopisz w `bot/archive.py`:

```python
import asyncio
from typing import Awaitable, Callable, Sequence


async def pack_to_volumes(
    sources: Sequence[Path],
    dest_basename: Path,
    volume_size_mb: int,
    *,
    progress_cb: Callable[[str], Awaitable[None]] | None = None,
) -> list[Path]:
    """Pack ``sources`` into a 7z multi-volume archive at ``dest_basename``.

    Resulting volumes are named ``<dest_basename>.7z.001``, ``.002`` etc.
    Returns the sorted list of created volume paths.

    The 7z process is invoked with ``-t7z -v<size>m -mx0 -mmt=on`` because:
    - the inputs are already-compressed media (mp3/mp4/m4a), so further
      compression buys nothing while costing significant CPU,
    - multi-threading speeds the I/O-bound packing on multi-core hosts.

    Raises:
        ValueError: when ``sources`` is empty.
        RuntimeError: when 7z exits with a non-zero status.
    """

    if not sources:
        raise ValueError("empty sources")

    archive_path = dest_basename.with_suffix(".7z")
    args = [
        "7z",
        "a",
        "-t7z",
        f"-v{volume_size_mb}m",
        "-mx0",
        "-mmt=on",
        str(archive_path),
        *[str(src) for src in sources],
    ]

    logging.info("Running 7z pack: %s", " ".join(args))
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    if progress_cb is not None:
        await _stream_7z_progress(process, progress_cb)

    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        err = stderr.decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"7z failed (exit {process.returncode}): {err}")

    parent = dest_basename.parent
    prefix = f"{archive_path.name}."
    volumes = sorted(
        p for p in parent.iterdir()
        if p.name.startswith(prefix) and p.name[len(prefix):].isdigit()
    )
    logging.info("7z packed %d volume(s) for %s", len(volumes), archive_path)
    return volumes


async def _stream_7z_progress(
    process: asyncio.subprocess.Process,
    progress_cb: Callable[[str], Awaitable[None]],
) -> None:
    """Throttle 7z stdout updates to one progress_cb call every 2 seconds."""

    if process.stdout is None:
        return

    last_update = 0.0
    loop = asyncio.get_event_loop()
    while True:
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
            except Exception as exc:  # progress is best-effort
                logging.warning("Archive progress callback failed: %s", exc)
            last_update = now
```

- [ ] **Step 3.4: Uruchomić testy — oczekiwany PASS (4 unit + 1 integration)**

```bash
pytest tests/test_archive.py -v
```

- [ ] **Step 3.5: Commit**

```bash
git add bot/archive.py tests/test_archive.py
git commit -m "Add async pack_to_volumes wrapper around 7z multi-volume packing"
```

---

## Task 4: `send_document_mtproto` w `bot/mtproto.py`

**Files:**
- Modify: `bot/mtproto.py`
- Test: `tests/test_mtproto.py` (extend)

- [ ] **Step 4.1: Dodać failing testy do `tests/test_mtproto.py`**

Dopisz na końcu istniejącego pliku:

```python
def test_send_document_mtproto_returns_false_without_pyrogram(monkeypatch):
    from bot import mtproto

    monkeypatch.setattr(
        "builtins.__import__",
        _make_blocked_import("pyrogram"),
    )
    result = asyncio.run(
        mtproto.send_document_mtproto(123, "/tmp/x.bin", caption="x")
    )
    assert result is False


def test_send_document_mtproto_returns_false_without_credentials(monkeypatch):
    from bot import mtproto

    monkeypatch.setattr(
        mtproto, "get_runtime_value",
        lambda key, default="": "",
    )
    result = asyncio.run(
        mtproto.send_document_mtproto(123, "/tmp/x.bin", caption="x")
    )
    assert result is False


def test_send_document_mtproto_returns_false_on_invalid_api_id(monkeypatch):
    from bot import mtproto

    values = {"TELEGRAM_API_ID": "not-a-number", "TELEGRAM_API_HASH": "abc"}
    monkeypatch.setattr(
        mtproto, "get_runtime_value",
        lambda key, default="": values.get(key, default),
    )
    result = asyncio.run(
        mtproto.send_document_mtproto(123, "/tmp/x.bin", caption="x")
    )
    assert result is False


def test_send_document_mtproto_invokes_send_document(tmp_path, monkeypatch):
    from bot import mtproto

    src = tmp_path / "vol.7z.001"
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

    captured: dict = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def send_document(self, **kwargs):
            captured["send_kwargs"] = kwargs

    monkeypatch.setattr(mtproto, "_build_client", lambda *a, **kw: FakeClient())

    result = asyncio.run(
        mtproto.send_document_mtproto(
            chat_id=42,
            file_path=str(src),
            caption="part 1",
            file_name="playlist.7z.001",
        )
    )

    assert result is True
    assert captured["send_kwargs"]["chat_id"] == 42
    assert captured["send_kwargs"]["document"] == str(src)
    assert captured["send_kwargs"]["caption"] == "part 1"
    assert captured["send_kwargs"]["file_name"] == "playlist.7z.001"
```

Jeśli `_make_blocked_import` jeszcze nie istnieje w pliku, dopisz na górze (między importami):

```python
def _make_blocked_import(blocked: str):
    real_import = __import__

    def fake(name, *args, **kwargs):
        if name == blocked or name.startswith(f"{blocked}."):
            raise ImportError(f"blocked: {name}")
        return real_import(name, *args, **kwargs)

    return fake
```

(jeśli helper istnieje pod inną nazwą — użyj istniejącego.)

- [ ] **Step 4.2: Uruchomić testy — oczekiwany FAIL (`AttributeError: send_document_mtproto`)**

```bash
pytest tests/test_mtproto.py -v -k "send_document_mtproto"
```

- [ ] **Step 4.3: Implementacja `send_document_mtproto` w `bot/mtproto.py`**

Dopisz na końcu pliku, po `send_video_mtproto`:

```python
async def send_document_mtproto(
    chat_id: int,
    file_path: str,
    caption: str | None = None,
    file_name: str | None = None,
) -> bool:
    """Send a document file via MTProto (up to 2 GB).

    Used to ship 7z volumes (.7z.001, ...) so Telegram does not try to
    render them as media. ``file_name`` (when provided) overrides the
    visible attachment name in the chat — useful when the on-disk path
    contains a workspace prefix we don't want users to see.

    Args:
        chat_id: Destination chat ID.
        file_path: Local path to the document.
        caption: Optional message caption.
        file_name: Optional override for the displayed filename.

    Returns:
        True on success, False on error.
    """

    try:
        from pyrogram import Client  # noqa: F401
    except ImportError:
        logging.error("pyrogram not installed — cannot send large document")
        return False

    api_id = get_runtime_value("TELEGRAM_API_ID", "")
    api_hash = get_runtime_value("TELEGRAM_API_HASH", "")
    if not api_id or not api_hash:
        logging.error("TELEGRAM_API_ID/TELEGRAM_API_HASH not configured")
        return False

    api_id_int = _parse_api_id(api_id)
    if api_id_int is None:
        return False

    client = _build_client(chat_id, "send_document", api_id_int, api_hash)

    try:
        async with client:
            send_kwargs: dict = {
                "chat_id": chat_id,
                "document": file_path,
                "caption": caption,
            }
            if file_name is not None:
                send_kwargs["file_name"] = file_name
            await client.send_document(**send_kwargs)
            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            logging.info(
                "MTProto send_document OK: %s (%.1f MB) to chat %d",
                os.path.basename(file_path), file_size_mb, chat_id,
            )
            return True
    except Exception as e:
        logging.error("MTProto send_document failed: %s", e)
        return False
```

- [ ] **Step 4.4: Uruchomić testy — oczekiwany PASS**

```bash
pytest tests/test_mtproto.py -v
```

- [ ] **Step 4.5: Commit**

```bash
git add bot/mtproto.py tests/test_mtproto.py
git commit -m "Add send_document_mtproto for shipping 7z volumes via pyrogram"
```

---

## Task 5: Stany sesji w `bot/session_store.py`

**Files:**
- Modify: `bot/session_store.py`
- Test: `tests/test_session_store.py` (extend)

- [ ] **Step 5.1: Dodać failing testy**

Dopisz w `tests/test_session_store.py`:

```python
def test_pending_archive_jobs_field_is_independent_per_chat():
    from bot.session_store import (
        ArchiveJobState,
        pending_archive_jobs,
        session_store,
    )
    from datetime import datetime
    from pathlib import Path

    session_store.reset()
    state_a = ArchiveJobState(
        file_path=Path("/tmp/a.mp4"),
        title="A",
        media_type="video",
        format_choice="best",
        file_size_mb=12.0,
        use_mtproto=False,
        created_at=datetime(2026, 5, 2, 12, 0, 0),
    )
    pending_archive_jobs[111] = {"tok-a": state_a}

    state_b = ArchiveJobState(
        file_path=Path("/tmp/b.mp4"),
        title="B",
        media_type="video",
        format_choice="best",
        file_size_mb=15.0,
        use_mtproto=False,
        created_at=datetime(2026, 5, 2, 12, 1, 0),
    )
    pending_archive_jobs[222] = {"tok-b": state_b}

    assert pending_archive_jobs[111] == {"tok-a": state_a}
    assert pending_archive_jobs[222] == {"tok-b": state_b}
    session_store.reset()


def test_archived_deliveries_field_holds_volume_state():
    from bot.session_store import (
        ArchivedDeliveryState,
        archived_deliveries,
        session_store,
    )
    from datetime import datetime
    from pathlib import Path

    session_store.reset()
    delivery = ArchivedDeliveryState(
        workspace=Path("/tmp/pl_ws"),
        volumes=[Path("/tmp/pl_ws/x.7z.001"), Path("/tmp/pl_ws/x.7z.002")],
        caption_prefix="My playlist",
        use_mtproto=True,
        created_at=datetime(2026, 5, 2, 12, 30, 0),
    )
    archived_deliveries[42] = {"tok-z": delivery}

    assert archived_deliveries[42] == {"tok-z": delivery}
    session_store.reset()
```

- [ ] **Step 5.2: Uruchomić testy — oczekiwany FAIL (`ImportError`)**

```bash
pytest tests/test_session_store.py -v -k "archive"
```

- [ ] **Step 5.3: Dodać dataclass'y i pola w `bot/session_store.py`**

Wstaw przed klasą `SessionState` (linia 12 obecnego pliku):

```python
@dataclass
class ArchiveJobState:
    """In-memory state for a single-file archive flow waiting for user choice.

    Created when a downloaded file exceeds the active Telegram transport
    limit; consumed by arc_split_<token> / arc_cancel_<token> callbacks.
    """

    file_path: Any  # pathlib.Path; Any avoids importing Path here
    title: str
    media_type: str
    format_choice: str
    file_size_mb: float
    use_mtproto: bool
    created_at: Any  # datetime


@dataclass
class ArchivedDeliveryState:
    """In-memory state for a sent archive set, kept for retry/purge buttons."""

    workspace: Any        # pathlib.Path
    volumes: list[Any]    # list[Path]
    caption_prefix: str
    use_mtproto: bool
    created_at: Any       # datetime
```

W `SessionState` dodaj dwa nowe pola (po `subtitle_pending`):

```python
    pending_archive_jobs: dict[str, "ArchiveJobState"] | None = None
    archived_deliveries: dict[str, "ArchivedDeliveryState"] | None = None
```

W `_cleanup_if_empty` dopisz koniunkcyjnie nowe pola:

```python
            and session.subtitle_pending is None
            and session.pending_archive_jobs is None
            and session.archived_deliveries is None
```

Na końcu pliku, obok istniejących mapowań, dodaj:

```python
pending_archive_jobs = SessionFieldMap(session_store, "pending_archive_jobs")
archived_deliveries = SessionFieldMap(session_store, "archived_deliveries")
```

- [ ] **Step 5.4: Uruchomić testy — oczekiwany PASS**

```bash
pytest tests/test_session_store.py -v
```

- [ ] **Step 5.5: Commit**

```bash
git add bot/session_store.py tests/test_session_store.py
git commit -m "Add ArchiveJobState and ArchivedDeliveryState session fields"
```

---

## Task 6: `bot/services/archive_service.py` — workspace + token registry

**Files:**
- Create: `bot/services/archive_service.py`
- Test: `tests/test_archive_service.py` (create)

- [ ] **Step 6.1: Stworzyć `tests/test_archive_service.py` z failing testami**

```python
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
```

- [ ] **Step 6.2: Uruchomić — oczekiwany FAIL**

```bash
pytest tests/test_archive_service.py -v
```

- [ ] **Step 6.3: Stworzyć `bot/services/archive_service.py`**

```python
"""End-to-end orchestration for 7z archive flows (playlist + single-file).

Boundaries:
- ``bot.archive`` — pure 7z wrapper, no Telegram/session knowledge.
- ``archive_service`` (this module) — knows about sessions, downloads,
  Telegram bot client, and gluing them together.
- ``bot.handlers.*`` — translate inline keyboard callbacks into calls
  on this service, never call ``bot.archive`` directly.
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from bot.archive import compute_archive_basename, transliterate_to_ascii
from bot.config import DOWNLOAD_PATH
from bot.downloader_validation import sanitize_filename
from bot.session_store import (
    ArchiveJobState,
    ArchivedDeliveryState,
    archived_deliveries,
    pending_archive_jobs,
)


_SLUG_MAX_LEN = 60


def _build_slug(title: str) -> str:
    """Translit-then-sanitize playlist/file title for use in filesystem path."""

    transliterated = transliterate_to_ascii(title)
    sanitized = sanitize_filename(transliterated)
    cleaned = sanitized.replace(" ", "_")
    return cleaned[:_SLUG_MAX_LEN] or "untitled"


def prepare_playlist_workspace(
    chat_id: int,
    playlist_title: str,
    *,
    prefix: str = "pl",
) -> Path:
    """Create ``downloads/<chat_id>/<prefix>_<slug>_<ts>/`` and return it."""

    slug = _build_slug(playlist_title)
    basename = compute_archive_basename(slug, datetime.now())
    chat_dir = Path(DOWNLOAD_PATH) / str(chat_id)
    chat_dir.mkdir(parents=True, exist_ok=True)
    workspace = chat_dir / f"{prefix}_{basename}"
    workspace.mkdir(parents=True, exist_ok=True)
    logging.info("Archive workspace ready: %s", workspace)
    return workspace


def register_pending_archive_job(chat_id: int, state: ArchiveJobState) -> str:
    """Store a pending archive job and return the lookup token (8 hex chars)."""

    token = secrets.token_hex(4)
    bucket = pending_archive_jobs.get(chat_id) or {}
    bucket[token] = state
    pending_archive_jobs[chat_id] = bucket
    return token


def register_archived_delivery(chat_id: int, state: ArchivedDeliveryState) -> str:
    """Store delivery metadata for retry/purge actions and return its token."""

    token = secrets.token_hex(4)
    bucket = archived_deliveries.get(chat_id) or {}
    bucket[token] = state
    archived_deliveries[chat_id] = bucket
    return token
```

- [ ] **Step 6.4: Utworzyć (jeśli nie istnieje) `bot/services/__init__.py`** — istnieje (`tests/test_archive_service.py` poleci, `bot/services` ma `__init__.py`).

- [ ] **Step 6.5: Uruchomić testy — oczekiwany PASS**

```bash
pytest tests/test_archive_service.py -v
```

- [ ] **Step 6.6: Commit**

```bash
git add bot/services/archive_service.py tests/test_archive_service.py
git commit -m "Add archive_service workspace creation and pending-job/delivery registries"
```

---

## Task 7: `download_playlist_into` — pobieranie do workspace bez wysyłki

**Files:**
- Modify: `bot/services/archive_service.py`
- Test: `tests/test_archive_service.py` (extend)

- [ ] **Step 7.1: Dodać failing testy**

```python
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
    assert any("huge" in title for title in failed)
```

- [ ] **Step 7.2: Uruchomić — oczekiwany FAIL**

```bash
pytest tests/test_archive_service.py -v -k "download_playlist_into"
```

- [ ] **Step 7.3: Implementacja**

Dopisz w `bot/services/archive_service.py`:

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Awaitable, Callable

from bot.security_limits import MAX_ARCHIVE_ITEM_SIZE_MB
from bot.services.download_service import (
    ensure_size_within_limit,
    estimate_download_size,
    execute_download,
    prepare_download_plan,
)
from bot.downloader_metadata import get_video_info  # used elsewhere; future hook


async def _download_one_into_workspace(
    entry: dict,
    workspace: Path,
    *,
    media_type: str,
    format_choice: str,
    executor: ThreadPoolExecutor,
) -> tuple[Path | None, float | None]:
    """Download one playlist item into ``workspace``. Returns (path, size_mb).

    On failure, ``path`` is None.
    Size limit (MAX_ARCHIVE_ITEM_SIZE_MB) is enforced by the caller, but we
    return the estimated size so the caller can decide what to do.
    """

    plan = prepare_download_plan(
        url=entry["url"],
        media_type=media_type,
        format_choice=format_choice,
        chat_download_path=str(workspace),
    )
    if plan is None:
        raise RuntimeError(f"could not fetch metadata for {entry.get('title')}")

    try:
        estimated = estimate_download_size(plan)
    except Exception:
        estimated = None

    if estimated is not None and not ensure_size_within_limit(
        estimated, max_size_mb=MAX_ARCHIVE_ITEM_SIZE_MB
    ):
        return None, estimated

    result = await execute_download(
        plan,
        chat_id=0,  # not used for progress reporting in archive flow
        executor=executor,
        progress_hook_factory=lambda _cid: (lambda _data: None),
        progress_state={},
        status_callback=_noop_status,
        format_bytes=lambda v: str(v),
        format_eta=lambda v: str(v),
    )
    return Path(result.file_path), result.file_size_mb


async def _noop_status(_text: str) -> None:
    return None


async def download_playlist_into(
    workspace: Path,
    entries: list[dict],
    *,
    media_type: str,
    format_choice: str,
    executor: ThreadPoolExecutor,
    status_cb: Callable[[str], Awaitable[None]],
) -> tuple[list[Path], list[str]]:
    """Download every entry into workspace, keeping the files (no os.remove).

    Returns (downloaded_paths, failed_titles). Items exceeding the
    MAX_ARCHIVE_ITEM_SIZE_MB cap are reported on failed_titles with a
    ``(too large: X MB)`` suffix. Network failures are similarly recorded
    with the original title.
    """

    downloaded: list[Path] = []
    failed: list[str] = []
    total = len(entries)

    for idx, entry in enumerate(entries, 1):
        title = entry.get("title", f"item_{idx}")
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

- [ ] **Step 7.4: Uruchomić — oczekiwany PASS**

```bash
pytest tests/test_archive_service.py -v
```

- [ ] **Step 7.5: Commit**

```bash
git add bot/services/archive_service.py tests/test_archive_service.py
git commit -m "Add download_playlist_into to keep files for archive packing"
```

---

## Task 8: `send_volumes` — wysyłka wolumenów Bot API / MTProto

**Files:**
- Modify: `bot/services/archive_service.py`
- Test: `tests/test_archive_service.py` (extend)

- [ ] **Step 8.1: Dodać failing testy**

```python
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
```

- [ ] **Step 8.2: Uruchomić — oczekiwany FAIL**

```bash
pytest tests/test_archive_service.py -v -k "send_volumes"
```

- [ ] **Step 8.3: Implementacja**

Dopisz w `bot/services/archive_service.py`:

```python
from bot.mtproto import mtproto_unavailability_reason, send_document_mtproto
from bot.security_limits import TELEGRAM_UPLOAD_LIMIT_MB


async def send_volumes(
    bot,
    chat_id: int,
    volumes: list[Path],
    caption_prefix: str,
    use_mtproto: bool,
    *,
    start_index: int = 0,
    status_cb: Callable[[str], Awaitable[None]] | None = None,
) -> None:
    """Send 7z volumes [start_index:] to ``chat_id`` as documents.

    Volumes ≤ TELEGRAM_UPLOAD_LIMIT_MB go via Bot API (``bot.send_document``);
    larger ones go via MTProto. Caption per volume is
    ``"<caption_prefix> [j/M]"``. The displayed file name is the volume's
    original name (``<basename>.7z.001`` etc).

    Raises:
        RuntimeError: when a volume needs MTProto but it is unavailable, or
            when MTProto sending returns False.
    """

    total = len(volumes)
    for idx in range(start_index, total):
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

(Parametr `use_mtproto` jest informacyjny dla wyższych warstw; sama decyzja transport-per-wolumen jest oparta o rozmiar.)

- [ ] **Step 8.4: Uruchomić — oczekiwany PASS**

```bash
pytest tests/test_archive_service.py -v
```

- [ ] **Step 8.5: Commit**

```bash
git add bot/services/archive_service.py tests/test_archive_service.py
git commit -m "Add send_volumes with Bot API/MTProto routing per volume size"
```

---

## Task 9: `execute_playlist_archive_flow` + `execute_single_file_archive_flow`

**Files:**
- Modify: `bot/services/archive_service.py`
- Test: `tests/test_archive_service.py` (extend)

- [ ] **Step 9.1: Dodać failing testy**

```python
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

    status = mock.AsyncMock()

    update = mock.MagicMock()
    update.callback_query = mock.MagicMock()
    update.callback_query.edit_message_text = status
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
    assert not any(chat_dir.iterdir()) if chat_dir.exists() else True
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
```

- [ ] **Step 9.2: Uruchomić — oczekiwany FAIL**

```bash
pytest tests/test_archive_service.py -v -k "execute_"
```

- [ ] **Step 9.3: Implementacja**

Dopisz w `bot/services/archive_service.py`:

```python
from bot.archive import is_7z_available, pack_to_volumes, volume_size_for


async def _safe_status_edit(update, text: str) -> None:
    """Edit the inline-keyboard message body, ignoring 'message not modified' errors."""

    try:
        await update.callback_query.edit_message_text(text)
    except Exception as exc:
        logging.debug("status edit failed (non-fatal): %s", exc)


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
    """End-to-end: workspace → download all → pack to 7z → send volumes."""

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

    workspace = prepare_playlist_workspace(chat_id, title, prefix="pl")
    lock_path = workspace / ".lock"
    lock_path.touch()

    async def status(text: str) -> None:
        await _safe_status_edit(update, text)

    await status(f"Playlista → 7z ({media_type} {format_choice})\n[0/{total}] Pobieranie...")

    try:
        downloaded, failed = await download_playlist_into(
            workspace,
            entries,
            media_type=media_type,
            format_choice=format_choice,
            executor=executor,
            status_cb=status,
        )

        if not downloaded:
            shutil.rmtree(workspace, ignore_errors=True)
            await status("Nie udało się pobrać żadnego elementu.")
            return

        await status(f"Pakowanie do 7z (vol_size={volume_size_mb} MB)...")
        slug = _build_slug(title)
        dest_basename = workspace / compute_archive_basename(
            f"{slug}_{media_type}_{format_choice}", datetime.now()
        )
        volumes = await pack_to_volumes(downloaded, dest_basename, volume_size_mb)

        caption_prefix = f"{title} ({media_type} {format_choice})"
        await status(f"Pakowanie OK: {len(volumes)} paczek. Wysyłanie...")
        await send_volumes(
            context.bot,
            chat_id=chat_id,
            volumes=volumes,
            caption_prefix=caption_prefix,
            use_mtproto=use_mtproto,
            status_cb=status,
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
            f"Wysłano: {len(volumes)}/{len(volumes)}",
            "Folder zostanie usunięty po 60 min.",
        ]
        if failed:
            summary_lines.append("")
            summary_lines.append("Nieudane elementy:")
            for title_ in failed[:5]:
                summary_lines.append(f"  - {title_[:60]}")

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "Wyślij wszystkie paczki ponownie",
                callback_data=f"arc_resend_{token}_0",
            )],
            [InlineKeyboardButton(
                "Usuń teraz",
                callback_data=f"arc_purge_{token}",
            )],
        ])
        try:
            await update.callback_query.edit_message_text(
                "\n".join(summary_lines),
                reply_markup=keyboard,
            )
        except Exception as exc:
            logging.debug("summary edit failed: %s", exc)
    except Exception as exc:
        logging.error("Playlist archive flow failed: %s", exc)
        await status(f"Pakowanie/wysyłka nie powiodły się: {exc}")
        # Workspace stays so user can retry; cleanup will remove it after retention.
    finally:
        try:
            lock_path.unlink()
        except OSError:
            pass


async def execute_single_file_archive_flow(
    update,
    context,
    *,
    chat_id: int,
    token: str,
) -> None:
    """End-to-end fallback for an oversized single file pre-registered as token."""

    if not is_7z_available():
        await _safe_status_edit(
            update,
            "Funkcja 7z niedostępna — administrator nie zainstalował p7zip-full.",
        )
        return

    bucket = pending_archive_jobs.get(chat_id) or {}
    state = bucket.get(token)
    if state is None:
        await _safe_status_edit(update, "Sesja wygasła. Wyślij plik ponownie.")
        return

    use_mtproto = mtproto_unavailability_reason() is None
    volume_size_mb = volume_size_for(use_mtproto)

    workspace = prepare_playlist_workspace(chat_id, state.title, prefix="big")
    lock_path = workspace / ".lock"
    lock_path.touch()

    src = Path(state.file_path)
    moved_path = workspace / src.name
    try:
        shutil.move(str(src), moved_path)
    except OSError as exc:
        await _safe_status_edit(update, f"Nie można przenieść pliku do workspace: {exc}")
        lock_path.unlink(missing_ok=True)
        return

    async def status(text: str) -> None:
        await _safe_status_edit(update, text)

    await status(f"Pakowanie do 7z (vol_size={volume_size_mb} MB)...")
    try:
        slug = _build_slug(state.title)
        dest_basename = workspace / compute_archive_basename(slug, datetime.now())
        volumes = await pack_to_volumes([moved_path], dest_basename, volume_size_mb)

        caption_prefix = state.title
        await status(f"Pakowanie OK: {len(volumes)} paczek. Wysyłanie...")
        await send_volumes(
            context.bot,
            chat_id=chat_id,
            volumes=volumes,
            caption_prefix=caption_prefix,
            use_mtproto=use_mtproto,
            status_cb=status,
        )

        delivery = ArchivedDeliveryState(
            workspace=workspace,
            volumes=volumes,
            caption_prefix=caption_prefix,
            use_mtproto=use_mtproto,
            created_at=datetime.now(),
        )
        delivery_token = register_archived_delivery(chat_id, delivery)

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

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
        try:
            await update.callback_query.edit_message_text(
                f"Plik wysłany w {len(volumes)} paczkach. Folder zostanie usunięty po 60 min.",
                reply_markup=keyboard,
            )
        except Exception as exc:
            logging.debug("summary edit failed: %s", exc)
    except Exception as exc:
        logging.error("Single-file archive flow failed: %s", exc)
        await status(f"Pakowanie/wysyłka nie powiodły się: {exc}")
    finally:
        # Always consume the pending job so the token cannot be re-used.
        bucket.pop(token, None)
        if not bucket:
            pending_archive_jobs.pop(chat_id, None)
        else:
            pending_archive_jobs[chat_id] = bucket
        try:
            lock_path.unlink()
        except OSError:
            pass
```

- [ ] **Step 9.4: Uruchomić — oczekiwany PASS**

```bash
pytest tests/test_archive_service.py -v
```

- [ ] **Step 9.5: Commit**

```bash
git add bot/services/archive_service.py tests/test_archive_service.py
git commit -m "Wire archive_service playlist+single-file flows end-to-end"
```

---

## Task 10: Parser callback + nowe przyciski w `bot/services/playlist_service.py`

**Files:**
- Modify: `bot/services/playlist_service.py`
- Test: `tests/test_playlist_service.py` (extend)

- [ ] **Step 10.1: Dodać failing testy**

```python
def test_parse_playlist_download_choice_recognizes_zip_prefix():
    from bot.services.playlist_service import parse_playlist_download_choice

    choice = parse_playlist_download_choice("pl_zip_dl_audio_mp3")

    assert choice.media_type == "audio"
    assert choice.format_choice == "mp3"
    assert choice.as_archive is True


def test_parse_playlist_download_choice_legacy_prefix_unchanged():
    from bot.services.playlist_service import parse_playlist_download_choice

    choice = parse_playlist_download_choice("pl_dl_audio_mp3")

    assert choice.media_type == "audio"
    assert choice.format_choice == "mp3"
    assert choice.as_archive is False


def test_build_playlist_message_includes_zip_buttons_when_archive_available():
    from bot.services.playlist_service import build_playlist_message

    msg, kb = build_playlist_message(
        {"title": "X", "entries": [{"title": "a", "duration": 60}], "playlist_count": 1},
        archive_available=True,
    )

    callback_data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "pl_dl_audio_mp3" in callback_data
    assert "pl_zip_dl_audio_mp3" in callback_data
    assert "pl_zip_dl_video_720p" in callback_data


def test_build_playlist_message_hides_zip_buttons_when_archive_unavailable():
    from bot.services.playlist_service import build_playlist_message

    msg, kb = build_playlist_message(
        {"title": "X", "entries": [{"title": "a", "duration": 60}], "playlist_count": 1},
        archive_available=False,
    )

    callback_data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "pl_dl_audio_mp3" in callback_data
    assert not any(cd.startswith("pl_zip_dl_") for cd in callback_data)
```

- [ ] **Step 10.2: Uruchomić — oczekiwany FAIL**

```bash
pytest tests/test_playlist_service.py -v -k "zip"
```

- [ ] **Step 10.3: Modyfikacja `bot/services/playlist_service.py`**

Zmień `PlaylistDownloadChoice`:

```python
@dataclass
class PlaylistDownloadChoice:
    """Parsed playlist callback choice."""

    media_type: str
    format_choice: str
    as_archive: bool = False
```

Zmień `parse_playlist_download_choice`:

```python
def parse_playlist_download_choice(callback_data: str) -> PlaylistDownloadChoice:
    """Parse playlist batch-download callback data.

    Recognizes two prefixes:
    - ``pl_dl_<media>_<format>``      → standard per-item send (legacy).
    - ``pl_zip_dl_<media>_<format>``  → archive (7z) flow.
    """

    if callback_data.startswith("pl_zip_dl_"):
        rest = callback_data.replace("pl_zip_dl_", "", 1)
        as_archive = True
    elif callback_data.startswith("pl_dl_"):
        rest = callback_data.replace("pl_dl_", "", 1)
        as_archive = False
    else:
        rest = callback_data
        as_archive = False

    parts = rest.split("_", 1)
    media_type = parts[0]
    format_choice = parts[1] if len(parts) > 1 else "best"
    return PlaylistDownloadChoice(
        media_type=media_type,
        format_choice=format_choice,
        as_archive=as_archive,
    )
```

Zmień `build_playlist_message` — dodaj parametr `archive_available` (domyślnie `False`, żeby istniejący kod się nie zepsuł), potem rozszerz keyboard:

```python
def build_playlist_message(
    playlist_info: dict[str, Any],
    *,
    archive_available: bool = False,
) -> tuple[str, InlineKeyboardMarkup]:
    """Build playlist listing text and controls.

    When ``archive_available`` is True, four extra "... jako 7z" buttons
    are inserted alongside the existing per-item-send buttons.
    """

    entries = playlist_info['entries']
    total = playlist_info.get('playlist_count', len(entries))

    msg = f"*{escape_markdown(playlist_info['title'], version=1)}*\n"
    msg += f"Filmów: {len(entries)}"
    if total > len(entries):
        msg += f" (z {total})"
    msg += "\n\n"

    for i, entry in enumerate(entries, 1):
        title = escape_markdown(entry.get('title', 'Nieznany')[:50], version=1)
        duration = entry.get('duration')
        dur_str = f"{duration // 60}:{duration % 60:02d}" if duration else "?"
        msg += f"{i}. {title} ({dur_str})\n"

    options = [
        ("Pobierz wszystkie — Audio MP3", "pl_dl_audio_mp3", "pl_zip_dl_audio_mp3"),
        ("Pobierz wszystkie — Audio M4A", "pl_dl_audio_m4a", "pl_zip_dl_audio_m4a"),
        ("Pobierz wszystkie — Video (najlepsza)", "pl_dl_video_best", "pl_zip_dl_video_best"),
        ("Pobierz wszystkie — Video 720p", "pl_dl_video_720p", "pl_zip_dl_video_720p"),
    ]
    keyboard: list[list[InlineKeyboardButton]] = []
    for label, plain, archive_cb in options:
        keyboard.append([InlineKeyboardButton(label, callback_data=plain)])
        if archive_available:
            keyboard.append([
                InlineKeyboardButton(f"{label} jako 7z", callback_data=archive_cb)
            ])

    if total > len(entries) and len(entries) < MAX_PLAYLIST_ITEMS_EXPANDED:
        more_count = min(total, MAX_PLAYLIST_ITEMS_EXPANDED)
        keyboard.append([InlineKeyboardButton(
            f"Pokaż więcej (do {more_count})", callback_data="pl_more"
        )])

    keyboard.append([InlineKeyboardButton("Anuluj", callback_data="pl_cancel")])
    return msg, InlineKeyboardMarkup(keyboard)
```

- [ ] **Step 10.4: Uruchomić — oczekiwany PASS**

```bash
pytest tests/test_playlist_service.py -v
```

- [ ] **Step 10.5: Commit**

```bash
git add bot/services/playlist_service.py tests/test_playlist_service.py
git commit -m "Add pl_zip_dl_* parser and archive buttons in playlist menu"
```

---

## Task 11: Dispatch w `bot/handlers/playlist_callbacks.py`

**Files:**
- Modify: `bot/handlers/playlist_callbacks.py`
- Test: `tests/test_playlist.py` (extend)

- [ ] **Step 11.1: Dodać failing test**

W `tests/test_playlist.py`:

```python
import asyncio
from unittest import mock


def test_handle_playlist_callback_pl_zip_dl_dispatches_to_archive_flow():
    from bot.handlers import playlist_callbacks
    from bot.session_store import session_store, user_playlist_data

    session_store.reset()
    user_playlist_data[42] = {
        "title": "Pl",
        "entries": [{"url": "u", "title": "a"}],
    }

    update = mock.MagicMock()
    update.effective_chat.id = 42
    update.callback_query = mock.MagicMock()
    update.callback_query.edit_message_text = mock.AsyncMock()
    context = mock.MagicMock()

    fake_flow = mock.AsyncMock()
    with mock.patch(
        "bot.handlers.playlist_callbacks.execute_playlist_archive_flow", fake_flow
    ):
        asyncio.run(
            playlist_callbacks.handle_playlist_callback(
                update, context, "pl_zip_dl_audio_mp3"
            )
        )

    assert fake_flow.await_count == 1
    kwargs = fake_flow.await_args.kwargs
    assert kwargs["chat_id"] == 42
    assert kwargs["media_type"] == "audio"
    assert kwargs["format_choice"] == "mp3"
    session_store.reset()
```

- [ ] **Step 11.2: Uruchomić — oczekiwany FAIL**

```bash
pytest tests/test_playlist.py -v -k "pl_zip_dl"
```

- [ ] **Step 11.3: Implementacja w `bot/handlers/playlist_callbacks.py`**

Dodaj import na górze:

```python
from bot.services.archive_service import execute_playlist_archive_flow
```

W funkcji `handle_playlist_callback` dodaj dispatch:

```python
    if data.startswith("pl_zip_dl_"):
        await _dispatch_archive_playlist(update, context, data)
        return

    if data.startswith("pl_dl_"):
        await download_playlist(update, context, data)
```

(zastąp istniejące `if data.startswith("pl_dl_")` powyższą parą — kolejność istotna, bo `pl_zip_dl_` zaczyna się od `pl_zip_`, ale gdyby kiedyś `pl_dl_` weszło pierwsze przez `startswith`, wciąż by łapało; specyficzny prefix ma wyższy priorytet.)

Dodaj nową funkcję na końcu pliku:

```python
async def _dispatch_archive_playlist(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    callback_data: str,
) -> None:
    chat_id = update.effective_chat.id
    playlist = _get_session_value(context, chat_id, "playlist_data", user_playlist_data)
    if not playlist:
        await update.callback_query.edit_message_text(
            "Sesja playlisty wygasła. Wyślij link ponownie."
        )
        return

    from bot.services.playlist_service import parse_playlist_download_choice
    choice = parse_playlist_download_choice(callback_data)

    await execute_playlist_archive_flow(
        update,
        context,
        chat_id=chat_id,
        playlist=playlist,
        media_type=choice.media_type,
        format_choice=choice.format_choice,
        executor=_executor,
    )
    _clear_session_value(context, chat_id, "playlist_data", user_playlist_data)
```

- [ ] **Step 11.4: Uruchomić — oczekiwany PASS**

```bash
pytest tests/test_playlist.py -v
```

- [ ] **Step 11.5: Commit**

```bash
git add bot/handlers/playlist_callbacks.py tests/test_playlist.py
git commit -m "Dispatch pl_zip_dl_* callbacks to archive_service playlist flow"
```

---

## Task 12: Dispatch `arc_*` w `bot/handlers/download_callbacks.py` + fallback po pobraniu

**Files:**
- Modify: `bot/handlers/download_callbacks.py`
- Test: `tests/test_callback_download_handlers.py` (extend)

- [ ] **Step 12.1: Dodać failing testy**

```python
def test_oversized_single_file_offers_archive_choice(tmp_path, monkeypatch):
    """File too big after download → user gets [Wyślij jako 7z][Anuluj]."""
    from bot.handlers import download_callbacks
    from bot.session_store import pending_archive_jobs, session_store

    session_store.reset()
    pretend = tmp_path / "big.mp4"
    pretend.write_bytes(b"x")

    captured = {}

    async def fake_offer_archive(update, context, chat_id, file_path, title, media_type, format_choice, file_size_mb):
        captured["called"] = True

    monkeypatch.setattr(
        download_callbacks, "_offer_archive_or_cancel", fake_offer_archive
    )

    import asyncio
    asyncio.run(
        download_callbacks._offer_archive_or_cancel(
            mock.MagicMock(),
            mock.MagicMock(),
            chat_id=1,
            file_path=str(pretend),
            title="t",
            media_type="video",
            format_choice="best",
            file_size_mb=999.0,
        )
    )
    assert captured["called"] is True
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
```

- [ ] **Step 12.2: Uruchomić — oczekiwany FAIL**

```bash
pytest tests/test_callback_download_handlers.py -v -k "arc_ or oversized"
```

- [ ] **Step 12.3: Implementacja w `bot/handlers/download_callbacks.py`**

Dodaj na górze do importów:

```python
from datetime import datetime
from pathlib import Path
import shutil

from bot.session_store import (
    ArchiveJobState,
    archived_deliveries,
    pending_archive_jobs,
)
from bot.services.archive_service import (
    execute_single_file_archive_flow,
    register_pending_archive_job,
    send_volumes,
)
from bot.archive import volume_size_for
from bot.mtproto import mtproto_unavailability_reason
```

W funkcji `download_file` zmodyfikuj sekcję wysyłania (gałąź `else` po `transcribe`), zastępując bieżący blok `if use_mtproto: ... mtproto_unavailability_reason() ... raise RuntimeError`:

Aktualnie (~linia 514):

```python
            use_mtproto = file_size_mb > TELEGRAM_UPLOAD_LIMIT_MB
```

zostaje. Dalej blok `if use_mtproto: ...` zastąp tak, żeby przed `raise RuntimeError` zaproponować fallback:

```python
            volume_size_mb = volume_size_for(use_mtproto=mtproto_unavailability_reason() is None)
            if file_size_mb > volume_size_mb:
                # File exceeds the practical Telegram limit even via the best
                # available transport. Offer the 7z archive split instead of
                # failing the whole download.
                await _offer_archive_or_cancel(
                    update,
                    context,
                    chat_id=chat_id,
                    file_path=downloaded_file_path,
                    title=title,
                    media_type=media_type,
                    format_choice=format,
                    file_size_mb=file_size_mb,
                )
                # Skip cleanup — the archive flow owns the file from now on.
                success_recorded = True
                return
```

(Wstaw ten blok PRZED istniejącą logiką `if use_mtproto: ... else: ...`. Stary kod pozostaje jako szybka ścieżka dla plików ≤ volume_size_mb.)

Dodaj na końcu pliku:

```python
async def _offer_archive_or_cancel(
    update,
    context,
    *,
    chat_id: int,
    file_path: str,
    title: str,
    media_type: str,
    format_choice: str,
    file_size_mb: float,
) -> None:
    """Register a pending archive job and present [Wyślij jako 7z]/[Anuluj]."""

    use_mtproto = mtproto_unavailability_reason() is None
    volume_size_mb = volume_size_for(use_mtproto)
    state = ArchiveJobState(
        file_path=Path(file_path),
        title=title,
        media_type=media_type,
        format_choice=format_choice,
        file_size_mb=file_size_mb,
        use_mtproto=use_mtproto,
        created_at=datetime.now(),
    )
    token = register_pending_archive_job(chat_id, state)

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Wyślij jako 7z", callback_data=f"arc_split_{token}")],
        [InlineKeyboardButton("Anuluj", callback_data=f"arc_cancel_{token}")],
    ])
    text = (
        f"Plik za duży dla Telegrama: {file_size_mb:.0f} MB > limit {volume_size_mb} MB.\n"
        f"Mogę spakować go w wolumeny 7z (po {volume_size_mb} MB) i wysłać paczki."
    )
    try:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    except Exception as exc:
        logging.debug("offer-archive edit failed: %s", exc)


async def handle_archive_callback(update, context, data: str) -> None:
    """Dispatch arc_split_/arc_cancel_/arc_resend_/arc_purge_ callbacks."""

    chat_id = update.effective_chat.id

    if data.startswith("arc_split_"):
        token = data[len("arc_split_"):]
        await execute_single_file_archive_flow(
            update, context, chat_id=chat_id, token=token,
        )
        return

    if data.startswith("arc_cancel_"):
        token = data[len("arc_cancel_"):]
        await _handle_arc_cancel(update, chat_id, token)
        return

    if data.startswith("arc_resend_"):
        await _handle_arc_resend(update, context, chat_id, data)
        return

    if data.startswith("arc_purge_"):
        token = data[len("arc_purge_"):]
        await _handle_arc_purge(update, chat_id, token)
        return


async def _handle_arc_cancel(update, chat_id: int, token: str) -> None:
    bucket = pending_archive_jobs.get(chat_id) or {}
    state = bucket.pop(token, None)
    if not bucket:
        pending_archive_jobs.pop(chat_id, None)
    else:
        pending_archive_jobs[chat_id] = bucket
    if state is not None:
        try:
            os.remove(str(state.file_path))
        except OSError:
            pass
    try:
        await update.callback_query.edit_message_text("Anulowano. Plik usunięty.")
    except Exception as exc:
        logging.debug("arc_cancel edit failed: %s", exc)


async def _handle_arc_resend(update, context, chat_id: int, data: str) -> None:
    rest = data[len("arc_resend_"):]
    if "_" not in rest:
        return
    token, idx_str = rest.rsplit("_", 1)
    try:
        start_index = int(idx_str)
    except ValueError:
        return

    bucket = archived_deliveries.get(chat_id) or {}
    state = bucket.get(token)
    if state is None:
        try:
            await update.callback_query.edit_message_text("Sesja wygasła.")
        except Exception:
            pass
        return

    async def status(text: str) -> None:
        try:
            await update.callback_query.edit_message_text(text)
        except Exception:
            pass

    try:
        await send_volumes(
            context.bot,
            chat_id=chat_id,
            volumes=state.volumes,
            caption_prefix=state.caption_prefix,
            use_mtproto=state.use_mtproto,
            start_index=start_index,
            status_cb=status,
        )
        await status(f"Wysłano paczki od [{start_index + 1}/{len(state.volumes)}].")
    except Exception as exc:
        await status(f"Wysyłka nadal nie powiodła się: {exc}")


async def _handle_arc_purge(update, chat_id: int, token: str) -> None:
    bucket = archived_deliveries.get(chat_id) or {}
    state = bucket.pop(token, None)
    if not bucket:
        archived_deliveries.pop(chat_id, None)
    else:
        archived_deliveries[chat_id] = bucket
    if state is not None and state.workspace.exists():
        shutil.rmtree(state.workspace, ignore_errors=True)
    try:
        await update.callback_query.edit_message_text("Folder usunięty.")
    except Exception as exc:
        logging.debug("arc_purge edit failed: %s", exc)
```

- [ ] **Step 12.4: Podpiąć dispatcher do głównego callback-routera**

W miejscu, gdzie router rozpoznaje callback prefixy (najprawdopodobniej w `bot/handlers/download_callbacks.py` lub wyżej w `telegram_callbacks.py` — sprawdź `if data.startswith(...)` ladder), dodaj gałąź:

```python
    if data.startswith("arc_"):
        await handle_archive_callback(update, context, data)
        return
```

Jeśli router znajduje się w innym pliku (np. `bot/telegram_callbacks.py`), import też tam:

```python
from bot.handlers.download_callbacks import handle_archive_callback
```

- [ ] **Step 12.5: Uruchomić — oczekiwany PASS**

```bash
pytest tests/test_callback_download_handlers.py -v
```

- [ ] **Step 12.6: Commit**

```bash
git add bot/handlers/download_callbacks.py bot/telegram_callbacks.py tests/test_callback_download_handlers.py
git commit -m "Add arc_* dispatching and oversized-file 7z fallback in single-file flow"
```

(Jeśli `bot/telegram_callbacks.py` nie wymagał zmian, dodaj tylko zmienione pliki.)

---

## Task 13: Flag `archive_available` w `bot/runtime.py` + przekazanie do menu

**Files:**
- Modify: `bot/runtime.py`
- Modify: `bot/handlers/playlist_callbacks.py` (przekazać flag do `build_playlist_message`)
- Test: `tests/test_runtime.py` (extend)

- [ ] **Step 13.1: Dodać failing test w `tests/test_runtime.py`**

```python
def test_app_runtime_includes_archive_available_flag(monkeypatch):
    from bot import runtime as runtime_module

    monkeypatch.setattr("bot.archive.is_7z_available", lambda: True)
    rt = runtime_module.build_app_runtime()
    assert rt.archive_available is True

    monkeypatch.setattr("bot.archive.is_7z_available", lambda: False)
    rt = runtime_module.build_app_runtime()
    assert rt.archive_available is False
```

- [ ] **Step 13.2: Uruchomić — oczekiwany FAIL (`archive_available` brak)**

```bash
pytest tests/test_runtime.py -v -k archive
```

- [ ] **Step 13.3: Modyfikacja `bot/runtime.py`**

W `AppRuntime` (frozen dataclass) dodaj pole:

```python
@dataclass(frozen=True)
class AppRuntime:
    config: dict[str, Any]
    session_store: Any
    security_store: Any
    services: Any
    authorized_users_repository: Any
    download_history_repository: Any
    authorized_users_set: set[int]
    archive_available: bool = False
```

W `build_app_runtime` dodaj wywołanie:

```python
def build_app_runtime() -> AppRuntime:
    from bot.archive import is_7z_available

    return AppRuntime(
        config=get_runtime_config(),
        session_store=session_store,
        security_store=security_store,
        services=get_runtime_services(),
        authorized_users_repository=get_authorized_users_repository(),
        download_history_repository=get_download_history_repository(),
        authorized_users_set=get_runtime_authorized_users(),
        archive_available=is_7z_available(),
    )
```

W `bot/handlers/playlist_callbacks.py` w funkcji `handle_playlist_callback` przy budowaniu `build_playlist_message`:

```python
        from bot.runtime import get_app_runtime
        runtime = get_app_runtime(context)
        archive_available = runtime.archive_available if runtime is not None else False
        msg, reply_markup = build_playlist_message(
            playlist_info, archive_available=archive_available,
        )
```

(Zastąp dwa wywołania `build_playlist_message(playlist_info)` w `pl_full` i `pl_more` powyższym wzorcem.)

- [ ] **Step 13.4: Uruchomić — oczekiwany PASS**

```bash
pytest tests/test_runtime.py tests/test_playlist.py -v
```

- [ ] **Step 13.5: Commit**

```bash
git add bot/runtime.py bot/handlers/playlist_callbacks.py tests/test_runtime.py
git commit -m "Expose archive_available flag in AppRuntime and propagate to playlist menu"
```

---

## Task 14: Cleanup workspace’ów + pending jobs w `bot/cleanup.py`

**Files:**
- Modify: `bot/cleanup.py`
- Test: `tests/test_cleanup.py` (extend)

- [ ] **Step 14.1: Dodać failing testy**

```python
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock


def test_purge_archive_workspaces_removes_old_pl_dirs(tmp_path, monkeypatch):
    from bot import cleanup

    chat_dir = tmp_path / "111"
    chat_dir.mkdir()
    old_ws = chat_dir / "pl_oldslug_20260102-080000"
    old_ws.mkdir()
    (old_ws / "x.7z.001").write_bytes(b"x")
    # Set mtime to 2 hours ago.
    old_time = time.time() - 2 * 3600
    os.utime(old_ws, (old_time, old_time))

    cleanup._purge_archive_workspaces(chat_dir, retention_min=60)

    assert not old_ws.exists()


def test_purge_archive_workspaces_keeps_recent_dirs(tmp_path):
    from bot import cleanup

    chat_dir = tmp_path / "222"
    chat_dir.mkdir()
    fresh_ws = chat_dir / "pl_fresh_20260502-080000"
    fresh_ws.mkdir()
    # Default mtime is "now", which is well under 60 min.
    cleanup._purge_archive_workspaces(chat_dir, retention_min=60)
    assert fresh_ws.exists()


def test_purge_archive_workspaces_respects_lock_when_recent(tmp_path):
    from bot import cleanup

    chat_dir = tmp_path / "333"
    chat_dir.mkdir()
    ws = chat_dir / "pl_locked_20260502-080000"
    ws.mkdir()
    (ws / ".lock").touch()
    # Make the workspace look 30 min old (under the 60 min retention).
    age = time.time() - 30 * 60
    os.utime(ws, (age, age))

    cleanup._purge_archive_workspaces(chat_dir, retention_min=60)

    assert ws.exists()


def test_purge_archive_workspaces_ignores_non_archive_dirs(tmp_path):
    from bot import cleanup

    chat_dir = tmp_path / "444"
    chat_dir.mkdir()
    other = chat_dir / "downloads_subfolder"
    other.mkdir()
    age = time.time() - 7200
    os.utime(other, (age, age))

    cleanup._purge_archive_workspaces(chat_dir, retention_min=60)
    assert other.exists()


def test_purge_pending_archive_jobs_removes_old_jobs(tmp_path):
    from bot import cleanup
    from bot.session_store import (
        ArchiveJobState,
        pending_archive_jobs,
        session_store,
    )

    session_store.reset()
    src = tmp_path / "x.mp4"
    src.write_bytes(b"x")
    old_state = ArchiveJobState(
        file_path=src, title="x", media_type="video", format_choice="best",
        file_size_mb=1.0, use_mtproto=False,
        created_at=datetime.now() - timedelta(hours=2),
    )
    pending_archive_jobs[1] = {"old": old_state}

    cleanup._purge_pending_archive_jobs(retention_min=60)

    assert pending_archive_jobs.get(1, {}).get("old") is None
    assert not src.exists()
    session_store.reset()
```

- [ ] **Step 14.2: Uruchomić — oczekiwany FAIL**

```bash
pytest tests/test_cleanup.py -v -k "purge"
```

- [ ] **Step 14.3: Implementacja w `bot/cleanup.py`**

Dopisz na początku pliku:

```python
from datetime import datetime, timedelta
from pathlib import Path

from bot.security_limits import PLAYLIST_ARCHIVE_RETENTION_MIN
```

Dodaj funkcje przed `periodic_cleanup`:

```python
_ARCHIVE_PREFIXES = ("pl_", "big_")


def _purge_archive_workspaces(chat_dir: Path, retention_min: int) -> int:
    """Remove archive workspaces older than retention_min, unless locked.

    Workspaces are subdirectories named ``pl_*`` or ``big_*``. A
    ``.lock`` file inside a young workspace blocks deletion (used during
    pack/send operations). After 24h workspaces are removed regardless
    of the lock — the long-running cleanup acts as a safety net.
    """

    if not chat_dir.exists():
        return 0

    now = time.time()
    threshold = retention_min * 60
    safety_net = 24 * 3600
    removed = 0

    for entry in chat_dir.iterdir():
        if not entry.is_dir():
            continue
        if not any(entry.name.startswith(p) for p in _ARCHIVE_PREFIXES):
            continue
        try:
            age = now - entry.stat().st_mtime
        except OSError as exc:
            logging.warning("Could not stat %s: %s", entry, exc)
            continue

        lock = entry / ".lock"
        if age <= threshold:
            continue
        if lock.exists() and age <= safety_net:
            continue

        try:
            shutil.rmtree(entry)
            removed += 1
            logging.info("Removed stale archive workspace: %s (age %.1f h)",
                         entry, age / 3600)
        except OSError as exc:
            logging.error("Failed to remove %s: %s", entry, exc)

    return removed


def _purge_pending_archive_jobs(retention_min: int) -> int:
    """Drop pending_archive_jobs entries older than retention_min and delete files."""

    from bot.session_store import pending_archive_jobs, session_store

    cutoff = datetime.now() - timedelta(minutes=retention_min)
    removed = 0
    for chat_id in list(pending_archive_jobs):
        bucket = pending_archive_jobs.get(chat_id) or {}
        for token in list(bucket):
            state = bucket[token]
            if state.created_at >= cutoff:
                continue
            bucket.pop(token, None)
            try:
                os.remove(str(state.file_path))
            except OSError:
                pass
            removed += 1
        if not bucket:
            pending_archive_jobs.pop(chat_id, None)
        else:
            pending_archive_jobs[chat_id] = bucket
    return removed
```

W `periodic_cleanup` (na końcu pętli) dopisz:

```python
            for chat_dir in Path(DOWNLOAD_PATH).iterdir():
                if chat_dir.is_dir():
                    _purge_archive_workspaces(chat_dir, PLAYLIST_ARCHIVE_RETENTION_MIN)
            _purge_pending_archive_jobs(PLAYLIST_ARCHIVE_RETENTION_MIN)
```

- [ ] **Step 14.4: Uruchomić — oczekiwany PASS**

```bash
pytest tests/test_cleanup.py -v
```

- [ ] **Step 14.5: Commit**

```bash
git add bot/cleanup.py tests/test_cleanup.py
git commit -m "Add cleanup of archive workspaces and pending jobs based on retention"
```

---

## Task 15: Integracja end-to-end (manual checklist)

Ten task nie ma testów automatycznych — to lista kontrolna ręczna do wykonania zanim PR `develop` → `main` zostanie utworzony. Zapisz wynik checklisty w komentarzu PR-a.

- [ ] **Step 15.1: Środowisko**

```bash
source /home/pi/venv/bin/activate
cd /mnt/c/code/ytdown
python main.py
```

Sprawdź w logach: `archive_available=True` przy starcie (lub stosowny log). Przyciski "… jako 7z" muszą się pojawiać w menu playlisty.

- [ ] **Step 15.2: Pobranie playlisty 5×audio_mp3 jako 7z**

Telegram: wyślij URL playlisty z 5 piosenkami → klik „Pobierz wszystkie — Audio MP3 jako 7z". Oczekiwane:
- jedna paczka `<title>_audio_mp3_<ts>.7z.001` w czacie,
- summary z `Pobrano: 5/5`, `Wysłano: 1/1`,
- przyciski `[Wyślij wszystkie paczki ponownie] [Usuń teraz]`.

- [ ] **Step 15.3: Pobranie playlisty 30×video_720p jako 7z**

Wyślij URL playlisty 30-elementowej → klik „Pobierz wszystkie — Video 720p jako 7z". Oczekiwane:
- kilka paczek `.7z.001`, `.002`, …, sumaryczny rozmiar > 1900 MB,
- po wszystkich paczkach summary OK.

- [ ] **Step 15.4: Sztuczne wymuszenie braku MTProto**

Tymczasowo zakomentuj `TELEGRAM_API_ID`/`HASH` w `api_key.md`, restart bota. Powtórz krok 15.2. Oczekiwane: paczki ≤ 49 MB, więcej wolumenów, status mówi że transport = Bot API.

- [ ] **Step 15.5: Recovery wysyłki**

W trakcie wysyłki kroku 15.3 wyłącz sieć na ~30 s tak żeby paczka `.003` failowała. Oczekiwane:
- status pokaże `Błąd wysyłki paczki [3/M]`,
- przycisk `[Ponów od [3/M]]` po włączeniu sieci dokończy wysyłkę bez ponownego pobierania.

- [ ] **Step 15.6: Cleanup po retencji**

Pozostaw workspace `pl_*` z poprzedniego kroku. Sprawdź `ls downloads/<chat_id>/` po 65 min — folder powinien zniknąć (lub wymuś `cleanup.periodic_cleanup` przez krótki `time.sleep` w testach manualnych).

- [ ] **Step 15.7: Single-file > limit**

Tymczasowo zmień w `bot/security_limits.py`: `MAX_FILE_SIZE_MB = 200`. Wymuś tryb Bot API (krok 15.4). Pobierz video YouTube ~250 MB. Oczekiwane:
- przyciski `[Wyślij jako 7z] [Anuluj]`,
- klik „Wyślij jako 7z" → 5–6 paczek po 49 MB,
- klik „Anuluj" → plik usunięty natychmiast.

Po teście przywróć `MAX_FILE_SIZE_MB = 1000` i odkomentuj `TELEGRAM_API_*`.

- [ ] **Step 15.8: Commit checklisty (jeśli były poprawki)**

Jeśli w trakcie testów coś poprawiałeś w kodzie — commituj punktowo. W przeciwnym razie żaden commit nie jest potrzebny.

---

## Self-review (dla autora planu)

1. **Spec coverage:**
   - Sekcja 2 (decyzje) — Tasks 1, 10, 12, 13.
   - Sekcja 3 (architektura) — Tasks 2, 3, 4, 5, 6.
   - Sekcja 4 (flow playlisty) — Tasks 7, 8, 9, 10, 11, 13.
   - Sekcja 5 (single-file fallback) — Tasks 9, 12.
   - Sekcja 6 (konfiguracja, cleanup, error handling) — Tasks 1, 4, 14.
   - Sekcja 7 (testy) — wszystkie testowe kroki w odpowiednich taskach.
   - Sekcja 8 (poza scope) — niezaadresowane (zgodnie z założeniem).

2. **Placeholder scan:** Po przejrzeniu nie ma „TBD"/„TODO"/„similar to". Komentarz „(usuń `_placeholder` i `shutil_module := ...` z bloku — to było pomyłkowe…)" w Task 3 jest instrukcją, nie placeholderem; zostawione celowo dla człowieka wykonującego plan.

3. **Type consistency:** `ArchiveJobState`, `ArchivedDeliveryState`, `pending_archive_jobs`, `archived_deliveries`, `pack_to_volumes`, `send_volumes`, `register_pending_archive_job`, `register_archived_delivery`, `volume_size_for`, `transliterate_to_ascii`, `compute_archive_basename`, `is_7z_available` — używane spójnie w taskach 5–14.

4. **Kolejność:** archive (T2,T3) przed archive_service (T6–T9); session_store (T5) przed archive_service (T6); archive_service przed handlers (T11, T12); runtime (T13) po handlers (potrzebuje `build_playlist_message` z parametrem). OK.

---

## Wybór trybu wykonania

Plan kompletny, zapisany w `docs/superpowers/plans/2026-05-02-playlist-zip-download-plan.md`. Dwie opcje wykonania:

1. **Subagent-driven (rekomendowane)** — dispatcher dispatches świeży subagent per task, review między taskami, szybka iteracja. Wymagany sub-skill: `superpowers:subagent-driven-development`.

2. **Inline execution** — wykonujemy taski sekwencyjnie w obecnej sesji z checkpointami. Wymagany sub-skill: `superpowers:executing-plans`.

Który tryb wybierasz?
