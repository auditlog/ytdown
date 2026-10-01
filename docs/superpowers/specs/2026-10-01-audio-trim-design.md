# Spec: Przycinanie audio według znaczników czasu

- **Data:** 2026-10-01
- **Gałąź:** `develop`
- **Status:** do przeglądu

## 1. Cel i kontekst

Bot wysyła dziś zawsze cały plik audio. Jedyny sposób na fragment to przycisk
„✂️ Zakres czasowy” przed pobraniem, który:

- działa tylko dla platform obsługiwanych przez yt-dlp z `hide_time_range=False`
  (YouTube, Vimeo, Instagram, LinkedIn),
- jest ukryty dla podcastów (Spotify, Castbox) oraz TikToka i X,
- przyjmuje jeden zamknięty zakres (`1:30-4:45`),
- nie pozwala przyciąć pliku już wysłanego ani pliku, który użytkownik wysłał do bota.

Specyfikacja dodaje **wspólny mechanizm przycinania audio** (ffmpeg na pliku
lokalnym) z trzema punktami wejścia oraz rozszerza składnię zakresów w całym bocie.

Zastosowania zgłoszone przez użytkownika: wycięcie jednego fragmentu, obcięcie
początku lub końca (intro, reklamy), kilka fragmentów naraz, krótkie klipy
i dzwonki (liczy się dokładność do sekundy).

## 2. Decyzje produktowe (z brainstormingu)

| Pytanie | Decyzja |
|---|---|
| Gdzie można ciąć | (1) pod każdym pojedynczym audio wysłanym przez bota, (2) plik audio wysłany do bota, (3) podcasty i utwory Spotify przez „✂️ Pobierz i przytnij”, (4) YouTube itp. — istniejące ✂️ przed pobraniem. |
| Jak długo działa ✂️ pod wysłanym audio | **24 h** — źródło zostaje na dysku. |
| Podejście | Przycisk ✂️ + magazyn źródeł na dysku (wariant A). Odrzucony wariant B: odpowiedź na wiadomość z audio i pobieranie pliku z Telegrama (limity 20/200 MB, słaba odkrywalność). |
| Podcasty i Spotify przed pobraniem | Przycisk **„✂️ Pobierz i przytnij”**: pobiera całość bez wysyłania, od razu pyta o zakres(y). Odrzucone: menu „✂️ Zakres czasowy” jak w YouTube (osobne źródło długości dla Spotify, odbudowa trzech menu Spotify przy „Powrót”, tylko jeden zakres). |
| Kilka fragmentów | Tak — każdy jako osobny plik, maks. 10 na raz. |
| Zakresy otwarte | Tak — `2:15-` (do końca) i `-5:00` (od początku). |
| Dokładność | ~25 ms (jedna ramka MP3), bez ponownego kodowania. |
| Format fragmentu | Taki sam jak źródła (MP3/M4A/FLAC). |
| Wideo | Poza zakresem — tylko audio. |

## 3. Składnia zakresów

Jeden parser dla wszystkich wejść (✂️ Przytnij, ✂️ Pobierz i przytnij, ✂️ Zakres
czasowy przed pobraniem).

### 3.1 Gramatyka

- **Znacznik czasu:** `S` (dowolna liczba cyfr, np. `90`), `M:SS` (minuty bez
  limitu, np. `102:30`), `H:MM:SS`. W `M:SS` sekundy < 60; w `H:MM:SS` minuty
  i sekundy < 60.
- **Zakres:** `START-KONIEC`, `START-` (do końca), `-KONIEC` (od początku).
  Myślnik może być `-`, `–` lub `—`; spacje wokół myślnika są ignorowane.
- **Lista zakresów:** rozdzielona `,`, `;` lub nową linią. Maks. `TRIM_MAX_RANGES` (10).
- Fragmenty mogą na siebie zachodzić — każdy jest osobnym plikiem.

| Wpis | Wynik dla pliku 1:42:10 |
|---|---|
| `1:30-4:45` | 90–285 s |
| `90-285` | 90–285 s |
| `1:02:30-1:05:00` | 3750–3900 s |
| `2:15-` | 135 s – koniec |
| `-5:00` | 0–300 s |
| `1:00-2:00, 5:30-7:00` | dwa pliki |

### 3.2 Walidacja i komunikaty

Parsowanie i walidacja zgłaszają `TimeRangeError` z polskim komunikatem dla
użytkownika. Długość pliku do walidacji to wynik `ffprobe` zaokrąglony do
pełnej sekundy (ta sama wartość jest wyświetlana).

