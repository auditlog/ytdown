# Spec: Komenda `/stop` — anulowanie długotrwałych operacji

- **Data:** 2026-05-03
- **Gałąź:** `develop`
- **Status:** zaakceptowany do implementacji

## 1. Cel i kontekst

Bot uruchamia kilka długotrwałych operacji, których użytkownik nie może w tej chwili przerwać bez restartu procesu:
- pobieranie playlisty (legacy per-item-send oraz nowy 7z flow),
- pobieranie pojedynczego pliku (yt-dlp + opcjonalnie MTProto upload),
- pakowanie 7z multi-volume,
- wysyłka kolejnych wolumenów,
- transkrypcja audio (Groq) i podsumowanie (Claude).

Specyfikacja dodaje **komendę `/stop`** — jedyny trigger anulowania — z listą aktywnych operacji per chat. Każdą operację można zatrzymać indywidualnie lub wszystkie naraz. Po anulowaniu workspace playlist (jeśli dotyczy) zostaje zachowany zgodnie z istniejącą retencją 60 minut, więc użytkownik może odzyskać częściową robotę przez przyciski recovery (`[Spakuj co mam]`, `[Wznów od X+1]`).

## 2. Decyzje produktowe (z brainstormingu)

| Pytanie | Decyzja |
|---|---|
| Scope cancela | Wszystko: playlisty (legacy + 7z), single-file download, 7z pack, send volumes, transkrypcja, podsumowanie, MTProto upload. |
| UX trigger | **Tylko** komenda `/stop`. Brak per-message przycisku. |
| Granularność | `/stop` listuje aktywne joby z osobnymi przyciskami `[Zatrzymaj N]` plus `[Zatrzymaj wszystkie]` i `[Anuluj listę]`. |
| Behaviour po cancel (pliki) | Workspace playlist zostaje przy istniejącej retencji 60 min; status pokazuje `[Spakuj co mam] [Usuń]` dla recovery. Single-file: yt-dlp posprząta `.part` automatycznie. |
| Mechanizm techniczny | Single shared `JobCancellation` (asyncio.Event + opcjonalny subprocess + opcjonalny pyrogram task), per-warstwa konsumpcja w sposób natywny dla danego kontekstu. |
| 6h zombie cleanup | TAK, log jako WARNING. |
| `cancelled_reason` w descriptorze | TAK (telemetria/diagnostyka). |

## 3. Architektura

### 3.1 Nowy moduł `bot/jobs.py` (~150 linii)

Wystawia `JobRegistry` (singleton) i typy stanu cancela. Brak zależności od `bot.handlers.*` ani `bot.services.*`.

```python
@dataclass
class JobCancellation:
    """Cancellation handle shared across async/threadpool/subprocess layers."""
    job_id: str
    event: asyncio.Event
    process: asyncio.subprocess.Process | None = None
    pyrogram_task: asyncio.Task | None = None
    cancelled_reason: str | None = None

@dataclass
class JobDescriptor:
    """User-facing description of a running job, listed by /stop."""
    job_id: str
    chat_id: int
    kind: Literal[
        "playlist_legacy", "playlist_zip", "single_dl",
        "transcription", "summary", "archive_pack", "archive_send",
    ]
    label: str
    started_at: datetime

class JobRegistry:
    """Thread-safe per-chat registry; in-memory only."""
    def register(chat_id: int, descriptor: JobDescriptor) -> JobCancellation
    def get(job_id: str) -> JobCancellation | None
    def list_for_chat(chat_id: int) -> list[JobDescriptor]
    def update_label(job_id: str, label: str) -> None
    def cancel(job_id: str, reason: str = "user via /stop") -> bool
    def unregister(job_id: str) -> None
    def purge_dead(threshold: timedelta) -> int    # called by cleanup.py
```

Importy dozwolone: stdlib (`asyncio`, `dataclasses`, `datetime`, `threading`, `secrets`, `logging`, `typing`).

### 3.2 Komenda `/stop` w `bot/telegram_commands.py`

Nowy handler + 4 callback handlery (`stop_<job_id>`, `stop_all`, `stop_dismiss`, `stop_refresh`).

```
/stop                        → list active jobs (or "brak aktywnych operacji.")
stop_<job_id>                → cancel single job
stop_all                     → cancel every job in this chat
stop_refresh                 → re-render the list (label/state changes)
stop_dismiss                 → delete the list message
```

