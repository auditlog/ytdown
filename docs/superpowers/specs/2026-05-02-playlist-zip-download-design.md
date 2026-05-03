# Spec: Tryb „pobierz playlistę / duży plik jako 7z"

- **Data:** 2026-05-02
- **Gałąź:** `develop`
- **Status:** zaakceptowany do implementacji

## 1. Cel i kontekst

Bot udostępnia dziś dwie ścieżki:
- pobranie pojedynczego pliku z YouTube/Spotify/Instagram i wysłanie go do Telegrama,
- pobranie playlisty YouTube w pętli — każdy element trafia do Telegrama jako osobna wiadomość, a po wysłaniu plik źródłowy jest natychmiast usuwany z dysku.

Dla użytkownika końcowego (Telegram Desktop na Windows 11) oznacza to, że przy długiej playliście trzeba ręcznie pobrać każdy element z Telegrama osobno. Specyfikacja dodaje **drugi tryb pobierania całej playlisty**, w którym bot zatrzymuje pliki na dysku, pakuje je do 7z multi-volume i wysyła użytkownikowi tylko paczki (`<title>.7z.001`, `.002`, …).

Ta sama logika jest fallbackiem dla **pojedynczego pliku przekraczającego limit wysyłki Telegrama**: zamiast „za duży, nie wyślę", bot proponuje wybór „Anuluj / Wyślij jako 7z".

## 2. Decyzje produktowe (przyjęte w brainstormingu)

| Pytanie | Decyzja |
|---|---|
| Limit wysyłki Telegrama | Hybrydowo: auto-detekcja MTProto. Z MTProto wolumen 7z = ~1900 MB, bez MTProto = ~49 MB. |
| Format kontenera | 7z multi-volume (`-t7z -v<size>m -mx0`). 7-Zip 26 jest zainstalowany na hoście (`p7zip-full`). Brak natywnego wsparcia w Windows Explorer jest akceptowanym kompromisem. |
| UX trybu playlisty | Wariant **C**: w menu playlisty dochodzi 4 dodatkowe przyciski „… jako 7z" obok obecnych 4. Stary tryb (każdy plik osobno) zostaje bez zmian. |
| Trigger fallbacku dla pojedynczego pliku | Wariant **A**: tylko gdy estymowany rozmiar pliku przekracza limit Telegrama w aktualnej konfiguracji. `MAX_FILE_SIZE_MB = 1000` zostaje (chroni dysk dla single-file flow). |
| Limit per-element w trybie 7z (playlist) | Nowa stała `MAX_ARCHIVE_ITEM_SIZE_MB = 10240` (10 GB). Pozwala wrzucić do playlist-7z duże filmy 4K, których single-file flow nie zaakceptowałby. |
| Lokalizacja plików tymczasowych | Per-playlist subfolder `downloads/<chat_id>/pl_<slug>_<timestamp>/`. Dla pojedynczego pliku w trybie 7z analogiczny `big_<slug>_<timestamp>/`. |
| Retencja po sukcesie | 60 minut, integrowane z istniejącym `bot/cleanup.py`. Stała `PLAYLIST_ARCHIVE_RETENTION_MIN = 60`. Pliki źródłowe + wolumeny zostają w katalogu, żeby umożliwić retry wysyłki bez ponownego pobierania. |
| Implementacja pakowania | System binary `7z` przez `asyncio.create_subprocess_exec`. Brak fallbacku do `py7zr`. Jeśli `7z` nie jest w `PATH` przy starcie, przyciski „… jako 7z" się nie pojawiają. |
| Anuluj single-file 7z | Plik usuwany natychmiast. |
| Przyciski po sukcesie | `[Wyślij wszystkie paczki ponownie]` + `[Usuń teraz]` zostają. |
| Nazewnictwo wolumenów | Czytelne (z tytułem), polskie znaki transliterowane do ASCII (`unicodedata.normalize('NFKD', ...)`). |

## 3. Architektura

### 3.1 Nowy moduł `bot/archive.py`

Niskopoziomowy, czysto funkcyjny wrapper na `7z` CLI. Brak zależności od warstwy Telegrama. ~120 linii.

API:

```python
def volume_size_for(use_mtproto: bool) -> int:
    """Zwraca MTPROTO_VOLUME_SIZE_MB albo BOTAPI_VOLUME_SIZE_MB."""

def transliterate_to_ascii(text: str) -> str:
    """Translit polskich znaków do ASCII przez NFKD. Zachowuje spacje, myślniki, cyfry."""

def compute_archive_basename(slug: str, ts: datetime) -> str:
    """Deterministyczny prefix wolumenów: <slug>_<YYYYMMDD-HHMMSS>."""

async def pack_to_volumes(
    sources: list[Path],
    dest_basename: Path,
    volume_size_mb: int,
    *,
    progress_cb: Callable[[str], Awaitable[None]] | None = None,
) -> list[Path]:
    """Uruchamia `7z a -t7z -v<size>m -mx0 -mmt=on dest.7z <sources...>`.
    Zwraca posortowaną listę utworzonych wolumenów (.7z.001, .002, ...).
    Raises RuntimeError przy non-zero exit, ValueError przy pustym sources.
    Raportuje progres przez progress_cb (parsing stdout 7z, throttle 2 s)."""

def is_7z_available() -> bool:
    """shutil.which('7z') is not None."""
```

### 3.2 Nowy moduł `bot/services/archive_service.py`

Orkiestracja end-to-end. Zawiera całą logikę wspólną dla playlist-flow i single-file-fallback. ~200 linii.

API:

```python
@dataclass
class ArchiveJobState:
    """Persystencja in-memory pendingowego zadania 7z (single-file)."""
    file_path: Path
    title: str
    media_type: str
    format_choice: str
    file_size_mb: float
    use_mtproto: bool
    created_at: datetime

@dataclass
class ArchivedDeliveryState:
    """Persystencja in-memory wysłanej paczki (do retry/purge)."""
    workspace: Path
    volumes: list[Path]
    caption_prefix: str
    use_mtproto: bool
    created_at: datetime

def prepare_playlist_workspace(chat_id: int, playlist_title: str, *, prefix: str = "pl") -> Path
async def download_playlist_into(
    workspace: Path,
    entries: list[dict],
    media_type: str,
    format_choice: str,
    executor: ThreadPoolExecutor,
    status_cb: Callable[[str], Awaitable[None]],
) -> tuple[list[Path], list[str]]:
    """Pobiera każdy item do workspace bez wysyłki. Sprawdza MAX_ARCHIVE_ITEM_SIZE_MB.
    Zwraca (downloaded_paths, failed_titles)."""

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
    """Wysyła wolumeny [start_index:] jako document (Bot API ≤49 MB lub MTProto)."""

async def execute_playlist_archive_flow(
    update,
    context,
    chat_id: int,
    playlist: dict,
    media_type: str,
    format_choice: str,
    executor: ThreadPoolExecutor,
) -> None:
    """End-to-end: prepare workspace → download all → pack → send volumes → status."""

async def execute_single_file_archive_flow(
    update,
    context,
    chat_id: int,
    token: str,
) -> None:
    """End-to-end dla single-file fallback. Pobiera ArchiveJobState po tokenie z
    pending_archive_jobs[chat_id], przenosi plik do workspace 'big_*', pakuje, wysyła."""

def schedule_workspace_for_cleanup(workspace: Path) -> None
def register_pending_archive_job(chat_id: int, state: ArchiveJobState) -> str
def register_archived_delivery(chat_id: int, state: ArchivedDeliveryState) -> str
```

### 3.3 Modyfikacje istniejących modułów

| Plik | Zmiana |
|---|---|
| `bot/security_limits.py` | Dodać `MAX_ARCHIVE_ITEM_SIZE_MB`, `MTPROTO_VOLUME_SIZE_MB`, `BOTAPI_VOLUME_SIZE_MB`, `PLAYLIST_ARCHIVE_RETENTION_MIN`. |
| `bot/services/playlist_service.py` | `PlaylistDownloadChoice.as_archive: bool`. `parse_playlist_download_choice` rozpoznaje prefix `pl_zip_dl_`. `build_playlist_message` wstawia 4 dodatkowe przyciski gdy `archive_available=True`. |
| `bot/handlers/playlist_callbacks.py` | Gałąź dla `as_archive=True` deleguje do `archive_service.execute_playlist_archive_flow`. Stary `download_playlist` (per-item send) zostaje bez zmian dla `as_archive=False`. |
| `bot/handlers/download_callbacks.py` | W `download_file`, gdy `file_size_mb > volume_size_for(use_mtproto)`: zarejestruj `ArchiveJobState`, pokaż wybór `[Wyślij jako 7z] [Anuluj]`. Dodać dispatching dla `arc_split_*`, `arc_cancel_*`, `arc_resend_*`, `arc_purge_*`. |
| `bot/mtproto.py` | Dodać `send_document_mtproto(chat_id, file_path, caption, file_name)`. Wzorowane na `send_video_mtproto`, ale wywołuje `client.send_document(...)`. |
| `bot/cleanup.py` | Helper `_purge_archive_workspaces(chat_dir, retention_min)`: skanuje katalogi pasujące do `pl_*` / `big_*`, sprawdza `mtime` i obecność `.lock`, usuwa po przekroczeniu retencji. Sprzątanie `pending_archive_jobs` po `created_at`. |
| `bot/session_store.py` | `pending_archive_jobs: dict[int, dict[str, ArchiveJobState]]`. `archived_deliveries: dict[int, dict[str, ArchivedDeliveryState]]`. |
| `bot/runtime.py` | Przy starcie: `archive_available = is_7z_available()`. Wystawić jako `get_runtime_value("archive_available")` lub atrybut `RuntimeServices`. |

