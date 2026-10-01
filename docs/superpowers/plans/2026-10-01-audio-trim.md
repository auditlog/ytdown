# Przycinanie audio według znaczników czasu — plan wdrożenia

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Użytkownik może wyciąć jeden lub kilka fragmentów audio (`1:30-4:45`, `2:15-`, `-5:00`, `1:00-2:00, 5:30-7:00`) spod wysłanego pliku, z pliku wysłanego do bota i z podcastów/Spotify przez „✂️ Pobierz i przytnij”.

**Architecture:** Wspólny parser zakresów (`bot/handlers/time_range.py`), silnik ffmpeg bez ponownego kodowania (`bot/services/audio_trim_service.py`), magazyn źródeł na dysku z wygasaniem po 24 h (`bot/services/trim_store.py`), jedno miejsce wysyłki audio z przyciskiem ✂️ (`bot/handlers/audio_delivery.py`) i przepływ Telegrama (`bot/handlers/trim_callbacks.py`). Istniejące ścieżki wysyłki pojedynczego audio przechodzą przez nowy helper; YouTube zachowuje ✂️ przed pobraniem przez yt-dlp.

**Tech Stack:** Python 3.12, `python-telegram-bot`, `pyrogram` (MTProto, opcjonalnie), `ffmpeg`/`ffprobe`, `pytest`. Bez nowych zależności.

**Spec:** `docs/superpowers/specs/2026-10-01-audio-trim-design.md`

## Global Constraints

- **Język:** komunikaty dla użytkownika po polsku; kod, komentarze, nazwy, commity po angielsku. Bez `Co-Authored-By` i wzmianek o AI.
- **Gałąź `develop`.** Nigdy nie commituj na `main`. **Nie pushuj** — PR #22 (`develop` → `main`) jest otwarty; o pushu decyduje użytkownik.
- **Formaty w magazynie:** `.mp3`, `.m4a`, `.flac`. Inne nie są zatrzymywane (wysyłka bez ✂️).
- **Stałe:** `TRIM_SOURCE_RETENTION_HOURS = 24`, `TRIM_PENDING_INPUT_TIMEOUT_MIN = 10`, `TRIM_MAX_RANGES = 10`, `TRIM_MIN_FREE_DISK_GB = 5`.
- **callback_data:** `trim_src_<token>` (token 11 znaków `[A-Za-z0-9_-]`), `trim_dl`, `trim_upload`, `trim_cancel`.
- **Komunikaty błędów zakresu wysyłane bez `parse_mode`** (zawierają tekst użytkownika).
- **ffmpeg:** cięcie `-c copy`, nigdy ponowne kodowanie fragmentu.
- **Rozmiary w MiB:** `os.path.getsize(path) / (1024 * 1024)`, jak w reszcie bota.
- **Testy:** `.venv/bin/python -m pytest <ścieżka> -q`. Przed pełnym przebiegiem `df -h /tmp` (mały tmpfs). Testy z prawdziwym ffmpeg mają `skipif` przy braku binarki.
- **Znana porażka lokalna:** `tests/test_poetry_config.py::TestProjectStructure::test_no_legacy_node_manifests_in_project_root` (lokalny `package-lock.json`) — nie naprawiać, nie liczyć jako regresji.

## Review Focus

1. **Tytuł z `_`, `*`, `[` w prompcie ✂️** — prompt idzie z `parse_mode="Markdown"`; nieescapowany tytuł zerwie wysyłkę. Test: Task 5 `test_prompt_escapes_markdown_in_title`.
2. **Wpis z nadmiarowymi separatorami i spacjami** (`1:00-2:00,` / `  1:00 -2:00 ;; 3:00-  `) — użytkownik oczekuje oczywistych zakresów, nie błędu. Test: Task 1 (parametryzacja `parse_time_ranges`).
3. **Bardzo długi tytuł (300 znaków)** — nazwa pliku fragmentu musi się zmieścić i kończyć rozszerzeniem. Test: Task 2 `test_fragment_filename_caps_long_titles`.
4. **Źródło usunięte przez agresywne sprzątanie, gdy prompt czeka** — wpisanie zakresu ma dać „Plik wygasł…”, nie wyjątek. Test: Task 5 `test_pending_input_reports_expired_source`.
5. **Wysyłka pada na fragmencie 2 z 3** — komunikat mówi, co się nie udało i ile poszło, pliki fragmentów są usunięte, źródło zostaje, jest `[✂️ Tnij dalej]`. Test: Task 5 `test_run_trim_job_stops_on_send_failure_and_keeps_source`.

---

## Struktura plików

| Plik | Odpowiedzialność |
|---|---|
| `bot/security_limits.py` | `+` stałe `TRIM_*` |
| `bot/handlers/time_range.py` | parser wielu zakresów, walidacja względem długości, formatowanie; zgodny wstecz `parse_time_range` |
| `bot/services/audio_trim_service.py` (nowy) | `probe_duration`, `cut_fragment`, nazwy i etykiety fragmentów |
| `bot/services/trim_store.py` (nowy) | magazyn `downloads/<chat>/trim_<token>/`, `meta.json`, wygasanie, sprzątanie |
| `bot/cleanup.py` | wywołanie `purge_expired_sources` w cyklu sprzątania |
| `bot/mtproto.py` | `send_audio_mtproto(..., performer, file_name, buttons)` |
| `bot/handlers/audio_delivery.py` (nowy) | `send_audio_file`, `send_audio_with_trim`, `max_sendable_audio_mb` |
| `bot/session_store.py`, `bot/session_context.py` | `PendingTrimInput`, pole `pending_trim` |
| `bot/jobs.py` | `JobKind += "trim"` |
| `bot/handlers/trim_callbacks.py` (nowy) | prompt, oczekujące wejście, zadanie cięcia, `trim_src_`/`trim_upload`/`trim_cancel`, `offer_trim_after_download` |
| `bot/handlers/transcript_prompt_handlers.py` | czyszczenie `pending_trim` przy ustawieniu polecenia transkrypcji |
| `bot/handlers/inbound_media.py` | hook `handle_pending_trim_input`; nowy parser dla ✂️ przed pobraniem |
| `bot/handlers/download_callbacks.py` | audio przez `send_audio_with_trim`; `trim_after` |
| `bot/handlers/spotify_callbacks.py` | audio przez `send_audio_with_trim`; `trim_after` |
| `bot/handlers/common_ui.py` | przycisk „✂️ Pobierz i przytnij” |
| `bot/handlers/inbound_audio.py` | przycisk „✂️ Przytnij” przy pliku wysłanym do bota |
| `bot/handlers/time_range_callbacks.py` | nowe przykłady w podpowiedzi |
| `bot/telegram_callbacks.py` | routing `trim_*`, `_handle_trim_download`, `trim_after` w wrapperach |
| `README.md` | sekcja „Przycinanie audio”, drzewo projektu |

Testy: nowe `tests/test_time_ranges.py`, `tests/test_audio_trim_service.py`, `tests/test_trim_store.py`, `tests/test_audio_delivery.py`, `tests/test_trim_callbacks.py`; zmiany w `tests/test_mtproto.py`, `tests/test_cleanup.py`, `tests/test_transcript_prompt_handlers.py`, `tests/test_inbound_media_handlers.py`, `tests/test_callback_download_handlers.py`, `tests/test_callback_transcription_handlers.py`, `tests/test_telegram_callbacks.py`.

---

### Task 1: Parser zakresów i stałe

**Files:**
- Modify: `bot/security_limits.py` (dopisz na końcu)
- Modify: `bot/handlers/time_range.py` (cała zawartość)
- Create: `tests/test_time_ranges.py`
- Regression: `tests/test_time_range.py` (bez zmian, musi przechodzić)

**Interfaces:**
- Produces:
  - `RangeSpec(start_sec: int | None, end_sec: int | None)` (frozen dataclass)
  - `ResolvedRange(start_sec: int, end_sec: int, open_end: bool)` (frozen dataclass)
  - `TimeRangeError(ValueError)` — `str(exc)` to polski komunikat
  - `EXAMPLES: str`
  - `format_timestamp(seconds: int) -> str`
  - `range_to_session_dict(start_sec: int, end_sec: int) -> dict`
  - `looks_like_time_ranges(text: str) -> bool`
  - `parse_time_ranges(text: str, *, max_ranges: int = TRIM_MAX_RANGES) -> list[RangeSpec]`
  - `resolve_ranges(specs: list[RangeSpec], duration_sec: int) -> list[ResolvedRange]`
  - `parse_time_range(text: str) -> dict | None` (bez zmian w kontrakcie)
  - stałe `TRIM_SOURCE_RETENTION_HOURS`, `TRIM_PENDING_INPUT_TIMEOUT_MIN`, `TRIM_MAX_RANGES`, `TRIM_MIN_FREE_DISK_GB` w `bot.security_limits`

- [ ] **Step 1: Write the failing test** — utwórz `tests/test_time_ranges.py`:

```python
"""Tests for multi-range parsing used by audio trimming and pre-download ranges."""

import pytest

from bot.handlers.time_range import (
    RangeSpec,
    ResolvedRange,
    TimeRangeError,
    format_timestamp,
    looks_like_time_ranges,
    parse_time_ranges,
    range_to_session_dict,
    resolve_ranges,
)

DURATION = 6130  # 1:42:10


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1:30-4:45", [RangeSpec(90, 285)]),
        ("90-285", [RangeSpec(90, 285)]),
        ("1:02:30-1:05:00", [RangeSpec(3750, 3900)]),
        ("102:30-103:00", [RangeSpec(6150, 6180)]),
        ("2:15-", [RangeSpec(135, None)]),
        ("-5:00", [RangeSpec(None, 300)]),
        ("1:30 - 4:45", [RangeSpec(90, 285)]),
        ("1:30–4:45", [RangeSpec(90, 285)]),
        ("1:30—4:45", [RangeSpec(90, 285)]),
        ("1:00-2:00, 5:30-7:00", [RangeSpec(60, 120), RangeSpec(330, 420)]),
        ("1:00-2:00;5:30-7:00", [RangeSpec(60, 120), RangeSpec(330, 420)]),
        ("1:00-2:00\n5:30-", [RangeSpec(60, 120), RangeSpec(330, None)]),
        ("1:00-2:00,", [RangeSpec(60, 120)]),
        ("  1:00 -2:00 ;; 3:00-  ", [RangeSpec(60, 120), RangeSpec(180, None)]),
        ("1:00-2:00, 1:30-2:30", [RangeSpec(60, 120), RangeSpec(90, 150)]),
    ],
)
def test_parse_time_ranges_accepts_supported_forms(text, expected):
    assert parse_time_ranges(text) == expected


@pytest.mark.parametrize(
    "text, message_part",
    [
        ("abc", "Nie rozumiem zakresu „abc”"),
        ("1:30", "Nie rozumiem zakresu „1:30”"),
        ("1-2-3", "Nie rozumiem zakresu „1-2-3”"),
        ("-", "Zakres „-” musi mieć początek albo koniec."),
        ("1:75-2:00", "„1:75” nie jest poprawnym czasem"),
        ("1:61:00-2:00:00", "„1:61:00” nie jest poprawnym czasem"),
        ("5:00-2:00", "W zakresie 5:00-2:00 początek musi być wcześniej niż koniec."),
        ("5:00-5:00", "W zakresie 5:00-5:00 początek musi być wcześniej niż koniec."),
        ("", "Podaj zakres."),
    ],
)
def test_parse_time_ranges_rejects_invalid_input(text, message_part):
    with pytest.raises(TimeRangeError) as exc_info:
        parse_time_ranges(text)
    assert message_part in str(exc_info.value)


def test_unparseable_message_lists_examples():
    with pytest.raises(TimeRangeError) as exc_info:
        parse_time_ranges("abc")
    assert "1:30-4:45 · 2:15- · -5:00 · 1:00-2:00, 5:30-7:00" in str(exc_info.value)


def test_parse_time_ranges_enforces_max_ranges():
    text = ", ".join(f"{i}-{i + 1}" for i in range(11))
    with pytest.raises(TimeRangeError) as exc_info:
        parse_time_ranges(text)
    assert str(exc_info.value) == "Możesz podać maksymalnie 10 fragmentów naraz (podano 11)."


@pytest.mark.parametrize(
    "specs, expected",
    [
        ([RangeSpec(90, 285)], [ResolvedRange(90, 285, False)]),
        ([RangeSpec(135, None)], [ResolvedRange(135, DURATION, True)]),
        ([RangeSpec(None, 300)], [ResolvedRange(0, 300, False)]),
        ([RangeSpec(0, DURATION - 1)], [ResolvedRange(0, DURATION - 1, False)]),
        ([RangeSpec(60, DURATION)], [ResolvedRange(60, DURATION, False)]),
    ],
)
def test_resolve_ranges_against_duration(specs, expected):
    assert resolve_ranges(specs, DURATION) == expected


@pytest.mark.parametrize(
    "specs, message",
    [
        ([RangeSpec(6200, None)], "Początek 1:43:20 jest poza plikiem (długość 1:42:10)."),
        (
            [RangeSpec(6000, 6300)],
            "Koniec 1:45:00 jest poza plikiem (długość 1:42:10). "
            "Wpisz „1:40:00-”, żeby ciąć do końca.",
        ),
        ([RangeSpec(0, DURATION)], "Zakres 0:00-1:42:10 obejmuje cały plik — nie ma czego ciąć."),
        ([RangeSpec(0, None)], "Zakres 0:00-1:42:10 obejmuje cały plik — nie ma czego ciąć."),
        (
            [RangeSpec(10, 20), RangeSpec(6000, 6300)],
            "Koniec 1:45:00 jest poza plikiem (długość 1:42:10). "
            "Wpisz „1:40:00-”, żeby ciąć do końca.",
        ),
    ],
)
def test_resolve_ranges_rejects_out_of_bounds(specs, message):
    with pytest.raises(TimeRangeError) as exc_info:
        resolve_ranges(specs, DURATION)
    assert str(exc_info.value) == message


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1:30-4:45", True),
        ("2:15-", True),
        ("-5:00", True),
        ("1:00-2:00, 5:30-7:00", True),
        ("1:30–4:45", True),
        ("12345678", False),
        ("https://youtube.com/watch?v=x", False),
        ("hello", False),
        ("-", False),
        ("", False),
    ],
)
def test_looks_like_time_ranges(text, expected):
    assert looks_like_time_ranges(text) is expected


def test_format_timestamp():
    assert format_timestamp(0) == "0:00"
    assert format_timestamp(285) == "4:45"
    assert format_timestamp(3599) == "59:59"
    assert format_timestamp(3600) == "1:00:00"
    assert format_timestamp(6130) == "1:42:10"


def test_range_to_session_dict_matches_legacy_shape():
    assert range_to_session_dict(10, 20) == {
        "start": "0:10",
        "end": "0:20",
        "start_sec": 10,
        "end_sec": 20,
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_time_ranges.py -q`
Expected: FAIL — `ImportError: cannot import name 'RangeSpec'`

- [ ] **Step 3: Write minimal implementation**

Dopisz na końcu `bot/security_limits.py`:

```python

# Audio trimming. See bot/services/trim_store.py and bot/handlers/trim_callbacks.py.
# Sources stay on disk this long so the ✂️ button under a sent audio keeps working.
TRIM_SOURCE_RETENTION_HOURS = 24
# How long the bot treats the next text message as trim ranges after ✂️ is pressed.
TRIM_PENDING_INPUT_TIMEOUT_MIN = 10
TRIM_MAX_RANGES = 10
# Matches the "low disk" threshold in cleanup.monitor_disk_space (aggressive cleanup below 5 GB).
TRIM_MIN_FREE_DISK_GB = 5
```

