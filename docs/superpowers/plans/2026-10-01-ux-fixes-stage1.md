# Poprawki UX — etap 1 + współbieżność (S1) — plan wdrożenia

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Usunąć potwierdzone błędy interfejsu Telegrama, ujednolicić komunikaty i sprawić, że długie operacje nie blokują bota, a `/stop` i przycisk „⏹ Zatrzymaj” naprawdę je przerywają.

**Architecture:** Poprawki punktowe w handlerach (`bot/handlers/*`, `bot/telegram_callbacks.py`, `bot/telegram_commands.py`, `main.py`). Współbieżność: `CallbackQueryHandler(..., block=False)` i uploady `block=False`, ochrona „jedno ciężkie zadanie na czat” w routerze callbacków, blokujący ffmpeg w uploadach przeniesiony do wątku, analiza „Własnego polecenia” jako zadanie w tle (wzorzec z `bot/handlers/trim_callbacks.py`).

**Tech Stack:** Python 3.12/3.13, python-telegram-bot 22, pytest.

**Źródło wymagań:** audyt UX z 2026-10-01 (rozmowa) — problemy S1, S2, S5, S6 oraz błędy 1–7. Etapy 2 i 3 audytu są poza zakresem.

## Global Constraints

- Komunikaty dla użytkownika po polsku; kod, komentarze, nazwy i commity po angielsku. Bez `Co-Authored-By`/wzmianek o AI. Praca na `develop`, bez pusha.
- Testy: `.venv/bin/python -m pytest <ścieżki> -q -p no:cacheprovider`; pełny zestaw z `--maxfail=1000`. Jedyna akceptowalna porażka: `tests/test_poetry_config.py::TestProjectStructure::test_no_legacy_node_manifests_in_project_root` (lokalny `package-lock.json`).
- Nie zmieniaj zachowania, którego zadanie nie dotyczy. Istniejące testy mogą wymagać aktualizacji tylko tam, gdzie zadanie celowo zmienia tekst lub zachowanie — każdą taką zmianę opisz w raporcie.
- Wszystkie komunikaty z tekstem pochodzącym od użytkownika lub z sieci (tytuły) wysyłaj bez `parse_mode` albo escapuj (`escape_md` z `bot/handlers/common_ui.py`).
- „Zero-akcji” callbacki (nawigacja) nie mogą być blokowane ani przez limit, ani przez ochronę współbieżności.

---

### Task 1: Odporność — miniaturka, puste wiadomości, edytowane wiadomości, globalny error handler

**Files:** `bot/telegram_callbacks.py`, `bot/handlers/media_extras_callbacks.py`, `bot/handlers/common_ui.py`, `main.py`, testy w `tests/test_callback_common.py` (lub nowy `tests/test_robustness.py`).

Wymagania:
1. **Miniaturka.** Przycisk „Miniaturka” (`callback_data="thumbnail"`, `common_ui.py:81`) nie ma dziś handlera (zgubiony w `a6175d7`; poprzednia implementacja: `git show a6175d7^:bot/telegram_callbacks.py`, funkcja `_handle_thumbnail_download`). Dodaj routing `data == "thumbnail"` w `handle_callback` (po sprawdzeniu `current_url`, obok `time_range`) i funkcję `handle_thumbnail_download(update, context, url)` w `bot/handlers/media_extras_callbacks.py`:
   - **nie** edytuje menu formatów (menu ma zostać klikalne); wysyła nowe wiadomości,
   - metadane przez `get_video_info` w executorze, pobranie miniaturki istniejącym helperem z `bot/downloader_media.py`, wysyłka `context.bot.send_photo(chat_id, photo, caption=<tytuł>[:200])`, plik usuwany po wysyłce,
   - błąd: `context.bot.send_message(chat_id, "Nie udało się pobrać miniaturki tego materiału.")`.
   Test: każdy `callback_data` zwracany przez `build_main_keyboard(platform)` dla wszystkich platform jest obsłużony przez `handle_callback` (nie kończy się bez żadnej reakcji) — test parametryzowany po platformach, z podmienionymi handlerami docelowymi.