Wiadomość:
```
Aktywne operacje (2):

1. Playlist 7z (audio mp3) — pobieranie [12/30] (5 min)
2. Pojedynczy plik (video best) — pakowanie 7z (2 min)

[Zatrzymaj 1]   [Zatrzymaj 2]
[Zatrzymaj wszystkie]
[Odśwież]      [Anuluj listę]
```

Wiek (`(N min)`) liczony z `descriptor.started_at` na każdym renderze listy.

### 3.3 Modyfikacje istniejących modułów

| Plik | Zmiana |
|---|---|
| `bot/security_limits.py` | Dodać `JOB_DEAD_AGE_HOURS = 6`, `JOB_TERMINATE_GRACE_SEC = 1.0`. |
| `bot/services/archive_service.py` | `download_playlist_into`, `send_volumes`, `execute_playlist_archive_flow`, `execute_single_file_archive_flow` przyjmują `cancellation: JobCancellation`. Pętle sprawdzają `event.is_set()` per iteration. Wrapper-y rejestrują/unregisterują job w `try/finally`. |
| `bot/archive.py` | `pack_to_volumes(..., cancellation=None)`. Zapisuje `process` po spawn. `_stream_7z_progress` sprawdza event w pętli readline. Po cancel: `process.terminate()` → grace 1s → `process.kill()`. Cleanup `.7z.NNN` w `except`. |
| `bot/services/download_service.py` | `execute_download(..., cancellation=None)`. Progress hook raise `yt_dlp.utils.DownloadError("cancelled")` gdy event set. |
| `bot/handlers/playlist_callbacks.py` | Legacy `download_playlist`: rejestruje job, pętla sprawdza event, na cancel kasuje workspace (legacy nie zachowuje plików). |
| `bot/handlers/download_callbacks.py` | `download_file` rejestruje job. Cancellation propagowane do `execute_download`, transcription pipeline, MTProto. |
| `bot/transcription_pipeline.py` | Pętla po chunkach sprawdza event przed każdym Groq call. |
| `bot/transcription_providers.py` | `generate_summary` zarejestrowany jako `cancellation.pyrogram_task` (slot generic dla async tasków); cancellation propaguje `CancelledError`. |
| `bot/mtproto.py` | `send_*_mtproto` zapisuje wewnętrzny task w `cancellation.pyrogram_task`. |
| `bot/session_store.py` | Dodać `ArchivePartialState` dataclass (workspace, downloaded files, title, media_type, format_choice, use_mtproto, created_at) + `partial_archive_workspaces: SessionFieldMap` i analogicznie pole w `SessionState`. |
| `bot/cleanup.py` | Co cykl wywołuje `job_registry.purge_dead(timedelta(hours=JOB_DEAD_AGE_HOURS))` (log warning) plus `_purge_partial_archive_workspaces(PLAYLIST_ARCHIVE_RETENTION_MIN)` analogicznie do istniejących cleanupów. |
| `bot/runtime.py` | Brak zmian (registry to globalny singleton, jak `session_store`). |

## 4. Flow

### 4.1 Sekwencja `/stop` → `[Zatrzymaj 1]`

1. Handler odbiera callback `stop_<job_id>`.
2. `cancellation = job_registry.get(job_id)` → jeśli `None`, edit wiadomości listy: `"Operacja już zakończona."`. Return.
3. `job_registry.cancel(job_id, reason="user via /stop")`:
   a. `cancellation.cancelled_reason = reason`
   b. `cancellation.event.set()`
   c. Jeśli `cancellation.process`: `process.terminate()` → `await asyncio.wait_for(process.wait(), JOB_TERMINATE_GRACE_SEC)` → na timeout `process.kill()`.
   d. Jeśli `cancellation.pyrogram_task`: `task.cancel()`.
4. Edit wiadomości listy: `"Wysłano sygnał zatrzymania dla operacji 1. Czekam na potwierdzenie..."` + przycisk `[Odśwież]`.
5. Sama operacja, po wykryciu event/sygnału, edytuje swoją wiadomość statusową na `⏹` + przyciski recovery (per kind, sekcja 4.2). W `finally` wywołuje `job_registry.unregister(job_id)`.

### 4.2 Statusy końcowe per kind