## 4. Flow: playlista → 7z

### 4.1 Rozszerzone menu playlisty

`build_playlist_message` po zmianie:

```
Pobierz wszystkie — Audio MP3              (pl_dl_audio_mp3)
Pobierz wszystkie — Audio MP3 jako 7z      (pl_zip_dl_audio_mp3)    [tylko gdy archive_available]
Pobierz wszystkie — Audio M4A              (pl_dl_audio_m4a)
Pobierz wszystkie — Audio M4A jako 7z      (pl_zip_dl_audio_m4a)
Pobierz wszystkie — Video (najlepsza)      (pl_dl_video_best)
Pobierz wszystkie — Video (najlepsza) 7z   (pl_zip_dl_video_best)
Pobierz wszystkie — Video 720p             (pl_dl_video_720p)
Pobierz wszystkie — Video 720p jako 7z     (pl_zip_dl_video_720p)
[Pokaż więcej…]
[Anuluj]
```

Callback prefix `pl_zip_dl_` (10 znaków + `audio_mp3` = 19, mieści się w limicie 64 B).

### 4.2 Sekwencja zdarzeń (po kliknięciu `pl_zip_dl_audio_mp3`)

1. `archive_service.prepare_playlist_workspace(chat_id, playlist_title, prefix="pl")` →
   `downloads/<chat_id>/pl_<slug>_<YYYYMMDD-HHMMSS>/`. `<slug>` = `sanitize_filename(transliterate_to_ascii(playlist_title))[:60]` — najpierw translit polskich znaków do ASCII, potem usunięcie znaków niedozwolonych systemowo.
2. Detekcja transportu: `use_mtproto = mtproto_unavailability_reason() is None`.
   `volume_size_mb = volume_size_for(use_mtproto)`.
3. Inicjalny status (jedna wiadomość, edytowana w pętli):
   `Playlista → 7z (audio mp3)\n[0/N] Pobieranie...`
4. Pętla po `entries`:
   - Pobranie itemu **do workspace** (`chat_download_path=workspace`), bez wysyłki, bez `os.remove`.
   - Wczesna walidacja: `estimate_download_size(plan)` > `MAX_ARCHIVE_ITEM_SIZE_MB` → item pominięty, tytuł na `failed_titles`.
   - Status: `[i/N] Pobieranie: <title>...`
5. Po pętli:
   - `len(failed) == N` → `shutil.rmtree(workspace)`, status `Nie udało się pobrać żadnego elementu.`. Koniec.
   - W przeciwnym razie tworzymy lock-file `<workspace>/.lock`.
6. Status: `Pakowanie do 7z (vol_size=<N> MB)...` →
   `archive.pack_to_volumes(downloaded_paths, workspace / "<slug>_<format>", volume_size_mb, progress_cb=...)`.
   Progress edytuje status co 2 s (parsing stdout 7z dla procentu i nazwy aktualnego pliku).
7. Status: `Pakowanie OK: M paczek. Wysyłanie...`
8. `archive_service.send_volumes(bot, chat_id, volumes, caption_prefix=f"{playlist_title} ({format})", use_mtproto, status_cb=...)`.
   Per-wolumen status: `Wysyłanie [j/M] (<size> MB)...`
   Caption per-wolumen: `<playlist_title> [j/M] (<format>)`.
   Każdy wolumen jako osobna wiadomość-dokument.