| Sytuacja | Komunikat |
|---|---|
| Nieczytelny fragment | „Nie rozumiem zakresu „{fragment}”. Przykłady: `1:30-4:45` · `2:15-` · `-5:00` · `1:00-2:00, 5:30-7:00`” |
| Sam myślnik | „Zakres „-” musi mieć początek albo koniec.” |
| Sekundy/minuty ≥ 60 | „„{znacznik}” nie jest poprawnym czasem — minuty i sekundy muszą być mniejsze niż 60.” |
| Początek ≥ koniec | „W zakresie {a}-{b} początek musi być wcześniej niż koniec.” |
| Początek poza plikiem | „Początek {a} jest poza plikiem (długość {d}).” |
| Koniec poza plikiem | „Koniec {b} jest poza plikiem (długość {d}). Wpisz `{a}-`, żeby ciąć do końca.” |
| Zakres = cały plik | „Zakres {a}-{b} obejmuje cały plik — nie ma czego ciąć.” |
| Za dużo zakresów | „Możesz podać maksymalnie 10 fragmentów naraz (podano {n}).” |

Pierwszy napotkany błąd przerywa całe zlecenie — nic nie jest cięte, dopóki
wszystkie zakresy nie są poprawne.

### 3.3 Przed pobraniem (YouTube, Vimeo, Instagram, LinkedIn)

Istniejący przepływ ✂️ Zakres czasowy zostaje (yt-dlp `download_sections`
pobiera tylko fragment), ale wpisany tekst przechodzi przez nowy parser:

- akceptowane są zakresy otwarte; rozwiązywane względem długości z `get_video_info`,
- gdy długość jest nieznana, a zakres otwarty: „Nie znam długości tego materiału —
  podaj oba końce, np. `2:15-10:00`.”,
- więcej niż jeden zakres: „Przed pobraniem ustawisz jeden zakres. Kilka
  fragmentów wytniesz przyciskiem ✂️ Przytnij pod pobranym plikiem.”,
- tekst, który nie wygląda na zakres (`looks_like_time_ranges` = tylko cyfry,
  `:`, myślniki, separatory i spacje), przechodzi dalej jak dziś,
- podpowiedź w menu ✂️ pokazuje nowe przykłady (`2:15-`, `-5:00`).

W sesji zakres zostaje zapisany w obecnym formacie
(`start`, `end`, `start_sec`, `end_sec`), więc downloader się nie zmienia.

## 4. Architektura

### 4.1 Nowe moduły

**`bot/handlers/time_range.py` (rozszerzenie)** — parser bez zależności od Telegrama:

```python
@dataclass(frozen=True)
class RangeSpec:
    start_sec: int | None  # None = from the beginning
    end_sec: int | None    # None = to the end

@dataclass(frozen=True)
class ResolvedRange:
    start_sec: int
    end_sec: int     # resolved against duration, used for labels and validation
    open_end: bool   # True for "2:15-": ffmpeg cuts to EOF without -t

class TimeRangeError(ValueError):
    """Carries a Polish, user-facing message."""

def looks_like_time_ranges(text: str) -> bool
def parse_time_ranges(text: str, *, max_ranges: int = TRIM_MAX_RANGES) -> list[RangeSpec]
def resolve_ranges(specs: list[RangeSpec], duration_sec: int) -> list[ResolvedRange]
def format_timestamp(seconds: int) -> str  # "M:SS" or "H:MM:SS"
```

`parse_time_range()` (pojedynczy zamknięty zakres) zostaje jako cienka nakładka
dla dotychczasowych wywołań i testów.

**`bot/services/audio_trim_service.py`** — ffmpeg/ffprobe:

```python
async def probe_duration(path: Path) -> float
async def cut_fragment(
    source: Path, fragment: ResolvedRange, dest: Path,
    *, title_tag: str, cancellation: JobCancellation | None = None,
) -> Path
def fragment_filename(title: str, start_sec: int, end_sec: int, ext: str) -> str
```

- Polecenie: `ffmpeg -v error -y -ss START -i SRC [-t DŁUGOŚĆ] -map 0:a:0 -map 0:v:0? -c copy -map_metadata 0 -metadata title=… DEST`.
  `-t` pomijane, gdy `fragment.open_end` (cięcie do EOF); w nazwie i podpisie
  koniec i tak jest pokazywany jako długość pliku (`[1:20:00–1:42:10]`).