Zastąp całą zawartość `bot/handlers/time_range.py`:

```python
"""Shared parsing helpers for chat-provided time ranges.

Used by the pre-download range flow (bot/handlers/inbound_media.py) and the
audio trimming flow (bot/handlers/trim_callbacks.py). Messages carried by
TimeRangeError are user-facing Polish text sent without parse_mode, because
they echo user input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bot.security_limits import TRIM_MAX_RANGES

# S, M:SS (minutes unbounded, e.g. 102:30) or H:MM:SS.
_TIMESTAMP_RE = re.compile(r"^\d+(?::\d{2}){0,2}$")
# Phones often autocorrect "-" into an en or em dash.
_DASH_RE = re.compile(r"\s*[-–—]\s*")
_SEPARATOR_RE = re.compile(r"[,;\n]")
_RANGE_LIKE_RE = re.compile(r"^[\d:\s,;\-–—]+$")

EXAMPLES = "Przykłady: 1:30-4:45 · 2:15- · -5:00 · 1:00-2:00, 5:30-7:00"


@dataclass(frozen=True)
class RangeSpec:
    """One parsed range; None marks an open end."""

    start_sec: int | None  # None = from the beginning
    end_sec: int | None  # None = to the end


@dataclass(frozen=True)
class ResolvedRange:
    """A range validated against a concrete duration."""

    start_sec: int
    end_sec: int  # resolved against the duration; used for labels and checks
    open_end: bool  # True for "2:15-": ffmpeg cuts to EOF without -t


class TimeRangeError(ValueError):
    """Invalid range input; str(exc) is a Polish, user-facing message."""


def format_timestamp(seconds: int) -> str:
    """Format seconds as M:SS below one hour and H:MM:SS above."""

    if seconds >= 3600:
        return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


def range_to_session_dict(start_sec: int, end_sec: int) -> dict:
    """Build the session shape consumed by the downloader (yt-dlp sections)."""

    return {
        "start": format_timestamp(start_sec),
        "end": format_timestamp(end_sec),
        "start_sec": start_sec,
        "end_sec": end_sec,
    }


def looks_like_time_ranges(text: str) -> bool:
    """Cheap pre-check so plain chat text and 8-digit PINs keep their old handling."""

    stripped = (text or "").strip()
    return (
        bool(_RANGE_LIKE_RE.match(stripped))
        and any(dash in stripped for dash in "-–—")
        and any(char.isdigit() for char in stripped)
    )


def _parse_timestamp(value: str, fragment: str) -> int:
    if not _TIMESTAMP_RE.match(value):
        raise TimeRangeError(f"Nie rozumiem zakresu „{fragment}”. {EXAMPLES}")
    parts = [int(part) for part in value.split(":")]
    # The first component is unbounded ("90" seconds, "102:30" minutes);
    # every later one is minutes or seconds and must stay below 60.
    if any(part >= 60 for part in parts[1:]):
        raise TimeRangeError(
            f"„{value}” nie jest poprawnym czasem — minuty i sekundy muszą być mniejsze niż 60."
        )
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def parse_time_ranges(text: str, *, max_ranges: int = TRIM_MAX_RANGES) -> list[RangeSpec]:
    """Parse comma/semicolon/newline separated ranges; raise TimeRangeError on bad input."""

    fragments = [part.strip() for part in _SEPARATOR_RE.split(text or "")]
    fragments = [part for part in fragments if part]
    if not fragments:
        raise TimeRangeError(f"Podaj zakres. {EXAMPLES}")
    if len(fragments) > max_ranges:
        raise TimeRangeError(
            f"Możesz podać maksymalnie {max_ranges} fragmentów naraz (podano {len(fragments)})."
        )

    specs: list[RangeSpec] = []
    for fragment in fragments:
        bounds = _DASH_RE.split(fragment)
        if len(bounds) != 2:
            raise TimeRangeError(f"Nie rozumiem zakresu „{fragment}”. {EXAMPLES}")
        raw_start, raw_end = (bound.strip() for bound in bounds)
        if not raw_start and not raw_end:
            raise TimeRangeError(f"Zakres „{fragment}” musi mieć początek albo koniec.")
        start = _parse_timestamp(raw_start, fragment) if raw_start else None
        end = _parse_timestamp(raw_end, fragment) if raw_end else None
        if start is not None and end is not None and start >= end:
            raise TimeRangeError(
                f"W zakresie {format_timestamp(start)}-{format_timestamp(end)} "
                "początek musi być wcześniej niż koniec."
            )
        specs.append(RangeSpec(start, end))
    return specs


def resolve_ranges(specs: list[RangeSpec], duration_sec: int) -> list[ResolvedRange]:
    """Validate parsed ranges against a file duration (whole seconds)."""

    length = format_timestamp(duration_sec)
    resolved: list[ResolvedRange] = []
    for spec in specs:
        start = spec.start_sec or 0
        open_end = spec.end_sec is None
        end = duration_sec if open_end else spec.end_sec
        if start >= duration_sec:
            raise TimeRangeError(
                f"Początek {format_timestamp(start)} jest poza plikiem (długość {length})."
            )
        if end > duration_sec:
            raise TimeRangeError(
                f"Koniec {format_timestamp(end)} jest poza plikiem (długość {length}). "
                f"Wpisz „{format_timestamp(start)}-”, żeby ciąć do końca."
            )
        if start == 0 and end == duration_sec:
            raise TimeRangeError(
                f"Zakres {format_timestamp(start)}-{format_timestamp(end)} "
                "obejmuje cały plik — nie ma czego ciąć."
            )
        resolved.append(ResolvedRange(start, end, open_end))
    return resolved


def parse_time_range(text: str) -> dict | None:
    """Parse one closed range; legacy contract kept for existing callers and tests."""

    try:
        specs = parse_time_ranges(text, max_ranges=1)
    except TimeRangeError:
        return None
    spec = specs[0]
    if spec.start_sec is None or spec.end_sec is None:
        return None
    return range_to_session_dict(spec.start_sec, spec.end_sec)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_time_ranges.py tests/test_time_range.py -q`
Expected: PASS (wszystkie, w tym stare testy `parse_time_range`, także `"12345678" is None`)

- [ ] **Step 5: Commit**

```bash
git add bot/security_limits.py bot/handlers/time_range.py tests/test_time_ranges.py
git commit -m "Parse multiple and open-ended time ranges"
```

---

### Task 2: Silnik cięcia ffmpeg

**Files:**
- Create: `bot/services/audio_trim_service.py`
- Create: `tests/test_audio_trim_service.py`

**Interfaces:**
- Consumes: `ResolvedRange`, `format_timestamp` (Task 1); `sanitize_filename` z `bot.downloader_validation`; `FFMPEG_TIMEOUT` z `bot.security_limits`.
- Produces:
  - `AudioTrimError(RuntimeError)`
  - `async _run(cmd: list[str], *, cancellation=None, timeout: int = FFMPEG_TIMEOUT) -> tuple[int, bytes, bytes]`
  - `async probe_duration(path: Path) -> float`
  - `fragment_label(fragment: ResolvedRange) -> str` — np. `"12:00–15:30"` (półpauza)
  - `fragment_filename(title: str, fragment: ResolvedRange, ext: str) -> str`
  - `async cut_fragment(source: Path, fragment: ResolvedRange, dest: Path, *, title_tag: str, cancellation=None) -> Path`

- [ ] **Step 1: Write the failing test** — utwórz `tests/test_audio_trim_service.py`:

```python
"""Tests for ffmpeg-based fragment cutting."""

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.handlers.time_range import ResolvedRange
from bot.services import audio_trim_service as trim

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)

_ENCODERS = {
    ".mp3": ["-c:a", "libmp3lame", "-q:a", "5"],
    ".m4a": ["-c:a", "aac", "-b:a", "96k"],
    ".flac": ["-c:a", "flac"],
}


def _make_tone(path: Path, seconds: int = 10) -> Path:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f=440:d={seconds}",
         *_ENCODERS[path.suffix], str(path)],
        check=True,
    )
    return path


def _probe_json(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:format_tags=title:stream=codec_type", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(out)


def test_fragment_label_and_filename():
    fragment = ResolvedRange(720, 930, False)
    assert trim.fragment_label(fragment) == "12:00–15:30"
    assert trim.fragment_filename("Podcast: #120", fragment, ".mp3") == "Podcast- #120 [12-00–15-30].mp3"


def test_fragment_filename_caps_long_titles():
    name = trim.fragment_filename("x" * 300, ResolvedRange(0, 5, False), ".mp3")
    assert name.endswith(".mp3")
    assert len(name) <= 204


def test_probe_duration_reports_missing_binary(monkeypatch, tmp_path):
    async def missing(*args, **kwargs):
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr(trim.asyncio, "create_subprocess_exec", missing)
    with pytest.raises(trim.AudioTrimError):
        asyncio.run(trim.probe_duration(tmp_path / "x.mp3"))


def test_run_attaches_and_detaches_process(monkeypatch):
    seen = {}
    cancellation = SimpleNamespace(process=None)

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            seen["attached"] = cancellation.process is self
            return b"", b""

    async def fake_exec(*cmd, **kwargs):
        return FakeProcess()

    monkeypatch.setattr(trim.asyncio, "create_subprocess_exec", fake_exec)
    asyncio.run(trim._run(["ffmpeg"], cancellation=cancellation))
    assert seen["attached"] is True
    assert cancellation.process is None


@needs_ffmpeg
@pytest.mark.parametrize("ext", [".mp3", ".m4a", ".flac"])
def test_cut_fragment_produces_requested_length(tmp_path, ext):
    source = _make_tone(tmp_path / f"source{ext}")
    dest = tmp_path / f"out{ext}"
    asyncio.run(trim.cut_fragment(source, ResolvedRange(2, 5, False), dest, title_tag="Tone [0:02–0:05]"))
    assert asyncio.run(trim.probe_duration(dest)) == pytest.approx(3.0, abs=0.15)


@needs_ffmpeg
def test_cut_fragment_open_end_runs_to_eof(tmp_path):
    source = _make_tone(tmp_path / "source.mp3")
    dest = tmp_path / "out.mp3"
    asyncio.run(trim.cut_fragment(source, ResolvedRange(7, 10, True), dest, title_tag="Tone"))
    assert asyncio.run(trim.probe_duration(dest)) == pytest.approx(3.0, abs=0.15)


@needs_ffmpeg
def test_cut_fragment_writes_title_tag(tmp_path):
    source = _make_tone(tmp_path / "source.mp3")
    dest = tmp_path / "out.mp3"
    asyncio.run(trim.cut_fragment(source, ResolvedRange(1, 2, False), dest, title_tag="Tone [0:01–0:02]"))
    assert _probe_json(dest)["format"]["tags"]["title"] == "Tone [0:01–0:02]"


@needs_ffmpeg
def test_cut_fragment_keeps_mp3_cover(tmp_path):
    tone = _make_tone(tmp_path / "tone.mp3")
    cover = tmp_path / "cover.png"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=16x16",
         "-frames:v", "1", str(cover)],
        check=True,
    )
    source = tmp_path / "source.mp3"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(tone), "-i", str(cover), "-map", "0:a",
         "-map", "1:v", "-c", "copy", "-id3v2_version", "3", "-disposition:v", "attached_pic",
         str(source)],
        check=True,
    )
    dest = tmp_path / "out.mp3"
    asyncio.run(trim.cut_fragment(source, ResolvedRange(1, 4, False), dest, title_tag="Tone"))
    codec_types = [stream["codec_type"] for stream in _probe_json(dest)["streams"]]
    assert codec_types.count("video") == 1


@needs_ffmpeg
def test_cut_fragment_retries_without_cover_when_first_attempt_fails(tmp_path, monkeypatch):
    calls = []
    real_run = trim._run

    async def flaky_run(cmd, **kwargs):
        calls.append(cmd)
        if "0:v:0?" in cmd:
            return 1, b"", b"Could not write header"
        return await real_run(cmd, **kwargs)

    monkeypatch.setattr(trim, "_run", flaky_run)
    source = _make_tone(tmp_path / "source.m4a")
    dest = tmp_path / "out.m4a"
    asyncio.run(trim.cut_fragment(source, ResolvedRange(1, 3, False), dest, title_tag="Tone"))
    assert len(calls) == 2
    assert "0:v:0?" not in calls[1]
    assert dest.exists()


@needs_ffmpeg
def test_cut_fragment_raises_on_failure(tmp_path):
    bogus = tmp_path / "bogus.mp3"
    bogus.write_bytes(b"not audio")
    with pytest.raises(trim.AudioTrimError):
        asyncio.run(trim.cut_fragment(bogus, ResolvedRange(1, 2, False), tmp_path / "out.mp3", title_tag="x"))


@needs_ffmpeg
def test_probe_duration_raises_on_garbage(tmp_path):
    bogus = tmp_path / "bogus.mp3"
    bogus.write_bytes(b"not audio")
    with pytest.raises(trim.AudioTrimError):
        asyncio.run(trim.probe_duration(bogus))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_audio_trim_service.py -q`
Expected: FAIL — `ImportError: cannot import name 'audio_trim_service'`

- [ ] **Step 3: Write minimal implementation** — utwórz `bot/services/audio_trim_service.py`:

```python
"""Cut audio fragments with ffmpeg stream copy (no re-encoding).

Precision is one audio frame (~25 ms for MP3). Verified on 2026-10-01 against
a 20-minute VBR MP3 encoded like yt-dlp's output (libmp3lame -q:a 5): input
seeking (-ss before -i) landed within 25 ms of the requested time.

See also: bot/handlers/trim_callbacks.py (caller), bot/handlers/time_range.py.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from bot.downloader_validation import sanitize_filename
from bot.handlers.time_range import ResolvedRange, format_timestamp
from bot.security_limits import FFMPEG_TIMEOUT


class AudioTrimError(RuntimeError):
    """ffmpeg/ffprobe failure; the message is technical and meant for logs."""


def _tail(stderr: bytes) -> str:
    return stderr.decode(errors="replace")[-500:]


def _is_cancelled(cancellation) -> bool:
    return cancellation is not None and cancellation.event.is_set()


async def _run(
    cmd: list[str],
    *,
    cancellation=None,
    timeout: int = FFMPEG_TIMEOUT,
) -> tuple[int, bytes, bytes]:
    """Run a subprocess and expose it to /stop through ``cancellation.process``."""

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise AudioTrimError(f"{cmd[0]} unavailable: {exc}") from exc

    if cancellation is not None:
        cancellation.process = process
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.wait()
        raise AudioTrimError(f"{cmd[0]} timed out after {timeout}s") from exc
    finally:
        if cancellation is not None and cancellation.process is process:
            cancellation.process = None
    return process.returncode, stdout, stderr


async def probe_duration(path: Path) -> float:
    """Return the media duration in seconds as reported by ffprobe."""

    code, stdout, stderr = await _run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
    )
    name = Path(path).name
    if code != 0:
        raise AudioTrimError(f"ffprobe failed for {name}: {_tail(stderr)}")
    try:
        duration = float(stdout.decode().strip())
    except ValueError as exc:
        raise AudioTrimError(f"ffprobe returned no duration for {name}") from exc
    if duration <= 0:
        raise AudioTrimError(f"ffprobe returned a non-positive duration for {name}")
    return duration


def fragment_label(fragment: ResolvedRange) -> str:
    """Human label used in captions, tags and status messages, e.g. 12:00–15:30."""

    return f"{format_timestamp(fragment.start_sec)}–{format_timestamp(fragment.end_sec)}"


def fragment_filename(title: str, fragment: ResolvedRange, ext: str) -> str:
    """Filesystem-safe fragment name; sanitize_filename caps the stem at 200 chars."""

    return sanitize_filename(f"{title} [{fragment_label(fragment)}]") + ext


async def cut_fragment(
    source: Path,
    fragment: ResolvedRange,
    dest: Path,
    *,
    title_tag: str,
    cancellation=None,
) -> Path:
    """Copy one fragment of ``source`` into ``dest`` without re-encoding."""

    head = ["ffmpeg", "-v", "error", "-y", "-ss", str(fragment.start_sec), "-i", str(source)]
    if not fragment.open_end:
        head += ["-t", str(fragment.end_sec - fragment.start_sec)]
    tail = ["-c", "copy", "-map_metadata", "0", "-metadata", f"title={title_tag}", str(dest)]

    # Keep embedded cover art when present. Some containers (M4A) reject a
    # copied cover stream, so retry once with the audio stream only.
    code, _, stderr = await _run(
        head + ["-map", "0:a:0", "-map", "0:v:0?"] + tail,
        cancellation=cancellation,
    )
    if code != 0 and not _is_cancelled(cancellation):
        logging.warning("ffmpeg cut with cover failed, retrying audio-only: %s", _tail(stderr))
        code, _, stderr = await _run(head + ["-map", "0:a:0"] + tail, cancellation=cancellation)
    if code != 0:
        raise AudioTrimError(f"ffmpeg cut failed: {_tail(stderr)}")
    return dest
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_audio_trim_service.py -q`
Expected: PASS (na maszynie z ffmpeg 13 testów; bez ffmpeg 4 przechodzą, reszta SKIPPED)