| Kind | Status po cancel | Przyciski recovery |
|---|---|---|
| `playlist_legacy` | `"⏹ Zatrzymano playlistę. Pobrano N/M (pliki usunięte)."` | brak (legacy nie zachowuje) |
| `playlist_zip` | `"⏹ Zatrzymano. Pobrano N/M plików."` | `[Spakuj co mam]` (`arc_pack_partial_<token>`), `[Usuń teraz]` (`arc_purge_<token>`) |
| `single_dl` | `"⏹ Zatrzymano pobieranie. Plik usunięty."` | brak |
| `transcription` | `"⏹ Zatrzymano transkrypcję."` | brak (audio source idzie do retencji `cleanup_old_files`) |
| `summary` | `"⏹ Zatrzymano podsumowanie. Transkrypcja jest dostępna."` | brak (transkrypcja już wysłana wcześniej) |
| `archive_pack` | `"⏹ Zatrzymano w trakcie pakowania. Wolumeny usunięte."` | brak (workspace zostaje 60 min, użytkownik może puścić to samo zadanie ponownie) |
| `archive_send` | `"⏹ Zatrzymano wysyłkę. Wysłano X/Y paczek."` | `[Wznów od X+1]` (`arc_resend_<token>_<X>`), `[Usuń teraz]` |

### 4.3 `[Spakuj co mam]` po cancelu playlist_zip

Nowa dataclass i mapa w `bot/session_store.py`:

```python
@dataclass
class ArchivePartialState:
    """Workspace state captured at cancel time, for [Spakuj co mam] retry."""
    workspace: Path
    downloaded: list[Path]
    title: str
    media_type: str
    format_choice: str
    use_mtproto: bool
    created_at: datetime

partial_archive_workspaces = SessionFieldMap(session_store, "partial_archive_workspaces")
```

Nowy callback `arc_pack_partial_<token>`:
1. Po cancel `download_playlist_into` zwraca już-pobrane pliki, `execute_playlist_archive_flow` zapisuje `ArchivePartialState` i pokazuje przycisk z tokenem.
2. Klik wywołuje `archive_service.execute_partial_archive_flow(update, context, chat_id, token)`:
   - Czyta `ArchivePartialState` z `partial_archive_workspaces`.
   - Pakuje `pack_to_volumes(state.downloaded, ...)` (z nową `JobCancellation` zarejestrowaną w registry).
   - Wysyła `send_volumes(...)`.
   - Rejestruje `ArchivedDeliveryState` jak normalny sukces, czyści `partial_archive_workspaces[chat_id][token]`.

`partial_archive_workspaces` sprzątane w `cleanup.py` razem z `pending_archive_jobs` i `archived_deliveries` (ten sam retention `PLAYLIST_ARCHIVE_RETENTION_MIN`).

## 5. Konfiguracja, transport, error handling

### 5.1 Stałe w `bot/security_limits.py`

```python
# Cleanup of stale (zombie) entries in JobRegistry. Defends /stop list
# against operations that never unregistered due to bugs or crashes.
JOB_DEAD_AGE_HOURS = 6

# Grace between SIGTERM and SIGKILL when terminating a 7z subprocess
# attached to a JobCancellation. Long enough for 7z to finish writing
# its current 1 MiB block, short enough to not block /stop UX.
JOB_TERMINATE_GRACE_SEC = 1.0
```

### 5.2 Race conditions (rozważone)

| ID | Sytuacja | Mitigacja |
|---|---|---|
| R1 | Cancel między `create_subprocess_exec` a `cancellation.process = process` | `pack_to_volumes` przypisuje `process` przed pierwszym `await readline()`; latencja akceptowalna (~1-2 s). |
| R2 | Cancel zaraz po normalnym `unregister` | `cancel()` zwraca `bool`; handler `stop_<id>` na `False` edytuje "Operacja już zakończona." |
| R3 | Stale job w liście | `try/finally unregister` w każdej warstwie + cykliczny `purge_dead(6h)` w `cleanup.py` (log warning). |
| R4 | Dwa równoległe `/stop` na ten sam job | Idempotentne: event set, terminate na zakończonym procesie to no-op. |
| R5 | `/stop` w grupie / od innego usera | Bot operuje per chat, nie per user — zgodne z istniejącym modelem `pl_cancel`. |

### 5.3 Cleanup półprodukcji

- **yt-dlp**: `.part` plik usuwany automatycznie po `DownloadError`.
- **7z**: `pack_to_volumes` w `except`/`finally` po terminate kasuje wszystkie `<basename>.7z.NNN` z workspace (mogą być corrupted).
- **Workspace playlist**: zachowany dla recovery (60 min retencja `cleanup.py:_purge_archive_workspaces`).
- **Workspace single-file `big_*`**: jak dotychczas.
- **Audio dla transkrypcji**: zachowany w `downloads/<chat_id>/`, idzie do 24h retencji `cleanup_old_files`.