9. Po sukcesie:
   - Usuwamy `.lock`. Rejestrujemy `ArchivedDeliveryState` w `archived_deliveries[chat_id][token]`.
   - Status końcowy:
     ```
     Playlista zakończona.
     Pobrano: K/N
     Spakowano: K plików → M paczek 7z
     Wysłano: M/M
     Folder zostanie usunięty po 60 min.
     ```
     plus przyciski `[Wyślij wszystkie paczki ponownie]` (`arc_resend_<token>_0`), `[Usuń teraz]` (`arc_purge_<token>`),
     plus pierwsze 5 `failed_titles` jeśli były.
   - `schedule_workspace_for_cleanup(workspace)` — wpisanie do `cleanup.py`'s tracking.

### 4.3 Recovery przy błędzie wysyłki wolumenu

- Wysyłka stop, status:
  `Błąd wysyłki paczki [3/M]: <error>. Folder zachowany.`
  + przyciski `[Ponów od [3/M]]` (`arc_resend_<token>_2`, indeksowane od 0), `[Anuluj]` (`arc_purge_<token>`).
- Token wskazuje na `archived_deliveries[chat_id][token]`. `arc_resend_<token>_<from>` wywołuje
  `send_volumes(..., start_index=from)`.

## 5. Flow: pojedynczy plik > limit Telegrama → 7z

Trigger w `bot/handlers/download_callbacks.download_file` (linie ~514–533 obecnego kodu): gałąź `use_mtproto = file_size_mb > TELEGRAM_UPLOAD_LIMIT_MB`, w której dziś `mtproto_unavailability_reason() is not None` rzuca `RuntimeError`. Tam podstawiamy fallback 7z. Warunek precyzyjnie: **plik został pobrany i `file_size_mb > volume_size_for(use_mtproto)`** (z MTProto: `file_size_mb > 1900` — w obecnym configu blokowane wcześniej przez `MAX_FILE_SIZE_MB=1000`, więc praktycznie nieosiągalne; bez MTProto: `file_size_mb > 49` — częsty przypadek). Wtedy:

1. Generujemy `token` (8 znaków hex).
2. Zapisujemy `pending_archive_jobs[chat_id][token] = ArchiveJobState(...)`.
3. Status:
   ```
   Plik za duży dla Telegrama: <X> MB > limit <Y> MB.
   Mogę spakować go w wolumeny 7z (po <Y> MB) i wysłać paczki.
   ```
   + przyciski `[Wyślij jako 7z]` (`arc_split_<token>`), `[Anuluj]` (`arc_cancel_<token>`).
4. **`arc_cancel_<token>`** → `os.remove(file_path)`, `pending_archive_jobs[chat_id].pop(token, None)`,
   status: `Anulowano. Plik usunięty.`
5. **`arc_split_<token>`** → `archive_service.execute_single_file_archive_flow`:
   - Tworzymy workspace `downloads/<chat_id>/big_<slug>_<ts>/`, przenosimy `file_path` do środka.
   - Tworzymy `<workspace>/.lock`.
   - Status: `Pakowanie do 7z (vol_size=<Y> MB)...` → `archive.pack_to_volumes`.
   - Status: `Pakowanie OK: M paczek. Wysyłanie...` → `send_volumes`.
   - Po sukcesie: rejestrujemy `ArchivedDeliveryState`, usuwamy `.lock`, status końcowy z
     `[Wyślij wszystkie paczki ponownie] [Usuń teraz]`.
6. **TTL pendingowego joba**: `cleanup.py` co cykl sprawdza `pending_archive_jobs`; gdy
   `created_at + PLAYLIST_ARCHIVE_RETENTION_MIN < now` → kasuje plik i usuwa wpis.

Spójność z playlistą: ten sam `pack_to_volumes`, ten sam `send_volumes`, ten sam mechanizm tokenów.
Single-file to przypadek brzegowy `sources` o długości 1.

## 6. Konfiguracja, transport, error handling

### 6.1 Stałe w `bot/security_limits.py`

```python
MAX_FILE_SIZE_MB = 1000               # bez zmian (single-file flow, poza fallbackiem 7z)
MAX_ARCHIVE_ITEM_SIZE_MB = 10240      # NOWE — per-element w trybie playlist 7z
TELEGRAM_UPLOAD_LIMIT_MB = 50         # bez zmian (Bot API)
MTPROTO_VOLUME_SIZE_MB = 1900         # NOWE
BOTAPI_VOLUME_SIZE_MB = 49            # NOWE
PLAYLIST_ARCHIVE_RETENTION_MIN = 60   # NOWE
```

### 6.2 Transport per-wolumen