- [ ] **Step 5: Commit**

```bash
git add bot/services/audio_trim_service.py tests/test_audio_trim_service.py
git commit -m "Add ffmpeg stream-copy audio fragment cutter"
```

---

### Task 3: Magazyn źródeł i sprzątanie

**Files:**
- Create: `bot/services/trim_store.py`
- Modify: `bot/cleanup.py` (import + pętla w `periodic_cleanup`, linie ~325–327)
- Create: `tests/test_trim_store.py`
- Modify: `tests/test_cleanup.py` (dopisz test na końcu)

**Interfaces:**
- Consumes: `TRIM_SOURCE_RETENTION_HOURS`, `TRIM_MIN_FREE_DISK_GB` (Task 1); `DOWNLOAD_PATH` z `bot.config`.
- Produces:
  - `TrimSource(token: str, chat_id: int, path: Path, title: str, performer: str | None, duration_sec: int, created_at: datetime)` (frozen) + właściwość `workspace -> Path`
  - `TRIM_DIR_PREFIX = "trim_"`, `SUPPORTED_EXTENSIONS = (".mp3", ".m4a", ".flac")`
  - `has_room_for_sources() -> bool`
  - `retain_source(chat_id: int, file_path, *, title: str, performer: str | None, duration_sec: int, link: bool = False) -> TrimSource | None`
  - `load_source(chat_id: int, token: str | None) -> TrimSource | None`
  - `discard_source(source: TrimSource) -> None`
  - `expires_at(source: TrimSource) -> datetime`
  - `purge_expired_sources(chat_dir: Path) -> int`
  - `cleanup._purge_chat_workspaces(download_root: Path) -> None`

- [ ] **Step 1: Write the failing test** — utwórz `tests/test_trim_store.py`:

```python
"""Tests for the on-disk trim source store."""

import json
import os
from collections import namedtuple
from datetime import UTC, datetime, timedelta

import pytest

from bot.services import trim_store

_Usage = namedtuple("usage", "total used free")


@pytest.fixture
def store_root(tmp_path, monkeypatch):
    root = tmp_path / "downloads"
    root.mkdir()
    monkeypatch.setattr(trim_store, "DOWNLOAD_PATH", str(root))
    monkeypatch.setattr(
        trim_store.shutil, "disk_usage", lambda _path: _Usage(100 * 1024**3, 0, 50 * 1024**3)
    )
    return root


def _audio(tmp_path, name="episode.mp3", payload=b"ID3 audio"):
    path = tmp_path / name
    path.write_bytes(payload)
    return path


def _retain(tmp_path, name="episode.mp3", **overrides):
    kwargs = {"title": "Podcast", "performer": None, "duration_sec": 600}
    kwargs.update(overrides)
    return trim_store.retain_source(42, _audio(tmp_path, name), **kwargs)


def test_retain_moves_file_and_writes_meta(store_root, tmp_path):
    original = _audio(tmp_path)
    source = trim_store.retain_source(
        42, original, title="Podcast #120", performer="Host", duration_sec=6130
    )
    assert source is not None
    assert not original.exists()
    assert source.path == store_root / "42" / f"trim_{source.token}" / "source.mp3"
    assert source.workspace == source.path.parent
    assert source.path.read_bytes() == b"ID3 audio"
    meta = json.loads((source.workspace / "meta.json").read_text(encoding="utf-8"))
    assert meta["title"] == "Podcast #120"
    assert meta["performer"] == "Host"
    assert meta["duration_sec"] == 6130
    assert len(source.token) == 11


def test_retain_with_link_keeps_original(store_root, tmp_path):
    original = _audio(tmp_path)
    source = trim_store.retain_source(42, original, title="Upload", performer=None, duration_sec=60, link=True)
    assert original.exists()
    assert source.path.read_bytes() == original.read_bytes()


def test_retain_link_falls_back_to_copy(store_root, tmp_path, monkeypatch):
    original = _audio(tmp_path)

    def no_links(*_args):
        raise OSError("cross-device link")

    monkeypatch.setattr(trim_store.os, "link", no_links)
    source = trim_store.retain_source(42, original, title="Upload", performer=None, duration_sec=60, link=True)
    assert source is not None
    assert original.exists()
    assert source.path.exists()


def test_retain_rejects_unsupported_extension(store_root, tmp_path):
    original = _audio(tmp_path, name="voice.ogg")
    assert trim_store.retain_source(42, original, title="Voice", performer=None, duration_sec=5) is None
    assert original.exists()


def test_retain_rejects_when_disk_is_low(store_root, tmp_path, monkeypatch):
    monkeypatch.setattr(trim_store.shutil, "disk_usage", lambda _p: _Usage(100 * 1024**3, 0, 4 * 1024**3))
    original = _audio(tmp_path)
    assert trim_store.retain_source(42, original, title="X", performer=None, duration_sec=5) is None
    assert original.exists()


def test_load_source_round_trips_after_restart(store_root, tmp_path):
    retained = _retain(tmp_path, performer="Host")
    assert trim_store.load_source(42, retained.token) == retained


def test_load_source_rejects_other_chat(store_root, tmp_path):
    retained = _retain(tmp_path)
    assert trim_store.load_source(43, retained.token) is None


@pytest.mark.parametrize("token", ["../../etc", "short", "a" * 12, "", None, "abc/def_ghi"])
def test_load_source_rejects_malformed_tokens(store_root, token):
    assert trim_store.load_source(42, token) is None


def test_load_source_expires_after_retention(store_root, tmp_path, monkeypatch):
    retained = _retain(tmp_path)
    later = retained.created_at + timedelta(hours=24, seconds=1)
    monkeypatch.setattr(trim_store, "_now", lambda: later)
    assert trim_store.load_source(42, retained.token) is None


def test_load_source_returns_none_when_file_was_cleaned(store_root, tmp_path):
    retained = _retain(tmp_path)
    retained.path.unlink()
    assert trim_store.load_source(42, retained.token) is None


def test_expires_at_is_24h_after_creation(store_root, tmp_path):
    retained = _retain(tmp_path)
    assert trim_store.expires_at(retained) - retained.created_at == timedelta(hours=24)


def test_discard_source_removes_workspace(store_root, tmp_path):
    retained = _retain(tmp_path)
    trim_store.discard_source(retained)
    assert not retained.workspace.exists()


def test_purge_expired_sources_keeps_live_and_removes_dead(store_root, tmp_path):
    live = _retain(tmp_path, "a.mp3", title="Live")
    expired = _retain(tmp_path, "b.mp3", title="Old")
    orphan = _retain(tmp_path, "c.mp3", title="Orphan")
    orphan.path.unlink()  # what cleanup_old_files does to files older than 24 h

    meta_path = expired.workspace / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["created_at"] = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    # Age every workspace past the creation grace period.
    old = (datetime.now() - timedelta(hours=1)).timestamp()
    for source in (live, expired, orphan):
        os.utime(source.workspace, (old, old))

    assert trim_store.purge_expired_sources(store_root / "42") == 2
    assert live.workspace.exists()
    assert not expired.workspace.exists()
    assert not orphan.workspace.exists()


def test_purge_skips_young_workspace_without_meta(store_root):
    young = store_root / "42" / "trim_AAAAAAAAAAA"
    young.mkdir(parents=True)
    assert trim_store.purge_expired_sources(store_root / "42") == 0
    assert young.exists()


def test_purge_ignores_other_directories(store_root):
    other = store_root / "42" / "pl_something"
    other.mkdir(parents=True)
    assert trim_store.purge_expired_sources(store_root / "42") == 0
    assert other.exists()
```

Dopisz na końcu `tests/test_cleanup.py`:

```python


def test_purge_chat_workspaces_runs_trim_purge_per_chat(tmp_path, monkeypatch):
    from bot import cleanup

    (tmp_path / "42").mkdir()
    (tmp_path / "not_a_dir.txt").write_text("x")
    seen = []
    monkeypatch.setattr(cleanup, "purge_expired_sources", lambda chat_dir: seen.append(chat_dir.name) or 0)
    monkeypatch.setattr(cleanup, "_purge_archive_workspaces", lambda *args, **kwargs: 0)

    cleanup._purge_chat_workspaces(tmp_path)

    assert seen == ["42"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_trim_store.py tests/test_cleanup.py -q`
Expected: FAIL — `ImportError: cannot import name 'trim_store'` oraz `AttributeError: ... '_purge_chat_workspaces'`

- [ ] **Step 3: Write minimal implementation** — utwórz `bot/services/trim_store.py`:

```python
"""On-disk store of audio sources that can be trimmed for 24 hours.

Layout: <DOWNLOAD_PATH>/<chat_id>/trim_<token>/source.<ext> + meta.json.
State lives on disk instead of SessionStore so ✂️ buttons keep working after
a bot restart. Expiry is enforced here and by bot/cleanup.py, whose
cleanup_old_files also deletes any file older than 24 h (6 h when disk is low).

See also: bot/handlers/audio_delivery.py, bot/handlers/trim_callbacks.py.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bot.config import DOWNLOAD_PATH
from bot.security_limits import TRIM_MIN_FREE_DISK_GB, TRIM_SOURCE_RETENTION_HOURS

TRIM_DIR_PREFIX = "trim_"
SUPPORTED_EXTENSIONS = (".mp3", ".m4a", ".flac")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{11}")
_META_NAME = "meta.json"
# retain_source creates the directory a moment before meta.json and the
# source file exist; never purge a workspace that young.
_PURGE_GRACE_SEC = 300


@dataclass(frozen=True)
class TrimSource:
    token: str
    chat_id: int
    path: Path
    title: str
    performer: str | None
    duration_sec: int
    created_at: datetime  # timezone-aware UTC

    @property
    def workspace(self) -> Path:
        return self.path.parent


def _now() -> datetime:
    return datetime.now(UTC)


def _chat_dir(chat_id: int) -> Path:
    return Path(DOWNLOAD_PATH) / str(chat_id)


def expires_at(source: TrimSource) -> datetime:
    return source.created_at + timedelta(hours=TRIM_SOURCE_RETENTION_HOURS)


def has_room_for_sources() -> bool:
    """False when keeping another source would push the disk into the low zone."""

    root = Path(DOWNLOAD_PATH)
    try:
        root.mkdir(parents=True, exist_ok=True)
        free_gb = shutil.disk_usage(root).free / (1024 ** 3)
    except OSError as exc:
        logging.warning("Cannot check free disk space for trim sources: %s", exc)
        return False
    return free_gb >= TRIM_MIN_FREE_DISK_GB


def _link_or_copy(source: Path, dest: Path) -> None:
    try:
        os.link(source, dest)
    except OSError:
        shutil.copy2(source, dest)


def retain_source(
    chat_id: int,
    file_path,
    *,
    title: str,
    performer: str | None,
    duration_sec: int,
    link: bool = False,
) -> TrimSource | None:
    """Move (or hardlink) a file into the store; None when it cannot be kept.

    Never raises for expected refusals (format, disk); the caller then falls
    back to its previous behaviour. On failure the original file is untouched.
    """

    source_path = Path(file_path)
    ext = source_path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        logging.info("Trim store skipped %s: unsupported extension", source_path.name)
        return None
    if not has_room_for_sources():
        logging.warning(
            "Trim store skipped %s: free disk below %s GB", source_path.name, TRIM_MIN_FREE_DISK_GB
        )
        return None

    token = secrets.token_urlsafe(8)
    workspace = _chat_dir(chat_id) / f"{TRIM_DIR_PREFIX}{token}"
    dest = workspace / f"source{ext}"
    created_at = _now()
    meta = {
        "title": title,
        "performer": performer,
        "duration_sec": int(duration_sec),
        "created_at": created_at.isoformat(),
        "file": dest.name,
    }
    try:
        workspace.mkdir(parents=True)
        # meta.json goes first so a failed move leaves the original in place.
        (workspace / _META_NAME).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        if link:
            _link_or_copy(source_path, dest)
        else:
            shutil.move(str(source_path), str(dest))
    except OSError as exc:
        logging.error("Could not retain trim source %s: %s", source_path, exc)
        shutil.rmtree(workspace, ignore_errors=True)
        return None

    logging.info(
        "Trim source retained: chat=%d token=%s size=%.1f MB",
        chat_id, token, dest.stat().st_size / (1024 * 1024),
    )
    return TrimSource(token, chat_id, dest, title, performer, int(duration_sec), created_at)


def _read_source(workspace: Path, chat_id: int, token: str) -> TrimSource | None:
    try:
        meta = json.loads((workspace / _META_NAME).read_text(encoding="utf-8"))
        created_at = datetime.fromisoformat(meta["created_at"])
        path = workspace / Path(str(meta["file"])).name
        title = str(meta["title"])
        performer = meta.get("performer") or None
        duration_sec = int(meta["duration_sec"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    source = TrimSource(token, chat_id, path, title, performer, duration_sec, created_at)
    if _now() >= expires_at(source) or not path.is_file():
        return None
    return source


def load_source(chat_id: int, token: str | None) -> TrimSource | None:
    """Return a live source for this chat, or None when unknown or expired."""

    if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
        return None
    return _read_source(_chat_dir(chat_id) / f"{TRIM_DIR_PREFIX}{token}", chat_id, token)


def discard_source(source: TrimSource) -> None:
    shutil.rmtree(source.workspace, ignore_errors=True)


def purge_expired_sources(chat_dir: Path) -> int:
    """Remove trim workspaces that expired or lost their source file."""

    chat_dir = Path(chat_dir)
    try:
        chat_id = int(chat_dir.name)
    except ValueError:
        return 0
    if not chat_dir.is_dir():
        return 0

    removed = 0
    now = time.time()
    for entry in chat_dir.iterdir():
        if not entry.is_dir() or not entry.name.startswith(TRIM_DIR_PREFIX):
            continue
        token = entry.name.removeprefix(TRIM_DIR_PREFIX)
        if _read_source(entry, chat_id, token) is not None:
            continue
        try:
            age = now - entry.stat().st_mtime
        except OSError:
            continue
        if age < _PURGE_GRACE_SEC:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        removed += 1
        logging.info("Removed expired trim source: %s", entry)
    return removed
```