- Okładka (strumień `attached_pic`) jest kopiowana, jeśli istnieje. Gdy ffmpeg
  odrzuci okładkę (zdarza się w M4A), jedna ponowna próba z samym `-map 0:a:0`.
- Proces uruchamiany przez `asyncio.create_subprocess_exec` i przypinany do
  `cancellation.process`, więc `/stop` go zabija. Limit czasu: `FFMPEG_TIMEOUT`.
- Nazwa pliku: `sanitize_filename(f"{title} [{a}–{b}]") + ext`; tag `title`
  w metadanych: `{title} [{a}–{b}]`.
- Dokładność zweryfikowana 2026-10-01 na 20-minutowym MP3 VBR (libmp3lame
  `-q:a 5`, jak w yt-dlp): `-ss` przed `-i` oraz po `-i` dają błąd ≤ 25 ms.

**`bot/services/trim_store.py`** — magazyn źródeł na dysku:

```python
@dataclass(frozen=True)
class TrimSource:
    token: str
    chat_id: int
    path: Path
    title: str
    performer: str | None
    duration_sec: int
    created_at: datetime

def retain_source(chat_id, file_path, *, title, performer, duration_sec, link=False) -> TrimSource | None
def load_source(chat_id, token) -> TrimSource | None
def discard_source(source: TrimSource) -> None
def expires_at(source: TrimSource) -> datetime
def purge_expired_sources(chat_dir: Path) -> int
def has_room_for_sources() -> bool
```

- Układ: `downloads/<chat_id>/trim_<token>/source.<ext>` + `meta.json`
  (title, performer, duration_sec, created_at w UTC ISO).
- Token: `secrets.token_urlsafe(8)` (11 znaków), walidowany wyrażeniem
  `^[A-Za-z0-9_-]{11}$` przed złożeniem ścieżki. Wyszukiwanie wyłącznie
  w `downloads/<chat_id>/`, więc token z innego czatu nie zadziała.
- `retain_source` przenosi plik (`shutil.move`), a przy `link=True` tworzy
  hardlink z kopią jako zapasem (plik wysłany do bota, którego używa też
  transkrypcja).
- Zwraca `None` (bez wyjątku), gdy rozszerzenie spoza `{.mp3, .m4a, .flac}` albo
  `has_room_for_sources()` jest fałszywe (wolne < `TRIM_MIN_FREE_DISK_GB`).
- `load_source` zwraca `None`, gdy token jest niepoprawny, katalog/plik nie
  istnieje albo minęło `TRIM_SOURCE_RETENTION_HOURS`.
- Stan żyje na dysku, więc przyciski działają po restarcie bota.

**`bot/handlers/audio_delivery.py`** — jedno miejsce wysyłki pojedynczego audio:

```python
class AudioDeliveryError(RuntimeError):
    """Carries a Polish, user-facing message."""

async def send_audio_file(
    context, chat_id, path, *, title, performer=None, caption=None,
    thumb_path=None, buttons: list[tuple[str, str]] | None = None,
    cancellation=None,
) -> None

async def send_audio_with_trim(
    context, chat_id, path, *, title, performer=None, caption=None,
    thumb_path=None, cancellation=None,
) -> TrimSource | None
```

- `send_audio_file` wybiera Bot API (≤ `TELEGRAM_UPLOAD_LIMIT_MB`) albo MTProto;
  gdy plik przekracza dostępny limit, zgłasza `AudioDeliveryError`
  („Fragment ma {x} MB — za dużo do wysłania. Wybierz krótszy zakres.” dla
  fragmentów; dla pełnych plików zachowuje obecne komunikaty).
- `send_audio_with_trim`: sonduje długość → `retain_source` → wysyła z przyciskiem
  `[✂️ Przytnij]` (`trim_src_<token>`). Gdy wysyłka się nie powiedzie,
  `discard_source`. Gdy magazyn odmówi (format, dysk), wysyła bez przycisku
  i kasuje plik jak dziś.

**`bot/handlers/trim_callbacks.py`** — przepływ cięcia:

```python
async def handle_trim_callback(update, context, data: str) -> None
async def handle_pending_trim_input(update, context) -> bool
async def start_trim_prompt(context, chat_id, requester_id, source, *, message=None) -> None
```

### 4.2 Modyfikacje istniejących modułów