2. **`send_long_message`** (`common_ui.py:312`) nigdy nie wysyła części pustej ani złożonej z samych białych znaków. Test: tekst zaczynający się linią dłuższą niż limit nie generuje pustego wywołania `send_message`.
3. **Edytowane wiadomości i posty kanałów** są ignorowane: do wszystkich `MessageHandler` w `main.py` dodaj `filters.UpdateType.MESSAGE`, a `CommandHandler` rejestruj z `filters=filters.UpdateType.MESSAGE`. Test: `register_handlers` na atrapie aplikacji — każdy handler wiadomości odrzuca update z `edited_message`.
4. **Globalny error handler** w `main.py` (`application.add_error_handler(on_error)`): loguje `context.error` z tracebackiem; jeśli update ma `effective_chat` i błąd nie jest `telegram.error.NetworkError`, `RetryAfter` ani `Forbidden`, wysyła: „⚠️ Nie udało się obsłużyć tej akcji z powodu błędu po stronie bota. Spróbuj ponownie — jeśli to się powtórzy, wyślij link jeszcze raz.” Wysyłka w `try/except` (nie może rzucić). Testy: błąd ogólny → wiadomość; `TimedOut` → brak wiadomości; brak chatu → brak wiadomości.

Commit: `Restore thumbnail button and harden message handling`

### Task 2: Teksty komend, Markdown, limit i powiadomienia admina

**Files:** `bot/handlers/command_access.py`, `bot/security_throttling.py`, `bot/handlers/inbound_media.py`, `bot/handlers/inbound_audio.py`, `bot/handlers/inbound_video.py`, `bot/handlers/trim_callbacks.py`, `bot/handlers/transcript_prompt_handlers.py`, `bot/handlers/download_callbacks.py`, `bot/services/archive_service.py` + testy.

1. **`/history`:** tytuły przez `escape_md`; pogrubienia `**x**` → `*x*` (legacy Markdown). Test: tytuł `a_b*c [x]` daje tekst z escapowanymi znakami.
2. **`/status`:** `**x**` → `*x*`.
3. **Limit:** jeden tekst w `bot/security_throttling.py`: `RATE_LIMIT_MESSAGE = f"Przekroczono limit żądań ({RATE_LIMIT_REQUESTS} na {RATE_LIMIT_WINDOW} s). Poczekaj chwilę i spróbuj ponownie."`; użyj go we wszystkich miejscach z „requestów” (inbound_media ×3, inbound_audio, inbound_video, trim_callbacks `RATE_LIMIT_TEXT`, transcript_prompt_handlers). Słowo „requestów” nie może zostać w `bot/`. (Callbacki — Task 4.)
4. **Powiadomienia admina** (`command_access.py`, nieudany PIN i blokada) po polsku: nagłówki „⚠️ Nieudana próba podania PIN-u” i „🚫 Zablokowano dostęp”, etykiety pól: „ID użytkownika”, „Nazwa użytkownika”, „Imię i nazwisko”, „Język”, „Próba”, „Czas”. Zachowaj istniejące dane i warunki wysyłki.
5. **„Sesja wygasła.” bez następnego kroku** (`download_callbacks.py` przy callbackach `arc_*`, `archive_service.py`): zamień na „Paczki wygasły (przechowuję je 60 min) albo bot był restartowany. Pobierz plik ponownie.” Pozostałe warianty, które już mówią co zrobić, zostaw.

Zaktualizuj testy oczekujące starych tekstów. Commit: `Fix command Markdown and unify rate-limit and admin messages`

### Task 3: `/help`, menu komend i uprawnienia admina

**Files:** `bot/handlers/command_access.py`, `bot/telegram_commands.py` (jeśli wrappery), `main.py`, `README.md` (tabela komend), testy.

1. **Menu komend** (`set_bot_commands` w `main.py`):
   - zakres domyślny (`BotCommandScopeDefault`): `start` „Rozpocznij korzystanie z bota”, `help` „Pomoc i instrukcje”, `stop` „Zatrzymaj trwające operacje”, `history` „Historia pobrań”, `logout` „Wyloguj się”;
   - gdy `ADMIN_CHAT_ID` jest ustawione i poprawne: dodatkowo `BotCommandScopeChat(admin)` z listą domyślną + `status` „Miejsce na dysku”, `cleanup` „Usuń pliki starsze niż 24 h”, `users` „Lista autoryzowanych użytkowników”, `spotify_login` „Połącz konto Spotify”, `spotify_logout` „Odłącz konto Spotify”;
   - gdy `ADMIN_CHAT_ID` nie jest ustawione (wtedy każdy jest adminem — `_is_admin`): zakres domyślny dostaje pełną listę.
   Test na atrapie `bot.set_my_commands` — oba warianty.