W `bot/cleanup.py`:

1. Pod `from bot.security_limits import JOB_DEAD_AGE_HOURS, PLAYLIST_ARCHIVE_RETENTION_MIN` dodaj:

```python
from bot.services.trim_store import purge_expired_sources
```

2. Nad `def periodic_cleanup():` dodaj funkcję:

```python
def _purge_chat_workspaces(download_root: Path) -> None:
    """Per-chat cleanup of archive workspaces and expired trim sources."""

    for chat_dir in download_root.iterdir():
        if chat_dir.is_dir():
            _purge_archive_workspaces(chat_dir, PLAYLIST_ARCHIVE_RETENTION_MIN)
            purge_expired_sources(chat_dir)
```

3. W `periodic_cleanup` zastąp:

```python
            for chat_dir in Path(DOWNLOAD_PATH).iterdir():
                if chat_dir.is_dir():
                    _purge_archive_workspaces(chat_dir, PLAYLIST_ARCHIVE_RETENTION_MIN)
```

przez:

```python
            _purge_chat_workspaces(Path(DOWNLOAD_PATH))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_trim_store.py tests/test_cleanup.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bot/services/trim_store.py bot/cleanup.py tests/test_trim_store.py tests/test_cleanup.py
git commit -m "Add on-disk trim source store with 24h expiry"
```

---

### Task 4: Wysyłka audio z przyciskiem ✂️ (Bot API i MTProto)

**Files:**
- Modify: `bot/mtproto.py` (`send_audio_mtproto`, linie ~171–245)
- Create: `bot/handlers/audio_delivery.py`
- Modify: `tests/test_mtproto.py` (dopisz testy w klasie `TestSendAudioMtproto`)
- Create: `tests/test_audio_delivery.py`

**Interfaces:**
- Consumes: `probe_duration`, `AudioTrimError` (Task 2); `TrimSource`, `retain_source`, `discard_source` (Task 3); `volume_size_for` z `bot.archive`.
- Produces:
  - `send_audio_mtproto(chat_id, file_path, title=None, caption=None, thumb_path=None, *, performer=None, file_name=None, buttons=None, cancellation=None) -> bool`
  - `AudioDeliveryError(RuntimeError)`
  - `TRIM_BUTTON_LABEL = "✂️ Przytnij"`, `TRIM_AVAILABLE_HINT: str`
  - `trim_button(token: str, label: str = TRIM_BUTTON_LABEL) -> tuple[str, str]`
  - `max_sendable_audio_mb() -> int`
  - `async send_audio_file(context, chat_id, path, *, title, performer=None, caption=None, thumb_path=None, filename=None, buttons=None, cancellation=None) -> None`
  - `async send_audio_with_trim(context, chat_id, path, *, title, performer=None, caption=None, thumb_path=None, cancellation=None) -> TrimSource | None`

- [ ] **Step 1: Write the failing tests**

Dopisz w `tests/test_mtproto.py` wewnątrz klasy `TestSendAudioMtproto` (po `test_returns_true_on_success`):

```python
    def _client_and_pyrogram(self):
        mock_client_instance = MagicMock()
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_instance.send_audio = AsyncMock()
        mock_pyrogram = MagicMock()
        mock_pyrogram.Client.return_value = mock_client_instance
        return mock_client_instance, mock_pyrogram

    def test_passes_performer_file_name_and_buttons(self, monkeypatch, tmp_path):
        _set_mtproto_config(monkeypatch)
        audio_file = tmp_path / "source.mp3"
        audio_file.write_bytes(b"\x00" * 100)
        client, pyrogram = self._client_and_pyrogram()

        with patch.dict('sys.modules', {'pyrogram': pyrogram}):
            result = asyncio.run(send_audio_mtproto(
                123, str(audio_file), title="T", performer="Host", file_name="Episode.mp3",
                buttons=[("✂️ Przytnij", "trim_src_AAAAAAAAAAA")],
            ))

        assert result is True
        kwargs = client.send_audio.await_args.kwargs
        assert kwargs["performer"] == "Host"
        assert kwargs["file_name"] == "Episode.mp3"
        pyrogram.types.InlineKeyboardButton.assert_called_once_with(
            "✂️ Przytnij", callback_data="trim_src_AAAAAAAAAAA"
        )
        assert kwargs["reply_markup"] is pyrogram.types.InlineKeyboardMarkup.return_value

    def test_omits_reply_markup_without_buttons(self, monkeypatch, tmp_path):
        _set_mtproto_config(monkeypatch)
        audio_file = tmp_path / "source.mp3"
        audio_file.write_bytes(b"\x00" * 100)
        client, pyrogram = self._client_and_pyrogram()

        with patch.dict('sys.modules', {'pyrogram': pyrogram}):
            asyncio.run(send_audio_mtproto(123, str(audio_file), title="T"))

        assert client.send_audio.await_args.kwargs["reply_markup"] is None
```

Utwórz `tests/test_audio_delivery.py`:

```python
"""Tests for single-audio delivery with the ✂️ trim button."""

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from bot.handlers import audio_delivery
from bot.services.audio_trim_service import AudioTrimError
from bot.services.trim_store import TrimSource
from tests.telegram_callbacks_support import _make_context


def _write(path: Path, payload: bytes = b"\x00" * 1024) -> Path:
    path.write_bytes(payload)
    return path


def _big(path: Path, size_mb: int) -> Path:
    path.write_bytes(b"")
    os.truncate(path, size_mb * 1024 * 1024)
    return path


def test_send_audio_file_uses_bot_api_with_buttons(tmp_path):
    context = _make_context()
    path = _write(tmp_path / "source.mp3")
    asyncio.run(audio_delivery.send_audio_file(
        context, 5, path, title="T", performer="P", caption="C", filename="Episode.mp3",
        buttons=[("✂️ Przytnij", "trim_src_AAAAAAAAAAA")],
    ))
    kwargs = context.bot.send_audio.await_args.kwargs
    assert (kwargs["title"], kwargs["performer"], kwargs["caption"]) == ("T", "P", "C")
    assert kwargs["filename"] == "Episode.mp3"
    assert kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "trim_src_AAAAAAAAAAA"


def test_send_audio_file_defaults_filename_and_no_markup(tmp_path):
    context = _make_context()
    path = _write(tmp_path / "song.mp3")
    asyncio.run(audio_delivery.send_audio_file(context, 5, path, title="T"))
    kwargs = context.bot.send_audio.await_args.kwargs
    assert kwargs["filename"] == "song.mp3"
    assert kwargs["reply_markup"] is None


def test_send_audio_file_uses_mtproto_above_bot_api_limit(tmp_path, monkeypatch):
    context = _make_context()
    path = _big(tmp_path / "big.mp3", 51)
    captured = {}

    async def fake_mtproto(chat_id, file_path, **kwargs):
        captured.update(kwargs, chat_id=chat_id, file_path=file_path)
        return True

    monkeypatch.setattr(audio_delivery, "mtproto_unavailability_reason", lambda: None)
    monkeypatch.setattr(audio_delivery, "send_audio_mtproto", fake_mtproto)
    asyncio.run(audio_delivery.send_audio_file(
        context, 5, path, title="T", caption="C", buttons=[("✂️ Przytnij", "trim_src_X")],
    ))
    assert captured["file_path"] == str(path)
    assert captured["file_name"] == "big.mp3"
    assert captured["buttons"] == [("✂️ Przytnij", "trim_src_X")]
    context.bot.send_audio.assert_not_awaited()


def test_send_audio_file_reports_unavailable_mtproto(tmp_path, monkeypatch):
    path = _big(tmp_path / "big.mp3", 51)
    monkeypatch.setattr(audio_delivery, "mtproto_unavailability_reason", lambda: "Brak pyrogram.")
    with pytest.raises(audio_delivery.AudioDeliveryError) as exc_info:
        asyncio.run(audio_delivery.send_audio_file(_make_context(), 5, path, title="T"))
    assert "Plik za duży dla Bot API (51 MB, limit: 50 MB)" in str(exc_info.value)
    assert "Brak pyrogram." in str(exc_info.value)


def test_send_audio_file_reports_failed_mtproto_upload(tmp_path, monkeypatch):
    path = _big(tmp_path / "big.mp3", 51)
    monkeypatch.setattr(audio_delivery, "mtproto_unavailability_reason", lambda: None)
    monkeypatch.setattr(audio_delivery, "send_audio_mtproto", AsyncMock(return_value=False))
    with pytest.raises(audio_delivery.AudioDeliveryError) as exc_info:
        asyncio.run(audio_delivery.send_audio_file(_make_context(), 5, path, title="T"))
    assert str(exc_info.value) == "Wysyłanie pliku przez MTProto nie powiodło się."


def test_max_sendable_audio_mb(monkeypatch):
    monkeypatch.setattr(audio_delivery, "mtproto_unavailability_reason", lambda: None)
    assert audio_delivery.max_sendable_audio_mb() == audio_delivery.volume_size_for(use_mtproto=True)
    monkeypatch.setattr(audio_delivery, "mtproto_unavailability_reason", lambda: "no")
    assert audio_delivery.max_sendable_audio_mb() == audio_delivery.TELEGRAM_UPLOAD_LIMIT_MB


def _fake_retain(store_dir: Path):
    def retain(chat_id, file_path, **kwargs):
        store_dir.mkdir(parents=True, exist_ok=True)
        dest = store_dir / "source.mp3"
        Path(file_path).rename(dest)
        return TrimSource("AAAAAAAAAAA", chat_id, dest, kwargs["title"], kwargs["performer"],
                          kwargs["duration_sec"], datetime.now(UTC))
    return retain


def test_send_audio_with_trim_retains_and_attaches_button(tmp_path, monkeypatch):
    context = _make_context()
    original = _write(tmp_path / "Episode.mp3")
    monkeypatch.setattr(audio_delivery, "probe_duration", AsyncMock(return_value=61.6))
    monkeypatch.setattr(audio_delivery, "retain_source", _fake_retain(tmp_path / "store"))

    source = asyncio.run(audio_delivery.send_audio_with_trim(
        context, 5, original, title="Ep", performer="Show", caption="Ep",
    ))

    assert source.duration_sec == 62
    kwargs = context.bot.send_audio.await_args.kwargs
    assert kwargs["filename"] == "Episode.mp3"
    button = kwargs["reply_markup"].inline_keyboard[0][0]
    assert (button.text, button.callback_data) == ("✂️ Przytnij", "trim_src_AAAAAAAAAAA")


def test_send_audio_with_trim_falls_back_when_probe_fails(tmp_path, monkeypatch):
    context = _make_context()
    original = _write(tmp_path / "episode.mp3")
    monkeypatch.setattr(audio_delivery, "probe_duration", AsyncMock(side_effect=AudioTrimError("bad")))
    retain = Mock()
    monkeypatch.setattr(audio_delivery, "retain_source", retain)

    assert asyncio.run(audio_delivery.send_audio_with_trim(context, 5, original, title="Ep")) is None
    retain.assert_not_called()
    assert context.bot.send_audio.await_args.kwargs["reply_markup"] is None
    assert original.exists()


def test_send_audio_with_trim_falls_back_when_store_refuses(tmp_path, monkeypatch):
    context = _make_context()
    original = _write(tmp_path / "episode.mp3")
    monkeypatch.setattr(audio_delivery, "probe_duration", AsyncMock(return_value=30.0))
    monkeypatch.setattr(audio_delivery, "retain_source", lambda *a, **k: None)

    assert asyncio.run(audio_delivery.send_audio_with_trim(context, 5, original, title="Ep")) is None
    assert context.bot.send_audio.await_args.kwargs["reply_markup"] is None


def test_send_audio_with_trim_discards_source_when_send_fails(tmp_path, monkeypatch):
    context = _make_context()
    context.bot.send_audio = AsyncMock(side_effect=RuntimeError("telegram down"))
    original = _write(tmp_path / "episode.mp3")
    monkeypatch.setattr(audio_delivery, "probe_duration", AsyncMock(return_value=30.0))
    monkeypatch.setattr(audio_delivery, "retain_source", _fake_retain(tmp_path / "store"))
    discard = Mock()
    monkeypatch.setattr(audio_delivery, "discard_source", discard)

    with pytest.raises(RuntimeError):
        asyncio.run(audio_delivery.send_audio_with_trim(context, 5, original, title="Ep"))
    discard.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_mtproto.py tests/test_audio_delivery.py -q`
Expected: FAIL — `TypeError: send_audio_mtproto() got an unexpected keyword argument 'performer'` oraz `ImportError: cannot import name 'audio_delivery'`

- [ ] **Step 3: Write minimal implementation**

W `bot/mtproto.py` zmień sygnaturę i wywołanie `send_audio_mtproto`:

```python
async def send_audio_mtproto(
    chat_id: int,
    file_path: str,
    title: str | None = None,
    caption: str | None = None,
    thumb_path: str | None = None,
    *,
    performer: str | None = None,
    file_name: str | None = None,
    buttons: list[tuple[str, str]] | None = None,
    cancellation: "JobCancellation | None" = None,
) -> bool:
```

W docstringu, w sekcji `Args:`, po `thumb_path` dopisz:

```
        performer: Optional performer shown in the audio player.
        file_name: Optional file name shown to the user (defaults to the path's).
        buttons: Optional (label, callback_data) pairs, one inline button per row.
```

Zastąp blok `coro = client.send_audio(...)` przez:

```python
            reply_markup = None
            if buttons:
                from pyrogram import types as pyrogram_types

                reply_markup = pyrogram_types.InlineKeyboardMarkup(
                    [[pyrogram_types.InlineKeyboardButton(label, callback_data=data)]
                     for label, data in buttons]
                )
            coro = client.send_audio(
                chat_id=chat_id,
                audio=file_path,
                title=title,
                performer=performer,
                file_name=file_name,
                caption=caption,
                thumb=thumb_path,
                reply_markup=reply_markup,
            )
```

Utwórz `bot/handlers/audio_delivery.py`:

```python
"""Send one audio file, optionally with the ✂️ trim button under it.

Every single-audio send site goes through here (bot/handlers/download_callbacks.py,
bot/handlers/spotify_callbacks.py) and so do trimmed fragments
(bot/handlers/trim_callbacks.py). See also: bot/services/trim_store.py.
"""

from __future__ import annotations

import logging
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.archive import volume_size_for
from bot.mtproto import mtproto_unavailability_reason, send_audio_mtproto
from bot.security_limits import TELEGRAM_UPLOAD_LIMIT_MB
from bot.services.audio_trim_service import AudioTrimError, probe_duration
from bot.services.trim_store import TrimSource, discard_source, retain_source

TRIM_BUTTON_LABEL = "✂️ Przytnij"
TRIM_AVAILABLE_HINT = "✂️ Pod plikiem jest przycisk „Przytnij” — działa przez 24 h."


class AudioDeliveryError(RuntimeError):
    """Delivery failed; str(exc) is a Polish, user-facing message."""


def trim_button(token: str, label: str = TRIM_BUTTON_LABEL) -> tuple[str, str]:
    return label, f"trim_src_{token}"


def max_sendable_audio_mb() -> int:
    """Largest file the active transport can deliver, in MiB."""

    if mtproto_unavailability_reason() is None:
        return volume_size_for(use_mtproto=True)
    return TELEGRAM_UPLOAD_LIMIT_MB


async def send_audio_file(
    context,
    chat_id: int,
    path,
    *,
    title: str,
    performer: str | None = None,
    caption: str | None = None,
    thumb_path: str | None = None,
    filename: str | None = None,
    buttons: list[tuple[str, str]] | None = None,
    cancellation=None,
) -> None:
    """Send through the Bot API up to its limit, through MTProto above it."""

    file_path = Path(path)
    display_name = filename or file_path.name
    size_mb = file_path.stat().st_size / (1024 * 1024)

    if size_mb <= TELEGRAM_UPLOAD_LIMIT_MB:
        reply_markup = (
            InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data)] for label, data in buttons])
            if buttons
            else None
        )
        thumb_file = open(thumb_path, "rb") if thumb_path else None
        try:
            with file_path.open("rb") as file_obj:
                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=file_obj,
                    filename=display_name,
                    title=title,
                    performer=performer,
                    caption=caption,
                    thumbnail=thumb_file,
                    reply_markup=reply_markup,
                    read_timeout=120,
                    write_timeout=120,
                )
        finally:
            if thumb_file is not None:
                thumb_file.close()
        return

    reason = mtproto_unavailability_reason()
    if reason is not None:
        raise AudioDeliveryError(
            f"Plik za duży dla Bot API ({size_mb:.0f} MB, limit: {TELEGRAM_UPLOAD_LIMIT_MB} MB).\n{reason}"
        )
    ok = await send_audio_mtproto(
        chat_id,
        str(file_path),
        title=title,
        caption=caption,
        thumb_path=thumb_path,
        performer=performer,
        file_name=display_name,
        buttons=buttons,
        cancellation=cancellation,
    )
    if not ok:
        raise AudioDeliveryError("Wysyłanie pliku przez MTProto nie powiodło się.")


async def send_audio_with_trim(
    context,
    chat_id: int,
    path,
    *,
    title: str,
    performer: str | None = None,
    caption: str | None = None,
    thumb_path: str | None = None,
    cancellation=None,
) -> TrimSource | None:
    """Send one audio file with ✂️ and keep it in the trim store for 24 h.

    Falls back to a plain send when the file cannot be kept (unsupported
    format, low disk, unreadable duration). When a TrimSource is returned the
    file has moved into the store, so callers must not reuse ``path``.
    """

    original = Path(path)
    source = None
    try:
        duration = await probe_duration(original)
    except AudioTrimError as exc:
        logging.warning("Not offering trim for %s: %s", original.name, exc)
    else:
        source = retain_source(
            chat_id, original, title=title, performer=performer, duration_sec=round(duration)
        )

    if source is None:
        await send_audio_file(
            context, chat_id, original, title=title, performer=performer, caption=caption,
            thumb_path=thumb_path, cancellation=cancellation,
        )
        return None

    try:
        await send_audio_file(
            context, chat_id, source.path, title=title, performer=performer, caption=caption,
            thumb_path=thumb_path, filename=original.name, buttons=[trim_button(source.token)],
            cancellation=cancellation,
        )
    except BaseException:
        discard_source(source)
        raise
    return source
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_mtproto.py tests/test_audio_delivery.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bot/mtproto.py bot/handlers/audio_delivery.py tests/test_mtproto.py tests/test_audio_delivery.py
git commit -m "Add audio delivery helper with trim button over Bot API and MTProto"
```

---

### Task 5: Przepływ cięcia — prompt, oczekujące wejście, zadanie

**Files:**
- Modify: `bot/session_store.py` (dataclass + pole + `_cleanup_if_empty`)
- Modify: `bot/session_context.py` (`TRANSIENT_FLOW_FIELDS`, `TRANSIENT_FLOW_LEGACY_KEYS`)
- Modify: `bot/jobs.py` (`JobKind`)
- Create: `bot/handlers/trim_callbacks.py`
- Modify: `bot/handlers/transcript_prompt_handlers.py` (`handle_transcript_prompt_callback`)
- Modify: `bot/handlers/inbound_media.py` (import + hook po `handle_pending_transcript_prompt`)
- Modify: `bot/telegram_callbacks.py` (import + routing po `tr_prompt_`)
- Create: `tests/test_trim_callbacks.py`
- Modify: `tests/test_transcript_prompt_handlers.py`, `tests/test_inbound_media_handlers.py`

**Interfaces:**
- Consumes: Task 1 (`parse_time_ranges`, `resolve_ranges`, `format_timestamp`, `TimeRangeError`, `ResolvedRange`), Task 2 (`cut_fragment`, `fragment_filename`, `fragment_label`, `AudioTrimError`), Task 3 (`TrimSource`, `load_source`, `expires_at`), Task 4 (`send_audio_file`, `max_sendable_audio_mb`, `AudioDeliveryError`).
- Produces:
  - `PendingTrimInput(token: str, requester_id: int, created_at)` w `bot.session_store`
  - `JobKind` zawiera `"trim"`
  - w `bot.handlers.trim_callbacks`: `EXPIRED_TEXT`, `AUTH_REQUIRED_TEXT`, `BUSY_TEXT`, `RATE_LIMIT_TEXT`, `NO_ROOM_TEXT`, `CANCEL_MARKUP`, `fragments_phrase(count: int) -> str`, `build_trim_prompt_text(source, intro: str = "") -> str`, `async start_trim_prompt(context, *, chat_id, requester_id, source, query=None, intro="") -> None`, `async ensure_trim_authorized(update, context) -> bool`, `async handle_trim_callback(update, context, data: str) -> None`, `async handle_pending_trim_input(update, context) -> bool`, `async run_trim_job(context, *, chat_id, requester_id, source, ranges, status_message) -> None`

- [ ] **Step 1: Write the failing tests** — utwórz `tests/test_trim_callbacks.py`:

```python
"""Tests for the ✂️ trim flow: prompt, pending input and the fragment job."""

import asyncio
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
    context = _make_context()
    _pending(context, source)
    run = AsyncMock()
    monkeypatch.setattr(tcb, "run_trim_job", run)
    monkeypatch.setattr(tcb, "check_rate_limit", lambda *_: True)
    update, status = _text_update("12:00-15:30, 1:20:00-")

    assert asyncio.run(tcb.handle_pending_trim_input(update, context)) is True

    kwargs = run.await_args.kwargs
    assert kwargs["ranges"] == [ResolvedRange(720, 930, False), ResolvedRange(4800, 6130, True)]
    assert kwargs["status_message"] is status
    assert kwargs["source"] == source
    assert "pending_trim" not in context.user_data
    assert update.message.reply_text.await_args.args[0] == "✂️ Przygotowuję 2 fragmenty..."


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
```

Dopisz na końcu `tests/test_transcript_prompt_handlers.py`:

```python


def test_prompt_callback_clears_pending_trim(tmp_path):
    transcript = tmp_path / "sample_transcript.md"
    transcript.write_text("Body", encoding="utf-8")
    context = _make_context()
    token = handlers.register_transcript_context(
        context, chat_id=10, requester_id=20, transcript_path=str(transcript), title="Sample",
    )
    context.user_data["pending_trim"] = object()
    update = _make_update(f"tr_prompt_{token}", chat_id=10)
    update.effective_user.id = 20

    asyncio.run(handlers.handle_transcript_prompt_callback(update, context, f"tr_prompt_{token}"))

    assert "pending_trim" not in context.user_data
    assert context.user_data["pending_transcript_prompt"].transcript_token == token
```

W `tests/test_inbound_media_handlers.py` dopisz na początku importów `from bot.handlers import inbound_media`, a w klasie `TestHandleYoutubeLinkTimeRange` dodaj test:

```python
    def test_pending_trim_input_wins_over_pre_download_range(self, monkeypatch):
        update = _make_update(text="0:10-0:20", user_id=333, chat_id=333)
        context = _make_context()

        _set_authorized_users(monkeypatch, {333})
        monkeypatch.setattr(tc, "handle_pin", AsyncMock(return_value=False))
        tc.user_urls[333] = "https://www.youtube.com/watch?v=existing"
        consumed = AsyncMock(return_value=True)
        monkeypatch.setattr(inbound_media, "handle_pending_trim_input", consumed)

        _async(tc.handle_youtube_link(update, context))

        consumed.assert_awaited_once()
        assert tc.user_time_ranges.get(333) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_trim_callbacks.py tests/test_transcript_prompt_handlers.py tests/test_inbound_media_handlers.py -q`
Expected: FAIL — `ImportError: cannot import name 'PendingTrimInput'` / `trim_callbacks`

- [ ] **Step 3: Write minimal implementation**

`bot/session_store.py` — pod `PendingTranscriptPrompt` dodaj:

```python
@dataclass(frozen=True)
class PendingTrimInput:
    """One chat waiting for a specific user to type trim ranges for a stored source."""

    token: str
    requester_id: int
    created_at: Any  # timezone-aware datetime
```

W `SessionState` po `pending_transcript_prompt: ...` dodaj `pending_trim: PendingTrimInput | None = None`, a w `_cleanup_if_empty` po `and session.pending_transcript_prompt is None` dodaj `and session.pending_trim is None`.

`bot/session_context.py` — dopisz `"pending_trim",` na końcu obu krotek `TRANSIENT_FLOW_FIELDS` i `TRANSIENT_FLOW_LEGACY_KEYS`.

`bot/jobs.py` — dopisz `"trim",` na końcu `JobKind`.

`bot/handlers/transcript_prompt_handlers.py` — w `handle_transcript_prompt_callback`, tuż przed `set_session_context_value(context, chat_id, _PENDING_FIELD, PendingTranscriptPrompt(...` dodaj:

```python
    # Only one text input may be pending per chat; see bot/handlers/trim_callbacks.py.
    clear_session_context_value(context, chat_id, "pending_trim", legacy_key="pending_trim")
```

Utwórz `bot/handlers/trim_callbacks.py`:

```python
"""✂️ audio trimming flow: ask for ranges, cut fragments, send them.

Callbacks handled here: ``trim_src_<token>`` (source in the trim store),
``trim_upload`` (audio sent to the bot) and ``trim_cancel``. ``trim_dl`` is
routed in bot/telegram_callbacks.py because it calls the download flows,
which import this module.

See also: bot/services/trim_store.py, bot/services/audio_trim_service.py,
bot/handlers/audio_delivery.py, bot/handlers/time_range.py.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.handlers.audio_delivery import AudioDeliveryError, max_sendable_audio_mb, send_audio_file
from bot.handlers.common_ui import escape_md, safe_edit_message
from bot.handlers.time_range import TimeRangeError, format_timestamp, parse_time_ranges, resolve_ranges
from bot.jobs import JobDescriptor, job_registry
from bot.runtime import record_download_for
from bot.security_limits import TRIM_PENDING_INPUT_TIMEOUT_MIN
from bot.security_policy import extract_url_from_text
from bot.security_throttling import check_rate_limit
from bot.services.audio_trim_service import AudioTrimError, cut_fragment, fragment_filename, fragment_label
from bot.services.trim_store import TrimSource, expires_at, load_source
from bot.session_context import (
    clear_session_context_value,
    get_session_context_value,
    set_session_context_value,
)
from bot.session_store import PendingTrimInput

_PENDING_FIELD = "pending_trim"
_TRANSCRIPT_PENDING_FIELD = "pending_transcript_prompt"
_MONTHS = ("sty", "lut", "mar", "kwi", "maj", "cze", "lip", "sie", "wrz", "paź", "lis", "gru")

EXPIRED_TEXT = (
    "Plik wygasł (minęły 24 h) albo został usunięty. Wyślij to MP3 do bota, żeby je przyciąć."
)
AUTH_REQUIRED_TEXT = "Wymagane uwierzytelnienie — wyślij kod PIN."
BUSY_TEXT = "Poczekaj, aż skończę poprzednie cięcie, albo przerwij je komendą /stop."
RATE_LIMIT_TEXT = "Przekroczono limit requestów. Spróbuj ponownie za chwilę."
NO_ROOM_TEXT = (
    "Na serwerze brakuje miejsca, żeby przechować plik do cięcia. "
    "Pobierz całość przyciskiem Audio (MP3)."
)
CANCEL_MARKUP = InlineKeyboardMarkup([[InlineKeyboardButton("Anuluj", callback_data="trim_cancel")]])


def _now() -> datetime:
    return datetime.now(UTC)


def _is_authorized(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    # Imported lazily, as in bot/handlers/inbound_media.py, to keep handler
    # modules free of import cycles.
    from bot.handlers.command_access import _is_authorized as shared_is_authorized

    return shared_is_authorized(context, user_id)


def _get_pending(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> PendingTrimInput | None:
    pending = get_session_context_value(context, chat_id, _PENDING_FIELD, legacy_key=_PENDING_FIELD)
    return pending if isinstance(pending, PendingTrimInput) else None


def _clear_pending(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    clear_session_context_value(context, chat_id, _PENDING_FIELD, legacy_key=_PENDING_FIELD)


def _set_pending(context: ContextTypes.DEFAULT_TYPE, chat_id: int, pending: PendingTrimInput) -> None:
    # Only one text input may be pending per chat: a trim prompt supersedes a
    # custom transcript instruction (transcript_prompt_handlers does the reverse).
    clear_session_context_value(
        context, chat_id, _TRANSCRIPT_PENDING_FIELD, legacy_key=_TRANSCRIPT_PENDING_FIELD
    )
    set_session_context_value(context, chat_id, _PENDING_FIELD, pending, legacy_key=_PENDING_FIELD)


def fragments_phrase(count: int) -> str:
    """Polish count phrase: 1 fragment, 2 fragmenty, 5 fragmentów."""

    if count == 1:
        return "1 fragment"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return f"{count} fragmenty"
    return f"{count} fragmentów"


def _format_expiry(source: TrimSource) -> str:
    local = expires_at(source).astimezone()
    return f"{local.day} {_MONTHS[local.month - 1]}, {local:%H:%M}"


def build_trim_prompt_text(source: TrimSource, intro: str = "") -> str:
    return (
        f"{intro}✂️ Przycinanie: *{escape_md(source.title)}*\n"
        f"Długość: {format_timestamp(source.duration_sec)}\n\n"
        "Wpisz zakres (albo kilka po przecinku):\n"
        "`1:30-4:45` — jeden fragment\n"
        "`2:15-` — od 2:15 do końca\n"
        "`-5:00` — od początku do 5:00\n"
        "`1:00-2:00, 5:30-7:00` — dwa pliki\n\n"
        f"Plik jest dostępny do {_format_expiry(source)}."
    )


async def start_trim_prompt(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    chat_id: int,
    requester_id: int,
    source: TrimSource,
    query=None,
    intro: str = "",
) -> None:
    """Ask for ranges; edits the callback message when ``query`` is given."""

    _set_pending(
        context,
        chat_id,
        PendingTrimInput(token=source.token, requester_id=requester_id, created_at=_now()),
    )
    text = build_trim_prompt_text(source, intro)
    if query is not None:
        await safe_edit_message(query, text, reply_markup=CANCEL_MARKUP, parse_mode="Markdown")
        return
    await context.bot.send_message(
        chat_id=chat_id, text=text, reply_markup=CANCEL_MARKUP, parse_mode="Markdown"
    )


async def ensure_trim_authorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Trim sources outlive restarts and /logout, so buttons re-check the PIN state."""

    if _is_authorized(context, update.effective_user.id):
        return True
    await context.bot.send_message(chat_id=update.effective_chat.id, text=AUTH_REQUIRED_TEXT)
    return False


async def handle_trim_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    """Route trim_src_*, trim_upload and trim_cancel callbacks."""

    if not await ensure_trim_authorized(update, context):
        return
    query = update.callback_query
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if data == "trim_cancel":
        pending = _get_pending(context, chat_id)
        if pending is not None and pending.requester_id == user_id:
            _clear_pending(context, chat_id)
            await safe_edit_message(query, "Anulowano przycinanie.")
            return
        await safe_edit_message(query, "Ta prośba nie jest już aktywna.")
        return

    if data.startswith("trim_src_"):
        source = load_source(chat_id, data.removeprefix("trim_src_"))
        if source is None:
            # A new message: the button may sit under an audio whose text cannot be edited.
            await context.bot.send_message(chat_id=chat_id, text=EXPIRED_TEXT)
            return
        await start_trim_prompt(context, chat_id=chat_id, requester_id=user_id, source=source)
        return

    await safe_edit_message(query, "Nieobsługiwana akcja przycinania.")


async def handle_pending_trim_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Consume typed ranges before normal URL/text handling."""

    chat_id = update.effective_chat.id
    requester_id = update.effective_user.id
    pending = _get_pending(context, chat_id)
    if pending is None or pending.requester_id != requester_id:
        return False

    text = (update.message.text or "").strip()
    if _now() - pending.created_at > timedelta(minutes=TRIM_PENDING_INPUT_TIMEOUT_MIN):
        _clear_pending(context, chat_id)
        return False
    if extract_url_from_text(text):
        # A new link means the user moved on; let the normal flow handle it.
        _clear_pending(context, chat_id)
        return False

    source = load_source(chat_id, pending.token)
    if source is None:
        _clear_pending(context, chat_id)
        await update.message.reply_text(EXPIRED_TEXT)
        return True

    try:
        ranges = resolve_ranges(parse_time_ranges(text), source.duration_sec)
    except TimeRangeError as exc:
        # No parse_mode: the message echoes user input.
        await update.message.reply_text(str(exc))
        return True

    if not check_rate_limit(requester_id):
        await update.message.reply_text(RATE_LIMIT_TEXT)
        return True
    if any(job.kind == "trim" for job in job_registry.list_for_chat(chat_id)):
        await update.message.reply_text(BUSY_TEXT)
        return True

    _clear_pending(context, chat_id)
    status_message = await update.message.reply_text(
        f"✂️ Przygotowuję {fragments_phrase(len(ranges))}..."
    )
    await run_trim_job(
        context,
        chat_id=chat_id,
        requester_id=requester_id,
        source=source,
        ranges=ranges,
        status_message=status_message,
    )
    return True


async def _edit_status(message, text: str, reply_markup=None) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except Exception as exc:
        logging.warning("Trim status update failed: %s", exc)


def _sent_before(sent: int) -> str:
    return f"\n\nWysłano wcześniej: {fragments_phrase(sent)}." if sent else ""


async def run_trim_job(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    chat_id: int,
    requester_id: int,
    source: TrimSource,
    ranges: list,
    status_message,
) -> None:
    """Cut and send each fragment; the source stays in the store for more cuts."""

    descriptor = JobDescriptor(
        job_id="",
        chat_id=chat_id,
        kind="trim",
        label=f"Przycinanie: {source.title}"[:80],
        started_at=datetime.now(),
    )
    cancellation = job_registry.register(chat_id, descriptor)
    again_markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✂️ Tnij dalej", callback_data=f"trim_src_{source.token}")]]
    )
    total = len(ranges)
    sent = 0
    limit_mb = max_sendable_audio_mb()

    try:
        for index, fragment in enumerate(ranges, start=1):
            if cancellation.event.is_set():
                break
            label = fragment_label(fragment)
            fragment_title = f"{source.title} [{label}]"
            dest = source.workspace / fragment_filename(source.title, fragment, source.path.suffix)
            try:
                await _edit_status(status_message, f"✂️ Tnę fragment {index}/{total} ({label})...")
                cut_started = time.monotonic()
                try:
                    await cut_fragment(
                        source.path, fragment, dest, title_tag=fragment_title, cancellation=cancellation
                    )
                    logging.info(
                        "Trim cut: token=%s range=%s took %.1fs",
                        source.token, label, time.monotonic() - cut_started,
                    )
                except AudioTrimError as exc:
                    if cancellation.event.is_set():
                        break
                    logging.error("Trim cut failed: token=%s range=%s: %s", source.token, label, exc)
                    await _edit_status(
                        status_message,
                        f"Nie udało się wyciąć fragmentu {label}. "
                        f"Pozostałe fragmenty nie zostały wysłane.{_sent_before(sent)}",
                        again_markup,
                    )
                    return

                size_mb = dest.stat().st_size / (1024 * 1024)
                if size_mb > limit_mb:
                    await _edit_status(
                        status_message,
                        f"Fragment {label} ma {size_mb:.0f} MB — za dużo do wysłania. "
                        f"Wybierz krótszy zakres.{_sent_before(sent)}",
                        again_markup,
                    )
                    return

                await _edit_status(status_message, f"✂️ Wysyłam fragment {index}/{total} ({label})...")
                try:
                    await send_audio_file(
                        context,
                        chat_id,
                        dest,
                        title=fragment_title,
                        performer=source.performer,
                        caption=fragment_title[:200],
                        cancellation=cancellation,
                    )
                except Exception as exc:
                    if cancellation.event.is_set():
                        break
                    logging.error("Trim send failed: token=%s range=%s: %s", source.token, label, exc)
                    message = (
                        str(exc)
                        if isinstance(exc, AudioDeliveryError)
                        else f"Nie udało się wysłać fragmentu {label}. Spróbuj ponownie."
                    )
                    await _edit_status(status_message, f"{message}{_sent_before(sent)}", again_markup)
                    return
                sent += 1
            finally:
                dest.unlink(missing_ok=True)
    finally:
        job_registry.unregister(cancellation.job_id)

    if cancellation.event.is_set():
        await _edit_status(status_message, f"⏹ Przerwano po {sent} z {total} fragmentów.", again_markup)
        return
    record_download_for(context, requester_id, source.title, "", "audio_trim")
    await _edit_status(status_message, f"Gotowe: {fragments_phrase(sent)}.", again_markup)
```

`bot/handlers/inbound_media.py`:

1. Pod `from bot.handlers.transcript_prompt_handlers import handle_pending_transcript_prompt` dodaj:

```python
from bot.handlers.trim_callbacks import handle_pending_trim_input
```

2. W `handle_youtube_link` po:

```python
    if await handle_pending_transcript_prompt(update, context):
        return
```

dodaj:

```python
    # Must run before the pre-download range parser below: both accept "1:30-4:45".
    if await handle_pending_trim_input(update, context):
        return
```

`bot/telegram_callbacks.py`:

1. Do importów dodaj:

```python
from bot.handlers.trim_callbacks import handle_trim_callback
```

2. W `handle_callback` po bloku `if data.startswith("tr_prompt_"): ... return` dodaj:

```python
    # trim_dl needs the session URL and is routed next to dl_ below.
    if data.startswith("trim_") and data != "trim_dl":
        await handle_trim_callback(update, context, data)
        return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_trim_callbacks.py tests/test_transcript_prompt_handlers.py tests/test_inbound_media_handlers.py tests/test_session_store.py tests/test_jobs.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bot/session_store.py bot/session_context.py bot/jobs.py bot/handlers/trim_callbacks.py \
  bot/handlers/transcript_prompt_handlers.py bot/handlers/inbound_media.py bot/telegram_callbacks.py \
  tests/test_trim_callbacks.py tests/test_transcript_prompt_handlers.py tests/test_inbound_media_handlers.py
git commit -m "Add trim prompt, pending range input and fragment job"
```

---

### Task 6: ✂️ pod każdym wysłanym pojedynczym audio

**Files:**
- Modify: `bot/handlers/download_callbacks.py` (import; blok wysyłki w `download_file`, linie ~603–660; obsługa błędów ~666)
- Modify: `bot/handlers/spotify_callbacks.py` (import; `download_spotify_resolved` ~271–289; `download_spotify_video` ~385–455)
- Modify: `tests/test_callback_download_handlers.py` (dopisz helper i test)
- Modify: `tests/test_callback_transcription_handlers.py` (aktualizacja `test_download_spotify_video_audio_mtproto_passes_title`, nowe testy)

**Interfaces:**
- Consumes: `send_audio_with_trim`, `AudioDeliveryError`, `TRIM_AVAILABLE_HINT` (Task 4).
- Produces: brak nowych nazw; zmiana zachowania — plik audio po wysyłce trafia do magazynu, a status zawiera `TRIM_AVAILABLE_HINT`.

- [ ] **Step 1: Write the failing tests**

Dopisz na końcu `tests/test_callback_download_handlers.py`:

```python


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
```

W `tests/test_callback_transcription_handlers.py`:

1. Do importów dodaj `from bot.handlers import audio_delivery`.
2. W `test_download_spotify_video_audio_mtproto_passes_title` zmień fałszywkę i podmiany:

```python
    async def fake_send_audio_mtproto(chat_id, file_path, title=None, caption=None, thumb_path=None, **kwargs):
        captured["title"] = title
        captured["caption"] = caption
        return True

    monkeypatch.setattr(sc, "download_episode_media", fake_download)
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)
    monkeypatch.setattr(audio_delivery, "mtproto_unavailability_reason", lambda: None)
    monkeypatch.setattr(audio_delivery, "send_audio_mtproto", fake_send_audio_mtproto)
```

(usuń stare podmiany `sc._mtproto_unavailability_reason` i `sc.send_audio_mtproto` w tym teście).

3. Dopisz na końcu pliku:

```python


def test_download_spotify_video_audio_offers_trim(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    produced = tmp_path / "episode.m4a"
    produced.write_bytes(b"X" * 1024)

    async def fake_download(**kwargs):
        return str(produced)

    sender = AsyncMock(return_value=SimpleNamespace(token="AAAAAAAAAAA"))
    monkeypatch.setattr(sc, "download_episode_media", fake_download)
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)
    monkeypatch.setattr(sc, "send_audio_with_trim", sender)
    update = _make_update("spv_audio_m4a", chat_id=123)

    asyncio.run(sc.download_spotify_video(update, _make_context(), _spotify_video_session(), height=None))

    assert sender.await_args.kwargs["title"] == "Test Episode"
    assert "✂️ Pod plikiem" in update.callback_query.edit_message_text.await_args.args[0]


def test_download_spotify_resolved_offers_trim(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    produced = tmp_path / "Host - Episode.mp3"
    produced.write_bytes(b"X" * 1024)
    sender = AsyncMock(return_value=SimpleNamespace(token="AAAAAAAAAAA"))
    monkeypatch.setattr(sc, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(sc, "download_resolved_audio", AsyncMock(return_value=str(produced)))
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)
    monkeypatch.setattr(sc, "send_audio_with_trim", sender)
    update = _make_update("dl_audio_mp3", chat_id=123)

    result = asyncio.run(sc.download_spotify_resolved(
        update, _make_context(), {"source": "itunes", "title": "Episode", "artist": "Host"},
    ))

    assert result is True
    assert sender.await_args.kwargs["performer"] == "Host"
    assert "✂️ Pod plikiem" in update.callback_query.edit_message_text.await_args.args[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_callback_download_handlers.py tests/test_callback_transcription_handlers.py -q`
Expected: FAIL — `AttributeError: ... has no attribute 'send_audio_with_trim'`

- [ ] **Step 3: Write minimal implementation**

`bot/handlers/download_callbacks.py`:

1. Do importów dodaj:

```python
from bot.handlers.audio_delivery import AudioDeliveryError, TRIM_AVAILABLE_HINT, send_audio_with_trim
```

2. W `download_file` zastąp cały blok od `thumb_path = await asyncio.get_event_loop().run_in_executor(_executor, download_thumbnail, info, chat_download_path, True)` do `await update_status("Plik został wysłany!")` przez:

```python
                thumb_path = await asyncio.get_event_loop().run_in_executor(_executor, download_thumbnail, info, chat_download_path, True)
                trim_source = None
                try:
                    if media_type == "audio":
                        # Moves the file into the trim store when it can be kept,
                        # so the ✂️ button under the audio works for 24 h.
                        trim_source = await send_audio_with_trim(
                            context,
                            chat_id,
                            downloaded_file_path,
                            title=title,
                            caption=title,
                            thumb_path=thumb_path,
                            cancellation=cancellation,
                        )
                    elif use_mtproto:
                        from bot.mtproto import mtproto_unavailability_reason, send_video_mtproto

                        reason = mtproto_unavailability_reason()
                        if reason is not None:
                            raise RuntimeError(
                                f"Plik za duży dla Bot API ({file_size_mb:.0f} MB, limit: {TELEGRAM_UPLOAD_LIMIT_MB} MB).\n"
                                f"{reason}"
                            )
                        ok = await send_video_mtproto(chat_id, downloaded_file_path, caption=title, thumb_path=thumb_path, cancellation=cancellation)
                        if not ok:
                            raise RuntimeError("Wysyłanie pliku przez MTProto nie powiodło się.")
                    else:
                        with open(downloaded_file_path, "rb") as file_obj:
                            thumb_file = open(thumb_path, "rb") if thumb_path else None
                            try:
                                await context.bot.send_video(
                                    chat_id=chat_id,
                                    video=file_obj,
                                    caption=title,
                                    thumbnail=thumb_file,
                                    read_timeout=60,
                                    write_timeout=60,
                                )
                            finally:
                                if thumb_file:
                                    thumb_file.close()
                finally:
                    if thumb_path and os.path.exists(thumb_path):
                        try:
                            os.remove(thumb_path)
                        except OSError:
                            pass

                try:
                    os.remove(downloaded_file_path)
                except OSError:
                    pass
                record_download_for(context, chat_id, title, url, f"{media_type}_{format}", file_size_mb, time_range, selected_format=format)
                success_recorded = True
                if trim_source is not None:
                    await update_status(f"Plik został wysłany!\n\n{TRIM_AVAILABLE_HINT}")
                else:
                    await update_status("Plik został wysłany!")
```

3. W bloku `except Exception as exc:` tej funkcji zastąp:

```python
            if isinstance(exc, DownloadLimitError):
                await update_status(str(exc))
```

przez:

```python
            if isinstance(exc, (DownloadLimitError, AudioDeliveryError)):
                await update_status(str(exc))
```

`bot/handlers/spotify_callbacks.py`:

1. Do importów dodaj:

```python
from bot.handlers.audio_delivery import AudioDeliveryError, TRIM_AVAILABLE_HINT, send_audio_with_trim
```

2. W `download_spotify_resolved` zastąp gałąź `else:` (od `await update_status(f"Wysyłanie pliku ({file_size_mb:.1f} MB)...")` do `await update_status(f"Gotowe: {title}")` / `return True`) przez:

```python
        else:
            await update_status(f"Wysyłanie pliku ({file_size_mb:.1f} MB)...")
            trim_source = await send_audio_with_trim(
                context,
                chat_id,
                downloaded_file_path,
                title=title,
                performer=artist or None,
                caption=title[:200],
            )
            record_download_for(
                context,
                chat_id,
                title,
                _get_session_value(context, chat_id, "current_url", user_urls) or "",
                f"spotify_audio_{audio_format}",
                file_size_mb,
            )
            _clear_session_context_value(context, chat_id, "spotify_resolved", legacy_key="spotify_resolved")
            if trim_source is not None:
                await update_status(f"Gotowe: {title}\n\n{TRIM_AVAILABLE_HINT}")
                return True

        await update_status(f"Gotowe: {title}")
        return True
    except AudioDeliveryError as exc:
        await update_status(str(exc))
        return False
```