| Plik | Zmiana |
|---|---|
| `bot/security_limits.py` | `TRIM_SOURCE_RETENTION_HOURS = 24`, `TRIM_PENDING_INPUT_TIMEOUT_MIN = 10`, `TRIM_MAX_RANGES = 10`, `TRIM_MIN_FREE_DISK_GB = 5`. |
| `bot/jobs.py` | `JobKind` += `"trim"`. |
| `bot/session_store.py`, `bot/session_context.py` | Pole sesji `pending_trim: PendingTrimInput | None` (token, requester_id, created_at). |
| `bot/mtproto.py` | `send_audio_mtproto(..., buttons=None)` — budowa `pyrogram.types.InlineKeyboardMarkup`. |
| `bot/handlers/download_callbacks.py` | Gałąź audio bez transkrypcji używa `send_audio_with_trim` zamiast inline'owej wysyłki + `os.remove`. Nowy parametr `trim_after: bool` — pobiera, nie wysyła, `retain_source`, `start_trim_prompt`. Oferta 7z pomijana przy `trim_after`. |
| `bot/handlers/spotify_callbacks.py` | `download_spotify_resolved` i audio-only `download_spotify_video` używają `send_audio_with_trim`; `download_spotify_resolved(..., trim_after=True)` jak wyżej. |
| `bot/handlers/common_ui.py` | `[✂️ Pobierz i przytnij]` (`trim_dl`) w gałęzi podcastowej `build_main_keyboard`, w `build_spotify_episode_keyboard` (tylko gdy `has_fallback_audio`) i w `build_spotify_track_keyboard`. |
| `bot/handlers/inbound_audio.py` | `[✂️ Przytnij]` (`trim_upload`) obok „Transkrypcja”. |
| `bot/handlers/inbound_media.py` | Wywołanie `handle_pending_trim_input` zaraz po `handle_pending_transcript_prompt`, **przed** parsowaniem zakresu dla `current_url`. Parsowanie zakresu przed pobraniem przez `parse_time_ranges` + `resolve_ranges` (sekcja 3.3). |
| `bot/handlers/time_range_callbacks.py` | Nowe przykłady w podpowiedzi menu ✂️. |
| `bot/telegram_callbacks.py` | Routing `data.startswith("trim_")` → `handle_trim_callback`. |
| `bot/cleanup.py` | Wywołanie `purge_expired_sources` obok `_purge_archive_workspaces`. |
| `README.md` | Sekcja „Przycinanie audio” (po polsku). |

## 5. Przepływy

### 5.1 ✂️ pod wysłanym audio

1. Pobieranie kończy się jak dziś; `send_audio_with_trim` wysyła audio
   z przyciskiem `[✂️ Przytnij]`.
2. Klik `trim_src_<token>` → `load_source`. Brak/wygasł: edycja odpowiedzi
   „Plik wygasł (minęły 24 h) albo został usunięty. Wyślij to MP3 do bota,
   żeby je przyciąć.” i koniec.
3. `start_trim_prompt` wysyła nową wiadomość (nie edytuje audio):

   ```
   ✂️ Przycinanie: <tytuł>
   Długość: 1:42:10

   Wpisz zakres (albo kilka po przecinku):
   1:30-4:45 — jeden fragment
   2:15- — od 2:15 do końca
   -5:00 — od początku do 5:00
   1:00-2:00, 5:30-7:00 — dwa pliki

   Plik jest dostępny do 2 paź, 14:05.
   [Anuluj]
   ```

   i zapisuje `pending_trim`. Ustawienie `pending_trim` czyści oczekujące
   polecenie transkrypcji (i odwrotnie) — w danym czacie czeka najwyżej jedno
   wejście tekstowe.
4. Tekst użytkownika w `handle_pending_trim_input`:
   - zawiera URL → `pending_trim` czyszczony, tekst obsługiwany normalnie
     (zwraca `False`),
   - `pending_trim` starszy niż 10 min → czyszczony, zwraca `False`,
   - inny tekst → `parse_time_ranges` + `resolve_ranges`; błąd → komunikat
     z sekcji 3.2, `pending_trim` zostaje (można poprawić),
   - `check_rate_limit(user_id)` fałszywe → istniejący komunikat rate limitu
     (ścieżka tekstowa nie liczy go dziś przed obsługą oczekujących wejść,
     więc wywołanie jest jawne),
   - w czacie trwa już zadanie `trim` → „Poczekaj, aż skończę poprzednie
     cięcie, albo przerwij je komendą /stop.”