`send_volumes` decyduje per-wolumen na podstawie rozmiaru: `≤ TELEGRAM_UPLOAD_LIMIT_MB` (49 MB)
→ `context.bot.send_document(...)`, większy → `send_document_mtproto(...)`. W praktyce:
- z MTProto: wszystkie wolumeny = 1900 MB → wszystkie idą MTProto;
- bez MTProto: wszystkie wolumeny = 49 MB → wszystkie idą Bot API.
Sprawdzenie `mtproto_unavailability_reason()` dla wolumenu > 49 MB bez MTProto → `RuntimeError`.

### 6.3 `send_document_mtproto`

Nowa funkcja w `bot/mtproto.py`:

```python
async def send_document_mtproto(
    chat_id: int,
    file_path: str,
    caption: str | None = None,
    file_name: str | None = None,
) -> bool:
    """Send a document file via MTProto (up to 2 GB)."""
```

Implementacyjnie analogiczna do `send_video_mtproto`, ale z `client.send_document(...)`.
`file_name` (opcjonalne) wymusza nazwę widoczną w Telegramie (np. `playlist_audio_mp3.7z.001`).

### 6.4 Cleanup w `bot/cleanup.py`

Nowy helper:

```python
def _purge_archive_workspaces(chat_dir: Path, retention_min: int) -> int:
    """Iteruje po podkatalogach pasujących do pl_*/big_*. Pomija jeśli istnieje .lock.
    Usuwa shutil.rmtree gdy wiek katalogu (mtime) > retention_min minut.
    Zwraca liczbę usuniętych katalogów."""
```

Wywołane razem z istniejącym 24h cleanupem w tym samym scheduler-thread.

Sprzątanie `pending_archive_jobs`:

```python
def _purge_pending_archive_jobs(retention_min: int) -> None:
    """Iteruje pending_archive_jobs, usuwa wpisy z created_at + retention < now,
    kasuje powiązany plik z dysku."""
```

### 6.5 Error handling

| Sytuacja | Obsługa |
|---|---|
| `7z` exit != 0 | `pack_to_volumes` raises `RuntimeError("7z failed: <stderr[:200]>")`. `archive_service` w `except` usuwa workspace, edytuje status: `Pakowanie nie powiodło się: <err>`. |
| `7z` brak w `PATH` (start bota) | `archive_available = False`. Przyciski 7z nie pojawiają się w menu playlisty. Single-file fallback pokazuje `Funkcja 7z niedostępna — administrator nie zainstalował p7zip-full.` z przyciskiem `[Anuluj]`. |
| Pobranie itemu playlisty fails | Tytuł na `failed_titles`, pętla kontynuuje. `len(failed) == N` → cleanup workspace + status. |
| Item playlisty > `MAX_ARCHIVE_ITEM_SIZE_MB` | Pominięty, na `failed_titles` z notatką `(za duży: X MB)`. |
| Wysyłka wolumenu fails | Mechanizm `[Ponów od [j/M]]` (sekcja 4.3). Workspace zostaje, retencja 60 min. |
| `arc_cancel` single-file | `os.remove(file_path)`, czyść state, status `Anulowano. Plik usunięty.`. |
| Restart bota w trakcie pakowania | `pending_archive_jobs` jest in-memory (zgodne z istniejącym wzorcem `user_urls`). Workspace z `.lock` zostaje, ale po 60 min `cleanup.py` go usunie (sprawdzenie `.lock` blokuje cleanup tylko gdy katalog jest młody; po 24h każdy katalog idzie do kasacji niezależnie od `.lock`, jako safety net). |
| Disk full przy pakowaniu | `7z` exit != 0, łapane jak wyżej. Przed startem pakowania logujemy `shutil.disk_usage` informacyjnie. |

### 6.6 Logowanie

`logging.info` na: workspace utworzony, pobranie itemu OK, pakowanie start, pakowanie OK (M wolumenów),
wysyłka wolumenu OK. `logging.error` na: pobranie itemu fail, pakowanie fail, wysyłka wolumenu fail,
cleanup fail. Spójne z `bot/mtproto.py`.

## 7. Plan testów

### 7.1 Nowe pliki