2. **`/status` i `/cleanup` tylko dla admina** (jak `/users`, przez `_is_admin`); dla innych: „Ta komenda jest dostępna tylko dla administratora bota.”
3. **`/help`** — nowy tekst wysyłany **bez** `parse_mode`. Sekcje (treść możesz dopracować, zachowując fakty):
   ```
   Jak korzystać z bota

   📥 Pobieranie
   Wyślij link (YouTube, Vimeo, TikTok, Instagram, LinkedIn, X, Spotify, Castbox) i wybierz format. Pliki większe niż limit Telegrama przyjdą w częściach 7z.

   ✂️ Przycinanie audio
   • Pod każdym wysłanym plikiem audio jest przycisk „✂️ Przytnij” (działa 24 h).
   • Podcasty i Spotify: „✂️ Pobierz i przytnij”.
   • Własny plik: wyślij MP3 lub wiadomość głosową i wybierz „✂️ Przytnij”.
   • Zapis: 1:30-4:45 · 2:15- (do końca) · -5:00 (od początku) · kilka zakresów po przecinku.

   📝 Transkrypcja i podsumowanie
   Pod linkiem wybierz „Transkrypcja audio” lub „Transkrypcja + Podsumowanie” albo wyślij plik audio/wideo.

   🎵 Spotify
   Odcinki, utwory, albumy i playlisty — przy albumach zaznacz utwory i pobierz je pojedynczo albo w paczkach 7z.

   Komendy
   /stop — zatrzymaj trwające pobieranie lub przetwarzanie
   /history — historia pobrań
   /logout — wyloguj się
   ```
   Sekcja „Komendy administratora” (`/status`, `/cleanup`, `/users`, `/spotify_login`, `/spotify_logout` z krótkimi opisami) dołączana tylko, gdy `_is_admin(user_id)`. Usuń poradę o `cookies.txt` z `/help` (to informacja dla operatora). Limity rozmiaru uploadu weź z kodu (`inbound_audio.py`: 20 MB, z MTProto 200 MB) jeśli je podajesz.
4. **README:** zaktualizuj tabelę „Komendy bota Telegram” (dodaj `/stop`, oznacz komendy admina).

Commit: `Rewrite /help and scope bot commands for admins`

### Task 4: Limit żądań dla przycisków

**Files:** `bot/telegram_callbacks.py` (+ ewentualnie `bot/handlers/callback_parsing.py`), testy.

- W `handle_callback` limit (`check_rate_limit`) liczą **tylko** callbacki uruchamiające pracę: prefiksy `dl_`, `spv_`, `trim_dl`, `thumbnail`, `transcribe` (obejmuje `transcribe_summary`), `summary_option_`, `audio_transcribe` (obejmuje wariant z podsumowaniem), `audio_summary_option_`, `sub_lang_`, `sub_auto_`, `sub_src_ai`, `sub_sum_`, `pl_dl_`, `pl_zip_dl_`, `spc_dl_`, `arc_split_`, `arc_resend_`, `arc_pack_partial_` oraz wybór wielkości paczki Spotify (`spc_pack_<mp3|m4a>_<50|100|all>`). Wszystkie inne (nawigacja: `spc_t_`, `spc_p_`, `spc_all`, `spc_clear`, `spc_pack_back`, `spc_pack_mp3`/`spc_pack_m4a`, `pl_more`, `pl_cancel`, `back`, `time_range*`, `formats`, `stop_*`, `trim_*` poza `trim_dl`, `tr_prompt_*`, `arc_cancel_`, `arc_purge*`, `noop` itd.) są zwolnione. Zdefiniuj to jako funkcję `is_work_callback(data) -> bool` z testem tablicowym.
- Przy przekroczeniu limitu: `await query.answer(RATE_LIMIT_TOAST, show_alert=True)` z tekstem „Za dużo żądań — poczekaj chwilę i spróbuj ponownie.” i **bez** edycji wiadomości (menu zostaje). W pozostałych przypadkach `query.answer()` jak dziś (dokładnie jedno `answer` na callback).
- Testy: 11 kliknięć nawigacji z rzędu nie trafia w limit; kliknięcie pracy po wyczerpaniu limitu wywołuje toast i nie edytuje wiadomości.