5. Poprawne zakresy: `pending_trim` czyszczony, rejestracja zadania `trim`
   w `job_registry`. Wiadomość z promptem jest edytowana w status (przycisk
   `[Anuluj]` znika): „✂️ Tnę fragment 1/2…” / „Wysyłam fragment 1/2…”.
   Każdy fragment: `cut_fragment` do `trim_<token>/out_<n>.<ext>` →
   `send_audio_file` (podpis `{tytuł} [12:00–15:30]`, bez przycisku) →
   usunięcie pliku fragmentu w `finally`.
6. Ta sama wiadomość jako status końcowy: „Gotowe: 2 fragmenty.” +
   `[✂️ Tnij dalej]` (`trim_src_<token>`).

### 5.2 ✂️ Pobierz i przytnij (podcasty, Spotify)

1. Klik `trim_dl`. Najpierw `has_room_for_sources()`; gdy fałsz: „Na serwerze
   brakuje miejsca, żeby przechować plik do cięcia. Pobierz całość przyciskiem
   Audio (MP3).”
2. Platforma `spotify` → `download_spotify_resolved(..., "mp3", trim_after=True)`;
   pozostałe (Castbox) → `download_file(..., "audio", "mp3", url, trim_after=True)`.
   Zakres z sesji (`time_range`) jest ignorowany przy `trim_after`.
3. Po pobraniu: status „Pobrano całość ({d}). Plik nie zostanie wysłany —
   wytnij z niego fragmenty.”, `retain_source`, `start_trim_prompt`.
   Dalej jak w 5.1 od kroku 4.

### 5.3 Plik wysłany do bota

1. `inbound_audio` jak dziś pobiera i konwertuje do MP3, pokazuje
   `[Transkrypcja] [Transkrypcja + Podsumowanie] [✂️ Przytnij]`.
2. Klik `trim_upload` → `audio_file_path` z sesji; brak lub plik nie istnieje:
   „Sesja wygasła — wyślij plik ponownie.”
3. `retain_source(..., link=True)` → `start_trim_prompt`. Dalej jak w 5.1.

### 5.4 Callbacki

| `callback_data` | Akcja |
|---|---|
| `trim_src_<token>` | Prompt dla źródła z magazynu (5.1). |
| `trim_dl` | Pobierz i przytnij (5.2). |
| `trim_upload` | Przytnij plik wysłany do bota (5.3). |
| `trim_cancel` | Czyści `pending_trim`, edytuje prompt: „Anulowano przycinanie.” |

**Autoryzacja:** `handle_callback` nie ma globalnej bramki — pozostałe
callbacki polegają na stanie sesji ustawianym dopiero po PIN-ie. Magazyn
na dysku przetrwa jednak restart i `/logout`, więc `handle_trim_callback`
jawnie wywołuje `_is_authorized(context, user_id)`; brak autoryzacji →
„Wymagane uwierzytelnienie — wyślij kod PIN.” Wejście tekstowe jest już
chronione, bo `handle_youtube_link` sprawdza autoryzację przed obsługą
oczekujących wejść.

## 6. Obsługa błędów, limity, sprzątanie

- **Rate limit:** każdy callback `trim_*` jest już liczony przez globalne
  `check_rate_limit` w `handle_callback`; zatwierdzenie zakresów tekstem liczy
  się jawnie jako jedno żądanie (5.1 krok 4). Limit 10/min jak wszędzie.
- **Jedno cięcie na czat naraz** (sprawdzane w `job_registry` po `kind == "trim"`).
- **/stop:** zabija bieżący ffmpeg albo upload MTProto; status
  „⏹ Przerwano po {n} z {m} fragmentów.”; źródło zostaje w magazynie.
- **Błąd ffmpeg:** log z stderr (ostatnie 500 znaków), użytkownik dostaje
  „Nie udało się wyciąć fragmentu {a}–{b}. Pozostałe fragmenty nie zostały
  wysłane.” Źródło zostaje.
- **Wysyłka:** `AudioDeliveryError` → komunikat z wyjątku, przerwanie pozostałych
  fragmentów.
- **Sprzątanie:** pliki fragmentów kasowane w `finally` po każdym fragmencie.
  Źródła: istniejące `cleanup_old_files` (24 h, przy < 5 GB wolnego — 6 h)
  plus `purge_expired_sources` usuwający katalogi `trim_*` z wygasłym
  `meta.json` lub bez pliku źródła. Katalog `trim_*` nie pasuje do prefiksów
  `pl_*`/`big_*`, więc `_purge_archive_workspaces` go nie dotyka.