(istniejące `except Exception as exc:` i `finally:` zostają bez zmian; `finally` usuwa plik tylko gdy nadal istnieje, więc przeniesiony do magazynu plik jest bezpieczny).

3. W `download_spotify_video`:
   - na początku bloku `try:` (przed pobieraniem) dodaj `trim_source = None`,
   - zastąp blok od `if file_size_mb > TELEGRAM_UPLOAD_LIMIT_MB:` do końca `else:` z `send_video` przez:

```python
        if height is None:
            trim_source = await send_audio_with_trim(
                context, chat_id, downloaded_path, title=title, caption=title[:200]
            )
        elif file_size_mb > TELEGRAM_UPLOAD_LIMIT_MB:
            reason = _mtproto_unavailability_reason()
            if reason is not None:
                await update_status(
                    f"Plik za duży dla Bot API ({file_size_mb:.0f} MB, "
                    f"limit: {TELEGRAM_UPLOAD_LIMIT_MB} MB).\n{reason}"
                )
                return
            ok = await send_video_mtproto(chat_id, downloaded_path, caption=title[:200])
            if not ok:
                await update_status("Wysyłanie pliku przez MTProto nie powiodło się.")
                return
        else:
            with open(downloaded_path, "rb") as file_obj:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=file_obj,
                    caption=title[:200],
                    read_timeout=120,
                    write_timeout=120,
                )
```

   - zastąp `await update_status(f"Gotowe: {title}")` (po `_clear_session_context_value(... "spotify_video" ...)`) przez:

```python
        if trim_source is not None:
            await update_status(f"Gotowe: {title}\n\n{TRIM_AVAILABLE_HINT}")
        else:
            await update_status(f"Gotowe: {title}")
```

   - przed `except Exception as exc:` dodaj:

```python
    except AudioDeliveryError as exc:
        await update_status(str(exc))
```

   - jeśli import `send_audio_mtproto` w `spotify_callbacks.py` przestał być używany, usuń go z listy importów z `bot.mtproto`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_callback_download_handlers.py tests/test_callback_transcription_handlers.py tests/test_spotify_tracks.py tests/test_telegram_callbacks.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bot/handlers/download_callbacks.py bot/handlers/spotify_callbacks.py \
  tests/test_callback_download_handlers.py tests/test_callback_transcription_handlers.py
git commit -m "Offer trimming under every single sent audio"
```

---

### Task 7: „✂️ Pobierz i przytnij” dla podcastów i Spotify

**Files:**
- Modify: `bot/handlers/trim_callbacks.py` (dopisz `offer_trim_after_download`)
- Modify: `bot/handlers/download_callbacks.py` (`download_file`: parametr `trim_after`)
- Modify: `bot/handlers/spotify_callbacks.py` (`download_spotify_resolved`: parametr `trim_after`)
- Modify: `bot/handlers/common_ui.py` (przycisk w trzech klawiaturach)
- Modify: `bot/telegram_callbacks.py` (wrappery, `_handle_trim_download`, routing `trim_dl`)
- Modify: `tests/test_trim_callbacks.py`, `tests/test_callback_download_handlers.py`, `tests/test_callback_transcription_handlers.py`, `tests/test_telegram_callbacks.py`

**Interfaces:**
- Consumes: `probe_duration`, `AudioTrimError` (Task 2), `retain_source`, `has_room_for_sources` (Task 3), `start_trim_prompt`, `NO_ROOM_TEXT`, `ensure_trim_authorized` (Task 5).
- Produces:
  - `TRIM_AFTER_DOWNLOAD_INTRO: str` i `async offer_trim_after_download(context, *, chat_id, requester_id, file_path, title, performer, query) -> bool` w `bot.handlers.trim_callbacks`
  - `download_file(..., trim_after=False)` (oba: `download_callbacks` i wrapper w `telegram_callbacks`)
  - `download_spotify_resolved(..., trim_after=False)` (oba)
  - `trim_download_button() -> InlineKeyboardButton` w `bot.handlers.common_ui`
  - `_handle_trim_download(update, context, url) -> None` w `bot.telegram_callbacks`

- [ ] **Step 1: Write the failing tests**

Dopisz na końcu `tests/test_trim_callbacks.py`:

```python


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
```

Dopisz na końcu `tests/test_callback_download_handlers.py`:

```python


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
```

Dopisz na końcu `tests/test_callback_transcription_handlers.py`:

```python