Commit: `Rate-limit only work-starting buttons and keep menus on limit`

### Task 5: Współbieżność — długie zadania nie blokują bota

**Files:** `main.py`, `bot/telegram_callbacks.py`, `bot/handlers/inbound_audio.py`, `bot/handlers/inbound_video.py`, `bot/handlers/transcript_prompt_handlers.py`, testy.

1. `main.py`: `CallbackQueryHandler(handle_callback, block=False)`; handlery uploadów (głosówki, audio, dokumenty audio, wideo) z `block=False`. Handler tekstu i komendy zostają blokujące.
2. **Jedno ciężkie zadanie na czat:** w `handle_callback`, dla callbacków z `is_work_callback(data)` (Task 4), jeśli w tym czacie trwa już takie zadanie uruchomione przez callback → `query.answer("Trwa już inna operacja w tym czacie. Poczekaj albo przerwij ją komendą /stop.", show_alert=True)` i koniec. Implementacja: zbiór czatów na poziomie modułu, dodanie przed `await` routingu i usunięcie w `finally` — sprawdzenie i dodanie bez `await` pomiędzy. Nawigacja i `stop_*` nigdy nie są blokowane. Testy: dwa równoległe callbacki pracy w jednym czacie (asyncio, pierwszy wisi na `Event`) — drugi dostaje toast; w innym czacie przechodzi; po zakończeniu pierwszego kolejny przechodzi.
3. **Uploady:** konwersja ffmpeg (`subprocess.run` w `inbound_audio.py` i `inbound_video.py`) uruchamiana przez `await asyncio.to_thread(...)`, z zachowaniem dotychczasowych argumentów, timeoutu i punktów, które testy podmieniają (`subprocess` w module).
4. **„Własne polecenie”** (`handle_pending_transcript_prompt`): po walidacjach i odpowiedzi statusowej generowanie analizy uruchom przez `context.application.create_task(..., update=update)` (jak `trim_callbacks.handle_pending_trim_input`); błąd planowania → komunikat „Nie udało się uruchomić analizy. Spróbuj ponownie.” Testy jak w `tests/test_trim_callbacks.py` (pętla zdarzeń, brak ostrzeżeń „never awaited”).
5. Komentarz w `main.py` wyjaśniający, dlaczego callbacki są nieblokujące (z odwołaniem do `bot/handlers/trim_callbacks.py`).

Commit: `Run long Telegram jobs without blocking other updates`

### Task 6: Przycisk „⏹ Zatrzymaj” i zatrzymanie zamiast błędu

**Files:** `bot/handlers/common_ui.py`, `bot/handlers/download_callbacks.py`, `bot/handlers/playlist_callbacks.py`, `bot/services/archive_service.py`, `bot/handlers/spotify_collection_callbacks.py`, `bot/services/spotify_archive_service.py`, `bot/telegram_commands.py`, testy.

1. Helper `stop_button_markup(job_id) -> InlineKeyboardMarkup` w `common_ui.py`: jeden przycisk „⏹ Zatrzymaj”, `callback_data=f"stop_{job_id}"`.
2. Komunikaty postępu edytowane w trakcie zadania mają ten przycisk: `download_file` (pobieranie, w tym ścieżka transkrypcji z linku), pobieranie playlisty (wysyłka pojedyncza i 7z), kolekcje Spotify (pojedynczo i 7z), pakowanie/wysyłka 7z pojedynczego pliku — wszędzie tam, gdzie przepływ ma `JobCancellation` i jedną wiadomość statusu edytowaną w miejscu. Komunikaty końcowe (sukces, błąd, zatrzymanie) **bez** tego przycisku.
3. `download_file`: gdy wyjątek nastąpił po ustawieniu `cancellation.event`, status końcowy „⏹ Zatrzymano pobieranie.” i **bez** zapisu porażki w historii.
4. `handle_stop_callback` dla `stop_<id>`: zatrzymuje tylko zadanie należące do tego czatu (`job_registry.list_for_chat(chat_id)`); dla cudzego/nieznanego id: „Operacja już zakończona.” Kliknięcie na komunikacie postępu edytuje go na „Wysłano sygnał zatrzymania…”.
5. Testy: markup na komunikatach w trakcie `download_file`, brak go na końcowym; zatrzymanie w trakcie → „⏹ Zatrzymano pobieranie.” i brak `record_download_for` ze statusem porażki; `stop_<id>` z innego czatu nie zatrzymuje.