- **Dysk:** przy wolnym < `TRIM_MIN_FREE_DISK_GB` nowe źródła nie są
  zatrzymywane (brak ✂️ pod audio, odmowa `trim_dl`).
- **Logowanie:** `INFO` przy zatrzymaniu źródła (token, rozmiar, chat),
  przy każdym cięciu (zakres, czas ffmpeg); `WARNING` przy odmowie magazynu.

## 7. Plan testów

### 7.1 Nowe pliki

- `tests/test_time_ranges.py` — tabela: wszystkie zapisy z 3.1, każdy błąd
  z 3.2, separatory `–`/`—`, `;`, nowe linie, spacje, `looks_like_time_ranges`,
  zgodność wsteczna `parse_time_range`.
- `tests/test_trim_store.py` — retain (move/link/fallback copy), load po
  „restarcie” (nowy odczyt `meta.json`), wygasanie, zły token (`../x`,
  za krótki), token z innego czatu, odmowa dla formatu i braku miejsca,
  `purge_expired_sources`.
- `tests/test_audio_trim_service.py` — prawdziwy ffmpeg na wygenerowanym
  10-sekundowym sinusie (MP3, M4A, FLAC); długość fragmentu `2-5` = 3 s ± 0,1;
  zakres otwarty; tag `title`; okładka zachowana w MP3; fallback bez okładki.
  `pytest.mark.skipif` gdy brak ffmpeg.
- `tests/test_trim_callbacks.py` — prompt, pending input, URL anuluje pending,
  błędny tekst zostawia pending, wygasły pending, wygasłe źródło, rate limit,
  równoległe zadanie, `trim_cancel`, `trim_upload` bez sesji, `/stop` w trakcie,
  callback `trim_src_*` po `/logout` (odmowa bez dostępu do pliku).
- `tests/test_audio_delivery.py` — wybór Bot API/MTProto, za duży plik,
  przycisk ✂️ przy retencji, `discard_source` po nieudanej wysyłce, wysyłka
  bez przycisku przy odmowie magazynu.

### 7.2 Modyfikacje istniejących

- Testy `download_file` i `download_spotify_resolved`: plik trafia do magazynu
  zamiast `os.remove`; `trim_after=True` nie wysyła pliku.
- Testy klawiatur (`common_ui`): `trim_dl` w gałęzi podcastowej, w odcinku
  Spotify tylko z `has_fallback_audio`, w utworze Spotify; brak dla wideo.
- `inbound_audio`: przycisk `trim_upload`.
- `inbound_media`: kolejność pending trim → zakres przed pobraniem; zakresy
  otwarte dla YouTube; komunikat dla wielu zakresów.
- `cleanup`: `purge_expired_sources` wywoływane w cyklu sprzątania.
- `mtproto`: `buttons` przekazywane do pyrogram.

### 7.3 Ręczna lista E2E (rpi5a)

1. YouTube → Audio (MP3) → ✂️ Przytnij → `0:10-0:20, 1:00-` → dwa pliki,
   długości się zgadzają.
2. Podcast Spotify (iTunes) → ✂️ Pobierz i przytnij → pełny plik **nie**
   przychodzi, prompt z długością → `-2:00` → jeden plik.
3. Castbox → ✂️ Pobierz i przytnij → `30:00-31:00`.
4. Wysłana notatka głosowa → ✂️ Przytnij → fragment w MP3.
5. Plik > 50 MB (MTProto) → przycisk ✂️ obecny pod audio.
6. Restart `ytdown.service` → ✂️ pod wcześniej wysłanym audio nadal działa.
7. YouTube przed pobraniem: ✂️ Zakres czasowy → `2:15-` → plik od 2:15.
8. `/stop` w trakcie cięcia wielu fragmentów.

## 8. Poza zakresem

- Przycinanie wideo.
- Ułamki sekund (`0:12.5`), zapisy słowne („koniec”).
- Łączenie fragmentów w jeden plik, fade in/out.
- ✂️ pod wysłanymi fragmentami (cięcie dalej idzie ze źródła przez `[✂️ Tnij dalej]`).
- ✂️ dla pozycji playlist i kolekcji Spotify (paczki 7z).
- Archiwa 7z dla fragmentów przekraczających limit wysyłki.
- Przetrwanie `pending_trim` przez restart (po restarcie wystarczy kliknąć ✂️ ponownie).
- Zakres przed pobraniem dla natywnego audio Spotify (`spv_audio_m4a`) — dostaje
  tylko ✂️ pod wysłanym plikiem.