### 5.4 Logowanie

- `logging.info` na: `register`, `cancel(reason=...)`, `unregister`.
- `logging.warning` na: SIGTERM → SIGKILL fallback, `purge_dead` zombie usunięty.
- Spójne z resztą bota (`bot/mtproto.py`, `bot/cleanup.py`).

## 6. Plan testów

### 6.1 Nowe pliki

**`tests/test_jobs.py`** (~200 linii):
- `test_register_returns_unique_job_id`
- `test_register_creates_unset_event`
- `test_list_for_chat_returns_active_descriptors`
- `test_list_for_chat_excludes_other_chats`
- `test_cancel_sets_event_and_returns_true`
- `test_cancel_unknown_job_returns_false`
- `test_cancel_terminates_attached_subprocess`
- `test_cancel_kills_subprocess_when_terminate_times_out`
- `test_cancel_cancels_attached_pyrogram_task`
- `test_unregister_removes_descriptor`
- `test_update_label_changes_descriptor_label`
- `test_registry_is_thread_safe`
- `test_purge_dead_jobs_removes_old_entries`
- `test_cancelled_reason_propagates_to_descriptor`

### 6.2 Modyfikacje istniejących

**`tests/test_archive_service.py`**:
- `test_download_playlist_into_breaks_on_cancel`
- `test_send_volumes_breaks_on_cancel`
- `test_execute_playlist_archive_flow_registers_and_unregisters`

**`tests/test_archive.py`**:
- `test_pack_to_volumes_terminates_subprocess_on_cancel`
- `test_pack_to_volumes_kills_when_terminate_times_out`
- `test_pack_to_volumes_cleans_partial_volumes_on_cancel`

**`tests/test_download_service.py`**:
- `test_execute_download_progress_hook_raises_on_cancel`

**`tests/test_telegram_commands.py`**:
- `test_stop_command_lists_active_jobs`
- `test_stop_command_empty_message_when_no_jobs`
- `test_stop_callback_cancels_job`
- `test_stop_all_callback_cancels_every_job_in_chat`
- `test_stop_callback_handles_already_finished_job`

**`tests/test_cleanup.py`**:
- `test_purge_dead_jobs_removes_after_threshold`

**`tests/test_mtproto.py`**:
- `test_send_video_mtproto_attaches_task_to_cancellation`

### 6.3 Manualna checklist E2E

1. Pobranie playlisty 30×, `/stop` po 5 → workspace ma 5 plików, status z `[Spakuj co mam]`, klik → wysyła paczki.
2. Pakowanie 7z dla playlisty 1 GB+, `/stop` w trakcie → status `⏹`, brak `.7z.001` w workspace, retencja zachowuje resztę.
3. Wysyłka 8 wolumenów MTProto, `/stop` po 3 → 3 wolumeny w czacie, status z `[Wznów od 4]`, klik → reszta idzie.
4. Transkrypcja 1h podcast, `/stop` w połowie → audio kasowane, status `⏹`.
5. Dwa równoległe joby (playlist + transkrypcja), `/stop` listuje oba, klik `[Zatrzymaj wszystkie]` → oba zatrzymane.
6. `/stop` gdy żadnej operacji → "Brak aktywnych operacji."
7. Stale job (manualne wywołanie `purge_dead` z `started_at = now - 7h`) → znika z listy, log warning.

### 6.4 Cele pokrycia

- `bot/jobs.py` — > 95 %
- Modyfikacje w istniejących plikach — istniejące pokrycie nie spada.
- E2E manual checklist (pkt 7.4) wykonywany przez użytkownika.

## 7. Poza scope

- Per-message przyciski `[⏹]` (świadome odrzucenie — wybrano tylko `/stop`).
- Persystencja `JobRegistry` na crash bota (in-memory zgodnie z istniejącym wzorcem `SessionStore`).
- Cancel pojedynczego API-call w trakcie request HTTP do Groq — pojedynczy chunk (~30 s) dokończy się.
- Cancel reklamacji częściowo zuploadowanych plików w Telegramie (Telegram sam usuwa po ~24 h).
- Per-user permissions dla `/stop` w grupach (per-chat zachowanie zgodne z istniejącym modelem `pl_cancel`).

## 8. Aktualizacje

- 2026-05-03: spec zaakceptowany w brainstormingu.