Commit: `Add a stop button to progress messages and report stops as stops`

### Task 7: Transkrypcja nie ginie

**Files:** `bot/handlers/download_callbacks.py` (gałąź transkrypcji), `bot/handlers/transcription_callbacks.py`, `bot/handlers/spotify_callbacks.py`, `bot/transcription_pipeline.py` lub odpowiednik (placeholdery), testy.

1. **Klucze przed pobraniem:** dla transkrypcji brak `GROQ_API_KEY` → komunikat „Funkcja niedostępna — brak klucza API do transkrypcji. Skontaktuj się z administratorem.” **przed** pobieraniem; dla „+ Podsumowanie” brak `CLAUDE_API_KEY` → przed pobraniem: „Podsumowanie jest niedostępne — brak klucza API Claude. Wybierz samą transkrypcję albo skontaktuj się z administratorem.” Dotyczy linku, uploadu i Spotify.
2. **Porażka podsumowania nie kasuje transkrypcji:** gdy generowanie podsumowania zwróci błąd/`None`, bot wysyła plik transkrypcji (jak w gałęzi „tekst zbyt długi”), status: „Transkrypcja gotowa, ale nie udało się wygenerować podsumowania. Wysyłam samą transkrypcję.”, rejestruje kontekst „✍️ Własne polecenie” jak dziś.
3. **Spotify:** `_handle_transcription` zwraca `bool`; `download_spotify_resolved` nie nadpisuje komunikatu błędu tekstem „Gotowe: {title}” i zwraca `False` przy porażce.
4. **Placeholdery fragmentów:** angielskie „[No transcription for this part]” i „Error during transcription” zastąp „[brak transkrypcji tego fragmentu]” i „Błąd podczas transkrypcji”.
5. Testy dla każdego punktu (link, upload, Spotify).

Commit: `Keep transcripts when summaries fail and check API keys first`

### Task 8: Uczciwe playlisty i instrukcja 7z

**Files:** `bot/services/playlist_service.py`, `bot/services/archive_service.py`, `bot/services/spotify_archive_service.py`, `bot/handlers/download_callbacks.py`, testy.

1. **Playlista YouTube:** gdy wyświetlonych pozycji jest mniej niż w całej playliście, przyciski mówią „Pobierz {n} — Audio MP3” (itd.) zamiast „Pobierz wszystkie — …”, nad listą formatów jest wiersz „Pokaż więcej (do 50)”, a treść zawiera: „Pobiorę pozycje widoczne na liście ({n} z {total}). „Pokaż więcej” rozszerza listę do 50.” Gdy lista jest pełna — bez zmian.
2. **Instrukcja 7z:** stała `ARCHIVE_UNPACK_HINT` w `archive_service.py`: „📦 Jak otworzyć: zapisz wszystkie części (.7z.001, .7z.002…) w jednym folderze, nie zmieniaj nazw i otwórz plik .7z.001 w 7-Zip (Windows), Keka (macOS) lub ZArchiver (Android).” Dołączana do komunikatu końcowego po wysłaniu paczek: pojedynczy plik, playlista, „Spakuj co mam”, archiwa Spotify.
3. **Żargon:** „Pakowanie do 7z (vol_size={n} MB)...” → „Pakuję do 7z w częściach po {n} MB…”; „Pakowanie OK: {V} paczek. Wysyłanie...” → „Spakowano: {V} części. Wysyłam…”; „Mogę spakować go w wolumeny 7z (po {n} MB)” → „Mogę spakować go w części 7z (po {n} MB)”.
4. Testy: etykiety przy liście obciętej i pełnej; obecność `ARCHIVE_UNPACK_HINT` w komunikatach końcowych; brak „vol_size” w tekstach dla użytkownika.

Commit: `Make playlist buttons honest and explain 7z archives`

## Weryfikacja końcowa

- Pełny zestaw testów lokalnie (jedyna porażka: `test_no_legacy_node_manifests_in_project_root`).
- Recenzja całej gałęzi.
- Ręcznie na rpi5a (po wdrożeniu, za zgodą użytkownika): `/help` (zwykły użytkownik i admin), menu komend, „Miniaturka”, `/stop` i „⏹ Zatrzymaj” w trakcie pobierania długiego filmu, drugi link w trakcie pobierania, upload głosówki w trakcie pobierania.