**`tests/test_archive.py`** (~150 linii):
- `test_volume_size_for_mtproto_returns_1900`
- `test_volume_size_for_botapi_returns_49`
- `test_transliterate_to_ascii_polish_letters`
- `test_transliterate_preserves_safe_chars`
- `test_pack_to_volumes_invokes_7z_with_correct_args` (mock subprocess)
- `test_pack_to_volumes_returns_sorted_volume_paths`
- `test_pack_to_volumes_raises_when_7z_exits_nonzero`
- `test_pack_to_volumes_raises_on_empty_sources`
- `test_pack_to_volumes_real_7z_small_volumes` (integration; skip gdy `not is_7z_available()`)

**`tests/test_archive_service.py`** (~200 linii):
- `test_prepare_workspace_creates_pl_prefix_dir`
- `test_prepare_workspace_uses_transliterated_slug`
- `test_download_playlist_into_keeps_files_after_download`
- `test_download_playlist_into_returns_empty_when_all_fail`
- `test_download_playlist_into_returns_failed_titles_on_partial`
- `test_download_playlist_into_respects_max_archive_item_size`
- `test_send_volumes_uses_botapi_for_small_volumes`
- `test_send_volumes_uses_mtproto_for_large_volumes`
- `test_send_volumes_raises_when_volume_too_large_and_no_mtproto`
- `test_send_volumes_caption_format`
- `test_send_volumes_resumes_from_start_index`
- `test_pending_archive_job_token_is_unique`
- `test_archive_lock_file_blocks_cleanup`

### 7.2 Modyfikacje istniejących

**`tests/test_playlist_service.py`**:
- `test_build_playlist_message_includes_zip_buttons_when_archive_available`
- `test_build_playlist_message_hides_zip_buttons_when_archive_unavailable`
- `test_parse_playlist_download_choice_recognizes_zip_prefix`
- `test_parse_playlist_download_choice_legacy_prefix_unchanged`

**`tests/test_callback_download_handlers.py`**:
- `test_oversized_single_file_offers_archive_choice`
- `test_arc_cancel_removes_file_immediately`
- `test_arc_split_dispatches_to_archive_service`
- `test_arc_resend_starts_from_given_index`
- `test_arc_purge_immediately_removes_workspace`

**`tests/test_playlist.py`** (handler-level):
- `test_handle_playlist_callback_pl_zip_dl_dispatches_to_archive_flow`

**`tests/test_cleanup.py`**:
- `test_purge_archive_workspaces_removes_old_pl_dirs`
- `test_purge_archive_workspaces_keeps_recent_dirs`
- `test_purge_archive_workspaces_respects_lock_file`
- `test_purge_archive_workspaces_cleans_pending_jobs`

**`tests/test_mtproto.py`**:
- `test_send_document_mtproto_invokes_send_document`
- `test_send_document_mtproto_returns_false_without_pyrogram`
- `test_send_document_mtproto_returns_false_without_credentials`
- `test_send_document_mtproto_returns_false_on_invalid_api_id`

### 7.3 Manualna checklist E2E

1. Pobranie playlisty 5×audio_mp3 jako 7z → 1 wolumen `<plist>_audio_mp3.7z.001`, otwiera się w 7-Zip Windows.
2. Pobranie playlisty 30×video_720p jako 7z → kilka wolumenów, sumarycznie > 1900 MB.
3. Sztuczne wymuszenie braku MTProto → wolumeny po 49 MB, wszystkie wysłane przez Bot API.
4. Ubicie sieci podczas wysyłki wolumenu `.003` → przycisk `[Ponów od [3/M]]` ponawia bez ponownego pobierania.
5. Pozostawienie workspace przez 65 min → `cleanup.py` usuwa folder.
6. Single-file > limit (np. testowo `MAX_FILE_SIZE_MB=200` + plik 250 MB w Bot API mode) → przyciski `[Wyślij jako 7z] [Anuluj]`.

### 7.4 Cele pokrycia

- `bot/archive.py` — > 95 %
- `bot/services/archive_service.py` — > 90 %
- Modyfikacje w istniejących plikach — istniejące pokrycie nie spada.

## 8. Poza scope

- Migracja istniejącego trybu „każdy plik osobno" do trybu 7z.
- Persystencja `pending_archive_jobs` / `archived_deliveries` na crash bota.
- Konfiguracja per-user rozmiaru wolumenu.
- Wsparcie innych formatów archiwum (ZIP split, RAR, tar.gz).
- Inkrementalne wysyłanie wolumenów w trakcie pakowania (streaming) — pakujemy wszystko, dopiero potem wysyłamy.
- Hashowanie / sumy kontrolne wolumenów.

## 9. Aktualizacje

- 2026-05-02: spec zaakceptowany w brainstormingu.