def test_download_spotify_resolved_trim_after_skips_send(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock

    produced = tmp_path / "Host - Episode.mp3"
    produced.write_bytes(b"X" * 1024)
    offer = AsyncMock(return_value=True)
    sender = AsyncMock()
    monkeypatch.setattr(sc, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(sc, "download_resolved_audio", AsyncMock(return_value=str(produced)))
    monkeypatch.setattr(sc, "record_download_for", lambda *a, **k: None)
    monkeypatch.setattr(sc, "offer_trim_after_download", offer)
    monkeypatch.setattr(sc, "send_audio_with_trim", sender)
    update = _make_update("trim_dl", chat_id=123)

    result = asyncio.run(sc.download_spotify_resolved(
        update, _make_context(), {"source": "itunes", "title": "Episode", "artist": "Host"},
        "mp3", trim_after=True,
    ))

    assert result is True
    assert offer.await_args.kwargs["performer"] == "Host"
    sender.assert_not_awaited()
```

Dopisz na końcu `tests/test_telegram_callbacks.py`:

```python


def _callbacks(keyboard):
    return [button.callback_data for row in keyboard for button in row]


def test_podcast_keyboards_offer_download_and_trim():
    from bot.handlers.common_ui import build_main_keyboard, build_spotify_track_keyboard

    assert "trim_dl" in _callbacks(build_main_keyboard("castbox"))
    assert "trim_dl" in _callbacks(build_main_keyboard("spotify"))
    assert "trim_dl" in _callbacks(build_spotify_track_keyboard())
    assert "trim_dl" not in _callbacks(build_main_keyboard("youtube"))


def test_spotify_episode_offers_trim_only_with_fallback_audio():
    with_fallback = build_spotify_episode_keyboard(
        quality_options=[], has_native_audio=True, has_fallback_audio=True
    )
    native_only = build_spotify_episode_keyboard(
        quality_options=[], has_native_audio=True, has_fallback_audio=False
    )
    assert "trim_dl" in _callbacks(with_fallback)
    assert "trim_dl" not in _callbacks(native_only)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_trim_callbacks.py tests/test_callback_download_handlers.py tests/test_callback_transcription_handlers.py tests/test_telegram_callbacks.py -q`
Expected: FAIL — `AttributeError: ... 'offer_trim_after_download'`, `TypeError: ... unexpected keyword argument 'trim_after'`

- [ ] **Step 3: Write minimal implementation**

`bot/handlers/trim_callbacks.py`:

1. Rozszerz importy:

```python
from bot.services.audio_trim_service import (
    AudioTrimError,
    cut_fragment,
    fragment_filename,
    fragment_label,
    probe_duration,
)
from bot.services.trim_store import TrimSource, expires_at, load_source, retain_source
```

2. Pod `NO_ROOM_TEXT` dodaj:

```python
TRIM_AFTER_DOWNLOAD_INTRO = "Pobrano całość — plik nie zostanie wysłany, wytnij z niego fragmenty.\n\n"
```

3. Pod `start_trim_prompt` dodaj:

```python
async def offer_trim_after_download(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    chat_id: int,
    requester_id: int,
    file_path: str,
    title: str,
    performer: str | None,
    query,
) -> bool:
    """"✂️ Pobierz i przytnij": keep a fresh download in the store and ask for ranges."""

    try:
        duration = await probe_duration(Path(file_path))
    except AudioTrimError as exc:
        logging.error("Cannot probe downloaded audio for trimming: %s", exc)
        await safe_edit_message(
            query,
            "Nie udało się odczytać długości pobranego pliku. Pobierz całość przyciskiem Audio (MP3).",
        )
        return False
    source = retain_source(
        chat_id, file_path, title=title, performer=performer, duration_sec=round(duration)
    )
    if source is None:
        await safe_edit_message(query, NO_ROOM_TEXT)
        return False
    await start_trim_prompt(
        context,
        chat_id=chat_id,
        requester_id=requester_id,
        source=source,
        query=query,
        intro=TRIM_AFTER_DOWNLOAD_INTRO,
    )
    return True
```

`bot/handlers/download_callbacks.py`:

1. Do importów dodaj `from bot.handlers.trim_callbacks import offer_trim_after_download`.
2. Dopisz parametr `trim_after=False` na końcu sygnatury `download_file`.
3. Zastąp `time_range = _get_session_value(context, chat_id, "time_range", user_time_ranges)` przez:

```python
        # "✂️ Pobierz i przytnij" always fetches the whole file; ranges come later.
        time_range = None if trim_after else _get_session_value(context, chat_id, "time_range", user_time_ranges)
```

4. Na samym początku gałęzi `else:` po `if transcribe: ...` (przed `use_mtproto = file_size_mb > TELEGRAM_UPLOAD_LIMIT_MB`) dodaj:

```python
                if trim_after:
                    offered = await offer_trim_after_download(
                        context,
                        chat_id=chat_id,
                        requester_id=update.effective_user.id,
                        file_path=downloaded_file_path,
                        title=title,
                        performer=None,
                        query=query,
                    )
                    if offered:
                        record_download_for(context, chat_id, title, url, "audio_trim_source", file_size_mb, selected_format=format)
                        success_recorded = True
                    return
```

`bot/handlers/spotify_callbacks.py`:

1. Do importów dodaj `from bot.handlers.trim_callbacks import offer_trim_after_download`.
2. Dopisz parametr `trim_after: bool = False` na końcu sygnatury `download_spotify_resolved`.
3. Między gałęzią `if transcribe: ...` a `else:` wstaw:

```python
        elif trim_after:
            offered = await offer_trim_after_download(
                context,
                chat_id=chat_id,
                requester_id=update.effective_user.id,
                file_path=downloaded_file_path,
                title=title,
                performer=artist or None,
                query=query,
            )
            if offered:
                record_download_for(
                    context,
                    chat_id,
                    title,
                    _get_session_value(context, chat_id, "current_url", user_urls) or "",
                    "spotify_trim_source",
                    file_size_mb,
                )
            return offered
```

`bot/handlers/common_ui.py`:

1. Pod `escape_md` dodaj:

```python
def trim_download_button() -> InlineKeyboardButton:
    """Download the whole audio without sending it, then ask for trim ranges."""

    return InlineKeyboardButton("✂️ Pobierz i przytnij", callback_data="trim_dl")
```

2. W `build_main_keyboard`, w gałęzi `if is_podcast:` po wierszu `Audio (M4A)` dodaj `[trim_download_button()],`.
3. W `build_spotify_episode_keyboard`, w bloku `if has_fallback_audio:` po wierszu `Audio (M4A)` dodaj `keyboard.append([trim_download_button()])`.
4. W `build_spotify_track_keyboard` dodaj trzeci wiersz `[trim_download_button()],`.

`bot/telegram_callbacks.py`:

1. Rozszerz import z Task 5 i dodaj magazyn:

```python
from bot.handlers.trim_callbacks import NO_ROOM_TEXT, ensure_trim_authorized, handle_trim_callback
from bot.services.trim_store import has_room_for_sources
```

2. Wrapper `download_file`: dopisz parametr `trim_after=False` i przekaż `trim_after=trim_after` do `_extracted_download_file(...)`.
3. Wrapper `download_spotify_resolved`: dopisz parametr `trim_after: bool = False` i przekaż `trim_after=trim_after`.
4. Pod wrapperem `download_spotify_resolved` dodaj:

```python
async def _handle_trim_download(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> None:
    """"✂️ Pobierz i przytnij": fetch the whole audio without sending it.

    Lives here, not in trim_callbacks, because it calls the download flows
    that import trim_callbacks.
    """

    query = update.callback_query
    chat_id = update.effective_chat.id
    if not has_room_for_sources():
        await query.edit_message_text(NO_ROOM_TEXT)
        return
    platform = _get_session_context_value(context, chat_id, "platform", legacy_key="platform")
    if platform == "spotify":
        resolved = _get_session_context_value(context, chat_id, "spotify_resolved", legacy_key="spotify_resolved")
        if not resolved:
            await query.edit_message_text("Sesja Spotify wygasła. Wyślij link ponownie.")
            return
        await download_spotify_resolved(update, context, resolved, "mp3", trim_after=True)
        return
    await download_file(update, context, "audio", "mp3", url, trim_after=True)
```

5. W `handle_callback` przed `if data.startswith("dl_ig_"):` dodaj:

```python
    if data == "trim_dl":
        if await ensure_trim_authorized(update, context):
            await _handle_trim_download(update, context, url)
        return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_trim_callbacks.py tests/test_callback_download_handlers.py tests/test_callback_transcription_handlers.py tests/test_telegram_callbacks.py tests/test_spotify.py tests/test_platforms.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bot/handlers/trim_callbacks.py bot/handlers/download_callbacks.py bot/handlers/spotify_callbacks.py \
  bot/handlers/common_ui.py bot/telegram_callbacks.py tests/test_trim_callbacks.py \
  tests/test_callback_download_handlers.py tests/test_callback_transcription_handlers.py tests/test_telegram_callbacks.py
git commit -m "Add download-and-trim for podcasts and Spotify"
```

---

### Task 8: ✂️ dla pliku wysłanego do bota

**Files:**
- Modify: `bot/handlers/inbound_audio.py` (klawiatura, linia ~220)
- Modify: `bot/handlers/trim_callbacks.py` (gałąź `trim_upload`)
- Modify: `tests/test_trim_callbacks.py`, `tests/test_inbound_media_handlers.py`

**Interfaces:**
- Consumes: `probe_duration`, `retain_source(..., link=True)`, `start_trim_prompt` (wcześniejsze zadania); pola sesji `audio_file_path`, `audio_file_title`.
- Produces: callback `trim_upload` obsłużony w `handle_trim_callback`.

- [ ] **Step 1: Write the failing tests**

Dopisz na końcu `tests/test_trim_callbacks.py`:

```python


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
```

W `tests/test_inbound_media_handlers.py`, w klasie zawierającej `test_process_audio_file_downloads_and_sets_context`, dodaj:

```python
    def test_process_audio_file_offers_trim_button(self, tmp_path, monkeypatch):
        update = _make_update(user_id=777, chat_id=777)
        context = _make_context()
        progress_message = Mock()
        progress_message.edit_text = AsyncMock()
        update.message.reply_text = AsyncMock(return_value=progress_message)
        monkeypatch.setattr(tc, "DOWNLOAD_PATH", str(tmp_path / "downloads"))
        os.makedirs(tc.DOWNLOAD_PATH, exist_ok=True)
        tg_file = AsyncMock()

        async def download_to_drive(path):
            Path(path).write_bytes(b"abc")

        tg_file.download_to_drive = download_to_drive
        context.bot.get_file = AsyncMock(return_value=tg_file)

        _async(tc.process_audio_file(update, context, {
            "file_id": "x1", "file_size": 1024, "duration": 12,
            "mime_type": "audio/mpeg", "title": "abc",
        }))

        markup = progress_message.edit_text.await_args.kwargs["reply_markup"]
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        assert callbacks == ["audio_transcribe", "audio_transcribe_summary", "trim_upload"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_trim_callbacks.py tests/test_inbound_media_handlers.py -q`
Expected: FAIL — `"Nieobsługiwana akcja przycinania."` zamiast promptu; brak `trim_upload` w klawiaturze

- [ ] **Step 3: Write minimal implementation**

`bot/handlers/inbound_audio.py` — zastąp klawiaturę:

```python
        reply_markup = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Transkrypcja", callback_data="audio_transcribe")],
                [InlineKeyboardButton("Transkrypcja + Podsumowanie", callback_data="audio_transcribe_summary")],
                [InlineKeyboardButton("✂️ Przytnij", callback_data="trim_upload")],
            ]
        )
```

`bot/handlers/trim_callbacks.py`:

1. Nad `handle_trim_callback` dodaj:

```python
async def _start_upload_trim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat_id = update.effective_chat.id
    path = get_session_context_value(context, chat_id, "audio_file_path", legacy_key="audio_file_path")
    title = get_session_context_value(
        context, chat_id, "audio_file_title", legacy_key="audio_file_title", default="Plik audio"
    )
    if not path or not Path(path).is_file():
        await safe_edit_message(query, "Sesja wygasła — wyślij plik ponownie.")
        return
    try:
        duration = await probe_duration(Path(path))
    except AudioTrimError as exc:
        logging.error("Cannot probe uploaded audio for trimming: %s", exc)
        await safe_edit_message(query, "Nie udało się odczytać długości pliku audio. Wyślij go ponownie.")
        return
    # Hardlink, not move: transcribing the same upload keeps using the original path.
    source = retain_source(
        chat_id, path, title=title, performer=None, duration_sec=round(duration), link=True
    )
    if source is None:
        await safe_edit_message(query, "Na serwerze brakuje miejsca, żeby przechować plik do cięcia.")
        return
    # A new message keeps the upload menu (transcription buttons) usable.
    await start_trim_prompt(
        context, chat_id=chat_id, requester_id=update.effective_user.id, source=source
    )
```

2. W `handle_trim_callback`, przed końcowym `await safe_edit_message(query, "Nieobsługiwana akcja przycinania.")`, dodaj:

```python
    if data == "trim_upload":
        await _start_upload_trim(update, context)
        return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_trim_callbacks.py tests/test_inbound_media_handlers.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bot/handlers/inbound_audio.py bot/handlers/trim_callbacks.py tests/test_trim_callbacks.py tests/test_inbound_media_handlers.py
git commit -m "Let users trim audio files they send to the bot"
```

---

### Task 9: Nowa składnia w ✂️ przed pobraniem (YouTube i inne)

**Files:**
- Modify: `bot/handlers/inbound_media.py` (import + blok zakresu w `handle_youtube_link`, linie ~236–275)
- Modify: `bot/handlers/time_range_callbacks.py` (podpowiedź w `show_time_range_options`)
- Modify: `tests/test_inbound_media_handlers.py` (aktualizacja asercji + nowe testy)
- Modify: `tests/test_callback_download_handlers.py` (test podpowiedzi)

**Interfaces:**
- Consumes: `looks_like_time_ranges`, `parse_time_ranges`, `resolve_ranges`, `range_to_session_dict`, `format_timestamp`, `TimeRangeError` (Task 1).
- Produces: `_set_pre_download_range(update, context, chat_id, current_url, message_text) -> None` w `bot.handlers.inbound_media`.

- [ ] **Step 1: Write the failing tests**

W `tests/test_inbound_media_handlers.py`, w `test_handle_youtube_link_rejects_range_after_video_end`, zastąp asercję:

```python
        assert "jest poza plikiem (długość 2:00)" in update.message.reply_text.await_args.args[0]
```

W tej samej klasie `TestHandleYoutubeLinkTimeRange` dodaj:

```python
    def _setup(self, monkeypatch, *, text, duration=360):
        update = _make_update(text=text, user_id=333, chat_id=333)
        context = _make_context()
        _set_authorized_users(monkeypatch, {333})
        monkeypatch.setattr(tc, "handle_pin", AsyncMock(return_value=False))
        monkeypatch.setattr(tc, "check_rate_limit", lambda *_: True)
        monkeypatch.setattr(tc, "validate_youtube_url", lambda *_: True)
        tc.user_urls[333] = "https://www.youtube.com/watch?v=test"
        monkeypatch.setattr(tc, "get_video_info", lambda *_: {"duration": duration, "title": "Clip"})
        tc.block_until[333] = 0
        return update, context

    def test_open_ended_range_runs_to_the_end(self, monkeypatch):
        update, context = self._setup(monkeypatch, text="2:15-")
        _async(tc.handle_youtube_link(update, context))
        assert tc.user_time_ranges.get(333) == {"start": "2:15", "end": "6:00", "start_sec": 135, "end_sec": 360}

    def test_range_from_the_beginning(self, monkeypatch):
        update, context = self._setup(monkeypatch, text="-1:00")
        _async(tc.handle_youtube_link(update, context))
        assert tc.user_time_ranges.get(333)["start_sec"] == 0
        assert tc.user_time_ranges.get(333)["end_sec"] == 60

    def test_multiple_ranges_point_to_trim_button(self, monkeypatch):
        update, context = self._setup(monkeypatch, text="0:10-0:20, 1:00-2:00")
        _async(tc.handle_youtube_link(update, context))
        assert "Przed pobraniem ustawisz jeden zakres" in update.message.reply_text.await_args.args[0]
        assert tc.user_time_ranges.get(333) is None

    def test_open_range_needs_known_duration(self, monkeypatch):
        update, context = self._setup(monkeypatch, text="2:15-", duration=0)
        _async(tc.handle_youtube_link(update, context))
        assert "podaj oba końce" in update.message.reply_text.await_args.args[0]

    def test_closed_range_works_without_duration(self, monkeypatch):
        update, context = self._setup(monkeypatch, text="0:10-0:20", duration=0)
        _async(tc.handle_youtube_link(update, context))
        assert tc.user_time_ranges.get(333)["end_sec"] == 20
```

Dopisz na końcu `tests/test_callback_download_handlers.py`:

```python


def test_time_range_menu_lists_open_range_examples(monkeypatch):
    monkeypatch.setattr(_trc, "get_video_info", lambda _url: {"title": "Clip", "duration": 600})
    update, context = _make_update("time_range"), _make_context()

    asyncio.run(_trc.show_time_range_options(update, context, "https://youtube.com/watch?v=x"))

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "`2:15-` (do końca)" in text
    assert "`-5:00` (od początku)" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_inbound_media_handlers.py tests/test_callback_download_handlers.py -q`
Expected: FAIL — stary parser odrzuca `2:15-`, stary komunikat „przekracza czas trwania filmu”, stara podpowiedź

- [ ] **Step 3: Write minimal implementation**

`bot/handlers/inbound_media.py`:

1. Pod importem `parse_time_range as _shared_parse_time_range` dodaj:

```python
from bot.handlers.time_range import (
    TimeRangeError,
    format_timestamp,
    looks_like_time_ranges,
    parse_time_ranges,
    range_to_session_dict,
    resolve_ranges,
)
```

2. Nad `async def handle_youtube_link` dodaj:

```python
async def _set_pre_download_range(update, context, chat_id, current_url, message_text) -> None:
    """Validate a typed range for the pre-download ✂️ flow (yt-dlp sections)."""

    info = get_video_info(current_url)
    if not info:
        await update.message.reply_text(
            "Nie udało się odczytać informacji o materiale. Wyślij link ponownie."
        )
        return
    duration = int(info.get("duration") or 0)
    title = info.get("title", "Nieznany tytuł")

    try:
        specs = parse_time_ranges(message_text)
        if len(specs) > 1:
            raise TimeRangeError(
                "Przed pobraniem ustawisz jeden zakres. Kilka fragmentów wytniesz "
                "przyciskiem ✂️ Przytnij pod pobranym plikiem."
            )
        if duration:
            fragment = resolve_ranges(specs, duration)[0]
            start_sec, end_sec = fragment.start_sec, fragment.end_sec
        elif specs[0].end_sec is None:
            raise TimeRangeError(
                "Nie znam długości tego materiału — podaj oba końce, np. 2:15-10:00."
            )
        else:
            start_sec, end_sec = specs[0].start_sec or 0, specs[0].end_sec
    except TimeRangeError as exc:
        # No parse_mode: the message echoes user input.
        await update.message.reply_text(f"❌ Nieprawidłowy zakres!\n\n{exc}")
        return

    time_range = range_to_session_dict(start_sec, end_sec)
    _set_session_value(context, chat_id, "time_range", time_range, user_time_ranges)
    duration_str = format_timestamp(duration) if duration else "?"
    cur_platform = _get_session_context_value(
        context,
        chat_id,
        "platform",
        legacy_key="platform",
        default="youtube",
    )
    reply_markup = InlineKeyboardMarkup(_build_main_keyboard(cur_platform))
    await update.message.reply_text(
        f"✅ Ustawiono zakres: {time_range['start']} - {time_range['end']}\n\n"
        f"*{escape_md(title)}*\nCzas trwania: {duration_str}\n"
        f"✂️ Zakres: {time_range['start']} - {time_range['end']}\n\n"
        f"Wybierz format do pobrania:",
        reply_markup=reply_markup,
        parse_mode="Markdown",
    )
```

3. W `handle_youtube_link` zastąp cały blok:

```python
    current_url = _get_session_value(context, chat_id, "current_url", user_urls)
    if current_url:
        time_range = parse_time_range(message_text)
        if time_range:
            ...
                return
```

(czyli wszystko od `current_url = ...` do `return` kończącego odpowiedź „✅ Ustawiono zakres”, tuż przed `if is_user_blocked(...)`) przez:

```python
    current_url = _get_session_value(context, chat_id, "current_url", user_urls)
    if current_url and looks_like_time_ranges(message_text):
        await _set_pre_download_range(update, context, chat_id, current_url, message_text)
        return
```

`bot/handlers/time_range_callbacks.py` — w `show_time_range_options` zastąp:

```python
        f"💡 Możesz też wpisać własny zakres w formacie:\n"
        f"`0:30-5:45` lub `1:00:00-1:30:00`",
```

przez:

```python
        "💡 Możesz też wpisać własny zakres, np.:\n"
        "`0:30-5:45` · `2:15-` (do końca) · `-5:00` (od początku)",
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_inbound_media_handlers.py tests/test_callback_download_handlers.py tests/test_time_range.py tests/test_telegram_integration.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bot/handlers/inbound_media.py bot/handlers/time_range_callbacks.py \
  tests/test_inbound_media_handlers.py tests/test_callback_download_handlers.py
git commit -m "Accept open-ended ranges in the pre-download time range flow"
```

---

### Task 10: Dokumentacja i weryfikacja całości

**Files:**
- Modify: `README.md` (nowa sekcja w „Używanie bota”, drzewo projektu, „Funkcje”)

- [ ] **Step 1: README — sekcja użytkowa.** W `README.md` po sekcji `### Transkrypcja plików video` (przed `## Typy streszczeń`) wstaw:

```markdown
### Przycinanie audio
Bot wycina fragmenty audio bez ponownego kodowania (jakość bez zmian, dokładność ~25 ms).

- **Pod wysłanym plikiem audio** jest przycisk **✂️ Przytnij** — działa przez 24 h od wysłania
  (plik czeka na serwerze; przy wolnym miejscu < 5 GB bot go nie zatrzymuje i przycisku nie ma).
- **Podcasty (Spotify, Castbox) i utwory Spotify:** **✂️ Pobierz i przytnij** pobiera całość
  bez wysyłania i od razu pyta o zakres.
- **Własny plik:** wyślij MP3 lub wiadomość głosową i wybierz **✂️ Przytnij**.
- **YouTube, Vimeo, Instagram, LinkedIn:** **✂️ Zakres czasowy** przed pobraniem pobiera tylko
  jeden wskazany fragment.

Zapis zakresów:

| Wpis | Znaczenie |
|---|---|
| `1:30-4:45` | jeden fragment |
| `90-285`, `1:02:30-1:05:00` | sekundy albo H:MM:SS |
| `2:15-` | od 2:15 do końca |
| `-5:00` | od początku do 5:00 |
| `1:00-2:00, 5:30-7:00` | kilka fragmentów (maks. 10), każdy jako osobny plik |

Cięcie można przerwać komendą `/stop`. Po wysłaniu fragmentów przycisk **✂️ Tnij dalej**
pozwala wyciąć kolejne z tego samego źródła.
```

- [ ] **Step 2: README — funkcje i drzewo.** W sekcji `### Podstawowe` (pod `## Funkcje`) dopisz punkt:

```markdown
- Przycinanie audio według znaczników czasu (✂️), także kilku fragmentów naraz
```

W drzewie `## Struktura projektu` dopisz w `handlers/` (po `inbound_audio.py`):

```
│   │   ├── audio_delivery.py       # Wysyłka pojedynczego audio (Bot API/MTProto) z przyciskiem ✂️
│   │   ├── trim_callbacks.py       # Przepływ przycinania: prompt, zakresy, cięcie i wysyłka fragmentów
```

oraz w `services/` (po `archive_service.py`):

```
│       ├── audio_trim_service.py   # Cięcie fragmentów ffmpeg bez ponownego kodowania
│       ├── trim_store.py           # Magazyn źródeł do przycinania (24 h, meta.json)
```

i w `tests/` (po `test_callback_transcription_handlers.py`):

```
│   ├── test_time_ranges.py         # Testy parsera wielu i otwartych zakresów
│   ├── test_audio_trim_service.py  # Testy cięcia ffmpeg (wymaga ffmpeg)
│   ├── test_trim_store.py          # Testy magazynu źródeł i wygasania
│   ├── test_audio_delivery.py      # Testy wysyłki audio z ✂️
│   ├── test_trim_callbacks.py      # Testy przepływu przycinania
```

- [ ] **Step 3: Pełny przebieg testów**

Run: `df -h /tmp && .venv/bin/python -m pytest tests/ -q -rfE`
Expected: wszystkie przechodzą poza znaną lokalną porażką `test_no_legacy_node_manifests_in_project_root`. Każda inna porażka to regresja — napraw przed commitem.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "Document audio trimming"
```

- [ ] **Step 5: Lista E2E do wykonania po wdrożeniu na rpi5a** (wymaga zgody użytkownika na push i deploy — nie wykonywać samodzielnie). Przekaż użytkownikowi listę ze specyfikacji, sekcja 7.3:

1. YouTube → Audio (MP3) → ✂️ Przytnij → `0:10-0:20, 1:00-` → dwa pliki, długości się zgadzają.
2. Podcast Spotify (iTunes) → ✂️ Pobierz i przytnij → pełny plik **nie** przychodzi, prompt z długością → `-2:00` → jeden plik.
3. Castbox → ✂️ Pobierz i przytnij → `30:00-31:00`.
4. Wysłana notatka głosowa → ✂️ Przytnij → fragment w MP3.
5. Plik > 50 MB (MTProto) → przycisk ✂️ obecny pod audio.
6. Restart `ytdown.service` → ✂️ pod wcześniej wysłanym audio nadal działa.
7. YouTube przed pobraniem: ✂️ Zakres czasowy → `2:15-` → plik od 2:15.
8. `/stop` w trakcie cięcia wielu fragmentów.
