# Pobieranie audycji TOK FM — plan wdrożenia

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Link do odcinka `audycje.tokfm.pl/podcast/<id>` działa w bocie tak jak odcinek Castbox (tytuł, czas trwania, MP3/M4A, transkrypcja, streszczenie), a płatne odcinki są pobierane wyłącznie w ważnej sesji premium właściciela.

**Architecture:** Wtyczka yt-dlp w katalogu głównym repo (`yt_dlp_plugins/extractor/tokfm.py`, pakiety namespace bez `__init__.py`) zawiera dwa ekstraktory:
- `TokFMIE` czyta stronę odcinka. Gdy odcinek nie jest jawnie darmowy, najpierw sprawdza dostęp przez `/premium/blokada`. Potem pobiera podpisany adres MP3 z `/gets`, a rozmiar pliku bierze z nagłówka HEAD.
- `TokFMNonEpisodeIE` odrzuca każdy inny adres na `audycje.tokfm.pl` czytelnym błędem.

Bot dostaje wpis platformy `tokfm` i dwa wzorce w `bot/download_errors.py`. Komunikat o linku do serii powstaje w samej wtyczce i w `download_errors.py`, a nie w `bot/handlers/inbound_media.py`, jak sugerował projekt. Powody: w tym pliku są niezacommitowane zmiany właściciela, a takie rozwiązanie działa w każdym miejscu, które tworzy `YoutubeDL`.

**Tech Stack:** Python 3.12 (lokalnie `.venv`) i 3.13 (rpi5a), yt-dlp 2026.07.04, pytest.

**Spec:** `docs/superpowers/specs/2026-11-18-tokfm-podcast-design.md`

## Global Constraints

- Komunikaty dla użytkownika po polsku; kod, komentarze, nazwy i commity po angielsku. Bez `Co-Authored-By` i bez wzmianek o AI w commitach.
- Praca na gałęzi `develop`. Push i wdrożenie wyłącznie w Task 5, po wyraźnej zgodzie właściciela.
- W drzewie roboczym są niezacommitowane zmiany właściciela (m.in. `README.md`, `bot/handlers/inbound_media.py`, `bot/config.py`). Nie dotykaj ich. Dodawaj do indeksu wyłącznie pliki wymienione w danym zadaniu (`git add <ścieżka>`), nigdy `git add -A`, `.` ani `-u`. Bez `git stash`. README stage'uj tylko techniką z Task 3.
- Hasło i cookies nie trafiają do repo, logów, komunikatów bota, wyników poleceń ani do zewnętrznych narzędzi (także jev). Cookies właściciela do testów na żywo: `/tmp/claude-1000/-mnt-c-code-ytdown/21f66977-7ac3-4a2f-810b-870db8592331/scratchpad/tokfm_probe/cookies.txt` (0600). Nigdy nie wyświetlaj jego zawartości.
- Anty-cel ze specyfikacji: płatnego odcinka nie wolno pobrać bez ważnej sesji, mimo że `/gets` dziś oddaje plik anonimowo. Bez sesji ekstraktor kończy się błędem **przed** zapytaniem `/gets`. Nie sprawdzaj luki na innych odcinkach.
- W poleceniach `$S` to katalog scratchpadu: `S=/tmp/claude-1000/-mnt-c-code-ytdown/21f66977-7ac3-4a2f-810b-870db8592331/scratchpad`. Pliki tymczasowe tylko tam.
- Testy lokalnie: `.venv/bin/python -m pytest <ścieżki> -q -p no:cacheprovider`. Przed pełnym przebiegiem: `rm -rf /tmp/pytest-of-pi && df -h /tmp` (mały tmpfs), pełny przebieg z `--maxfail=1000`. Import na `/mnt/c` jest wolny (9p); długi czas testów to nie błąd kodu.
- Nie zmieniaj istniejących testów poza miejscami wskazanymi w zadaniach.

## Review Focus

1. **Sesja wygasa między menu a kliknięciem przycisku.** Pobieranie uruchamia ekstraktor jeszcze raz, a błąd idzie przez `download_callbacks.py`, który pokazuje `cookies_hint` platformy tylko wtedy, gdy tekst błędu zawiera „cookie”. Oczekiwane: podpowiedź TOK FM zamiast „Wystąpił błąd podczas pobierania”. Testy: Task 1 (`SESSION_ERROR` zawiera „cookie”), Task 2 (`cookies_hint` TOK FM ustawiony).
2. **Na stronie stoją przyciski innych odcinków** (polecane) przed przyciskiem tego odcinka. Oczekiwane: tytuł i flagi premium pochodzą z elementu, którego `data-id` równa się id z adresu. Test: Task 1.
3. **HEAD na podpisany adres MP3 się nie udaje** albo nie zwraca `Content-Length`. Oczekiwane: menu nadal się pokazuje, czas trwania „?”, bez fałszywego ostrzeżenia o rozmiarze (bez `filesize` i `duration` bot szacuje 5 Mb/s, czyli dla 42 min ok. 1,5 GB). Test: Task 1 (`estimate_file_size(info) is None`).
4. **Zmiana znaczników strony usuwa flagi premium.** Oczekiwane: odcinek jest traktowany jako płatny i sprawdzana jest sesja. Odcinek uznany za darmowy pomija sprawdzenie tylko przy `data-podcast_free="true"` i `data-only_for_premium` innym niż `"true"`. Test: Task 1.
5. **Warianty linku:** bez sluga, z query (`?utm_source=…`), `http://`. Oczekiwane: `TokFMIE` pasuje i id to `200878`. `/player/v2/<id>`, `/audycja/...` i strona główna dostają komunikat o linku do odcinka. Test: Task 1.

---

### Task 1: Wtyczka yt-dlp `tokfm`

**Files:**
- Create: `yt_dlp_plugins/extractor/tokfm.py` (katalogi `yt_dlp_plugins/` i `yt_dlp_plugins/extractor/` **bez** `__init__.py`)
- Test: `tests/test_tokfm_plugin.py`

**Interfaces:**
- Produces (moduł `yt_dlp_plugins.extractor.tokfm`):
  - `SESSION_ERROR: str = "TOK FM premium session missing or expired; refresh TOK FM cookies"`
  - `NON_EPISODE_ERROR: str = "TOK FM: only episode links are supported (audycje.tokfm.pl/podcast/...)"`
  - `class TokFMIE(InfoExtractor)`: `IE_NAME = "tokfm"`, `_VALID_URL = r"https?://audycje\.tokfm\.pl/podcast/(?P<id>\d+)"`
  - `class TokFMNonEpisodeIE(InfoExtractor)`: `IE_NAME = "tokfm:non-episode"`, `_VALID_URL = r"https?://audycje\.tokfm\.pl(?!/podcast/\d)"`
  - `__all__ = ["TokFMIE", "TokFMNonEpisodeIE"]` (yt-dlp rejestruje tylko klasy z `__all__`, których nazwy kończą się na `IE`)

- [ ] **Step 1: Zapisz bazowy wynik pełnego zestawu testów**

Run: `rm -rf /tmp/pytest-of-pi && df -h /tmp && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider --maxfail=1000 2>&1 | grep -E "^(FAILED|ERROR) | passed| failed" > "$S/baseline-tests.txt"; cat "$S/baseline-tests.txt"`
Expected: plik z listą `FAILED ...`/`ERROR ...` i linią podsumowania sprzed zmian (porażki środowiskowe WSL, np. brak `7z`, `package-lock.json`, oraz ewentualne z niezacommitowanej pracy właściciela). To punkt odniesienia dla Task 5.

- [ ] **Step 2: Napisz testy, które nie przejdą**

Pomocnicze elementy w `tests/test_tokfm_plugin.py`:
- `_player_tag(episode_id, title, *, free=None, premium=None) -> str` zwraca `<button class="tok-js__play" data-id=… data-podcast-title=… data-leader="'Jakub Janiszewski'" data-guests="'Gość Pierwszy', 'Gość Drugi'" [data-podcast_free=…] [data-only_for_premium=…]>`. Atrybut pomijany, gdy argument to `None`.
- `_page(*tags) -> str` daje HTML z `<meta property="og:image" content="https://static.radioagora.pl/p.jpg">` i podanymi tagami.
- `FakeTokFM(page, *, access_status=204, gets=None, content_length="20287696")` przez `monkeypatch.setattr` na instancji ekstraktora podmienia `_download_webpage`, `_request_webpage` i `_download_json`:
  - zapisuje w `self.calls` krotki `("page", url)`, `("access", url)`, `("gets", url, data, headers)` i `("head", url)`;
  - dla `.../premium/blokada?...` zwraca `SimpleNamespace(status=access_status, headers={})`;
  - dla `HEADRequest` (rozpoznaj po `getattr(req, "method", None) == "HEAD"`) zwraca `SimpleNamespace(status=200, headers={"Content-Length": content_length})` albo `False`, gdy `content_length is None`;
  - `gets` domyślnie `{"url": AUDIO_URL, "tok": "tokfm", "start_time": 0}`.
- Ekstraktor tworzony przez `TokFMIE(yt_dlp.YoutubeDL({"quiet": True}))`.

```python
EPISODE_URL = "https://audycje.tokfm.pl/podcast/200878,Antrophic-Nvidia-i-Microsoft"
AUDIO_URL = "https://storage.tuba.fm/podcast/tok/abc/00/00/20/08/78.mp3"

@pytest.mark.parametrize("url", [
    EPISODE_URL,
    "https://audycje.tokfm.pl/podcast/200878",
    "http://audycje.tokfm.pl/podcast/200878,Slug?utm_source=x",
])
def test_episode_links_match_tokfm_ie(url):
    assert TokFMIE.suitable(url)
    assert not TokFMNonEpisodeIE.suitable(url)
    assert TokFMIE.get_temp_id(url) == "200878"

@pytest.mark.parametrize("url", [
    "https://audycje.tokfm.pl/audycja/687,Dluzsza-rozmowa",
    "https://audycje.tokfm.pl/player/v2/200878",
    "https://audycje.tokfm.pl/",
    "https://audycje.tokfm.pl",
])
def test_non_episode_links_match_only_non_episode_ie(url):
    assert TokFMNonEpisodeIE.suitable(url)
    assert not TokFMIE.suitable(url)

def test_premium_episode_with_session_returns_single_mp3_format(monkeypatch):
    # premium="true", free="false", access 204
    # assert info["id"] == "200878"; info["title"] == <tytuł z data-podcast-title>
    # assert info["creators"] == ["Jakub Janiszewski"]; info["cast"] == ["Gość Pierwszy", "Gość Drugi"]
    # assert info["thumbnail"] == "https://static.radioagora.pl/p.jpg"
    # assert info["formats"] == [{"format_id": "mp3", "url": AUDIO_URL, "ext": "mp3",
    #     "acodec": "mp3", "vcodec": "none", "abr": 64, "filesize": 20287696}]
    # assert info["duration"] == 2536          # round(20287696 / 8000)
    # gets call: url "https://audycje.tokfm.pl/gets", json.loads(data) == {"pid": 200878, "st": "tokfm"},
    #     headers["X-Tuba"] == "tokfm"
    # access call url == "https://audycje.tokfm.pl/premium/blokada?podcast=200878"

def test_premium_episode_without_session_fails_before_gets(monkeypatch):
    # access_status=200 -> pytest.raises(ExtractorError) z SESSION_ERROR w str(exc)
    # assert not any(c[0] == "gets" for c in fake.calls)

def test_session_error_mentions_cookies_for_download_hint():
    assert "cookie" in SESSION_ERROR.lower()

def test_free_episode_skips_access_check(monkeypatch):
    # free="true", premium="false" -> brak wywołania "access"; jest "gets"; formats[0]["url"] == AUDIO_URL

def test_episode_without_premium_flags_is_treated_as_premium(monkeypatch):
    # free=None, premium=None, access_status=200 -> ExtractorError z SESSION_ERROR; brak "gets"

def test_player_tag_is_matched_by_episode_id(monkeypatch):
    # _page(_player_tag("199999", "Inny odcinek", free="true", premium="false"),
    #       _player_tag("200878", "Rozmowa &quot;o IT&quot; &amp; chmura", free="false", premium="true"))
    # access_status=204 -> info["title"] == 'Rozmowa "o IT" & chmura'; jest wywołanie "access"

def test_missing_content_length_leaves_size_and_duration_unknown(monkeypatch):
    # content_length=None -> info["duration"] is None; "filesize" not in formats[0] albo None
    # from bot.security_policy import estimate_file_size; assert estimate_file_size(info) is None

def test_gets_response_without_url_raises(monkeypatch):
    # gets={"tok": "tokfm"} -> pytest.raises(ExtractorError)

def test_page_without_player_tag_raises(monkeypatch):
    # _page() bez tagów -> pytest.raises(ExtractorError); brak "access" i "gets"

def test_non_episode_link_raises_expected_error():
    # TokFMNonEpisodeIE(ydl).extract("https://audycje.tokfm.pl/audycja/687,X")
    # -> ExtractorError, NON_EPISODE_ERROR in str(exc), exc.expected is True

def test_yt_dlp_discovers_plugin_from_repo_root():
    code = ("import yt_dlp; yt_dlp.YoutubeDL({'quiet': True}); "
            "from yt_dlp.globals import plugin_ies; print(sorted(plugin_ies.value))")
    env = {k: v for k, v in os.environ.items() if k != "YTDLP_NO_PLUGINS"}
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, env=env,
                         capture_output=True, text=True, check=True, timeout=300)
    assert "TokFMIE" in out.stdout and "TokFMNonEpisodeIE" in out.stdout
    assert "Error while importing module" not in out.stderr
```

- [ ] **Step 3: Uruchom testy i sprawdź, że nie przechodzą**

Run: `.venv/bin/python -m pytest tests/test_tokfm_plugin.py -q -p no:cacheprovider`
Expected: błąd zbierania testów, `ModuleNotFoundError: No module named 'yt_dlp_plugins.extractor.tokfm'`.

- [ ] **Step 4: Zaimplementuj `yt_dlp_plugins/extractor/tokfm.py`**

`TokFMIE._real_extract(self, url) -> dict`, kolejno:
1. `webpage = self._download_webpage(url, video_id)`.
2. Tag odtwarzacza: pierwszy `re.finditer(r'<[a-zA-Z][^>]*\bdata-id="%s"[^>]*>' % video_id, webpage)`, którego `yt_dlp.utils.extract_attributes(...)` zawiera `data-podcast-title`. Gdy go brak, `ExtractorError("TOK FM episode page has no player data", video_id=video_id)`.
3. `is_free = attrs.get("data-podcast_free") == "true" and attrs.get("data-only_for_premium") != "true"`. Gdy `not is_free`: `self._request_webpage(f"https://audycje.tokfm.pl/premium/blokada?podcast={video_id}", video_id, note="Checking TOK FM premium access", expected_status=lambda _: True)`. Jeśli `.status != 204`, rzuć `ExtractorError(f"{SESSION_ERROR} (access check returned HTTP {status})", expected=True, video_id=video_id)`. Komentarz w kodzie musi wyjaśniać, dlaczego sprawdzamy przed `/gets`: anty-cel ze specyfikacji, `/gets` dziś nie egzekwuje płatności.
4. `self._download_json("https://audycje.tokfm.pl/gets", video_id, note="Fetching TOK FM audio URL", data=json.dumps({"pid": int(video_id), "st": "tokfm"}).encode(), headers={"X-Tuba": "tokfm", "Content-Type": "text/plain;charset=UTF-8"})`. Brak niepustego `url` w odpowiedzi oznacza `ExtractorError("TOK FM did not return an audio URL", video_id=video_id)`.
5. `self._request_webpage(HEADRequest(audio_url), video_id, note="Checking TOK FM audio size", fatal=False)` (`from yt_dlp.networking import HEADRequest`). `filesize = int_or_none(urlh.headers.get("Content-Length"))` przy udanym zapytaniu, w przeciwnym razie `None`. `duration = round(filesize / 8000)` (MP3 CBR 64 kb/s) albo `None`.
6. Zwróć `id`, `title` (z `data-podcast-title`), `creators` / `cast` (`re.findall(r"'([^']+)'", ...)` z `data-leader` / `data-guests`, `None`, gdy lista pusta), `thumbnail` (`self._og_search_thumbnail(webpage, default=None)`), `duration` oraz `formats` w kształcie z testu. Klucz `filesize` tylko wtedy, gdy znany.

`TokFMNonEpisodeIE._real_extract` rzuca `ExtractorError(NON_EPISODE_ERROR, expected=True)`.

- [ ] **Step 5: Uruchom testy i sprawdź, że przechodzą**

Run: `.venv/bin/python -m pytest tests/test_tokfm_plugin.py -q -p no:cacheprovider`
Expected: wszystkie PASS.

- [ ] **Step 6: Commit**

```bash
git add yt_dlp_plugins/extractor/tokfm.py tests/test_tokfm_plugin.py
git commit -m "Add TOK FM yt-dlp extractor plugin with premium session check"
```

---

### Task 2: Platforma `tokfm` w bocie i komunikaty błędów

**Files:**
- Create: `bot/platforms/tokfm.py`
- Modify: `bot/platforms/__init__.py` (import `tokfm`, `tokfm.CONFIG` na końcu `PLATFORMS`)
- Modify: `bot/download_errors.py` (dwa wpisy na **początku** `_ERROR_PATTERNS`)
- Test: `tests/test_platforms.py`, `tests/test_download_errors.py`

**Interfaces:**
- Consumes: `SESSION_ERROR`, `NON_EPISODE_ERROR` z Task 1. Importują je tylko testy; kod bota nie importuje wtyczki.
- Produces: `bot.platforms.get_platform("tokfm")`; `classify_download_error` rozpoznaje oba błędy TOK FM.

- [ ] **Step 1: Napisz testy, które nie przejdą**

W `tests/test_platforms.py`:
- `test_platforms_registry_is_non_empty_tuple`: `== 8` zmień na `== 9`.
- Do parametryzacji `test_detect_by_domain_matches_known_hosts` dodaj `("audycje.tokfm.pl", "tokfm")`.
- `test_cookies_hint_set_for_platforms_requiring_cookies_except_podcasts` zastąp dwoma testami. Stara reguła zakazywała podpowiedzi wszystkim podcastom, a TOK FM to podcast wymagający cookies.

```python
def test_cookies_hint_set_for_every_platform_requiring_cookies():
    for p in platforms_pkg.PLATFORMS:
        if p.requires_cookies:
            assert p.cookies_hint, f"Platform {p.name!r} marked requires_cookies but has no hint"

def test_podcast_platforms_without_cookies_have_no_hint():
    for p in platforms_pkg.PLATFORMS:
        if p.is_podcast and not p.requires_cookies:
            assert p.cookies_hint is None

def test_tokfm_is_a_cookie_podcast_platform():
    from bot.security_policy import detect_platform, get_media_label, validate_url
    from bot.handlers.common_ui import build_main_keyboard
    url = "https://audycje.tokfm.pl/podcast/200878,Slug"
    assert validate_url(url) and detect_platform(url) == "tokfm"
    assert get_media_label("tokfm") == "odcinku"
    config = platforms_pkg.get_platform("tokfm")
    assert (config.display_name, config.is_podcast, config.requires_cookies) == ("TOK FM", True, True)
    callbacks = [b.callback_data for row in build_main_keyboard("tokfm") for b in row]
    assert "dl_audio_mp3" in callbacks and "transcribe" in callbacks
    assert "dl_audio_flac" not in callbacks and "time_range" not in callbacks
```

W `tests/test_download_errors.py`:

```python
TOKFM_SESSION = ("ERROR: [tokfm] 200878: TOK FM premium session missing or expired; "
                 "refresh TOK FM cookies (access check returned HTTP 200)")
TOKFM_NON_EPISODE = ("ERROR: [tokfm:non-episode] TOK FM: only episode links are supported "
                     "(audycje.tokfm.pl/podcast/...)")

def test_classifies_tokfm_expired_session():
    reason = classify_download_error(TOKFM_SESSION)
    assert reason is not None and "TOK FM" in reason and "cookies" in reason

def test_classifies_tokfm_non_episode_link():
    reason = classify_download_error(TOKFM_NON_EPISODE)
    assert reason is not None and "/podcast/" in reason

def test_tokfm_messages_match_plugin_phrases():
    from yt_dlp_plugins.extractor.tokfm import NON_EPISODE_ERROR, SESSION_ERROR
    assert classify_download_error(SESSION_ERROR) == classify_download_error(TOKFM_SESSION)
    assert classify_download_error(NON_EPISODE_ERROR) == classify_download_error(TOKFM_NON_EPISODE)

def test_tokfm_media_error_message_uses_episode_label():
    assert build_media_error_message("odcinku", TOKFM_SESSION).startswith(
        "Nie udało się pobrać informacji o odcinku.")
```

- [ ] **Step 2: Uruchom testy i sprawdź, że nie przechodzą**

Run: `.venv/bin/python -m pytest tests/test_platforms.py tests/test_download_errors.py -q -p no:cacheprovider`
Expected: FAIL. Długość `PLATFORMS` to 8, `detect_by_domain("audycje.tokfm.pl")` zwraca `None`, a `classify_download_error` zwraca `None` dla tekstów TOK FM.

- [ ] **Step 3: Dodaj `bot/platforms/tokfm.py` i rejestrację**

`CONFIG = PlatformConfig(name="tokfm", display_name="TOK FM", domains=("audycje.tokfm.pl",), hide_flac=True, hide_time_range=True, is_podcast=True, media_label="odcinku", requires_cookies=True, cookies_hint=...)`. Tekst podpowiedzi dokładnie taki:

```python
cookies_hint=(
    "TOK FM: sesja wygasła albo brak cookies. Zaloguj się na audycje.tokfm.pl "
    "w przeglądarce, wyeksportuj cookies rozszerzeniem \"Get cookies.txt LOCALLY\" "
    "i dopisz linie z domeny .tokfm.pl (wystarczy PHPSESSID) do cookies.txt na serwerze."
),
```

W `bot/platforms/__init__.py` dopisz `tokfm` do importów (alfabetycznie) i `tokfm.CONFIG` na końcu `PLATFORMS`.

- [ ] **Step 4: Dodaj wzorce w `bot/download_errors.py`**

Na początku `_ERROR_PATTERNS`, needle'e małymi literami, teksty dokładnie takie:

```python
(
    ("tok fm premium session missing or expired",),
    "Sesja TOK FM wygasła albo na serwerze brakuje cookies TOK FM. Zaloguj się "
    "na audycje.tokfm.pl w przeglądarce, wyeksportuj cookies i dopisz linie "
    "z domeny .tokfm.pl do cookies.txt na serwerze.",
),
(
    ("tok fm: only episode links are supported",),
    "TOK FM: wyślij link do konkretnego odcinka (audycje.tokfm.pl/podcast/…). "
    "Linki do całych audycji i innych stron TOK FM nie są obsługiwane.",
),
```

Dodaj komentarz, że frazy pochodzą z `yt_dlp_plugins/extractor/tokfm.py` (`SESSION_ERROR`, `NON_EPISODE_ERROR`) i że ich zgodność pilnuje `test_tokfm_messages_match_plugin_phrases`.

- [ ] **Step 5: Uruchom testy i sprawdź, że przechodzą**

Run: `.venv/bin/python -m pytest tests/test_platforms.py tests/test_download_errors.py tests/test_tokfm_plugin.py tests/test_command_access_handlers.py tests/test_security_policy.py -q -p no:cacheprovider`
Expected: wszystkie PASS.

- [ ] **Step 6: Commit**

```bash
git add bot/platforms/tokfm.py bot/platforms/__init__.py bot/download_errors.py tests/test_platforms.py tests/test_download_errors.py
git commit -m "Register TOK FM as a cookie-backed podcast platform with session error messages"
```

---

### Task 3: README i dokumenty projektu

**Files:**
- Modify: `README.md`. Właściciel ma w nim niezacommitowane zmiany, więc do indeksu idą tylko linie TOK FM.
- Add: `docs/superpowers/specs/2026-11-18-tokfm-podcast-design.md`, `docs/superpowers/plans/2026-11-18-tokfm-podcast.md`

Treść zmian w README:
- W tabeli „Obsługiwane platformy”, po wierszu Spotify: `| TOK FM | audycje.tokfm.pl | Odcinki audycji (link audycje.tokfm.pl/podcast/…). Płatne odcinki wymagają cookies konta premium w cookies.txt; linki do całych audycji nie są obsługiwane |`
- W sekcji „Cookies”, na liście „Plik cookies.txt jest potrzebny gdy:”: `- TOK FM: odcinki premium (wystarczy ciasteczko PHPSESSID z domeny .tokfm.pl)`
- Pod tą listą akapit: przy podmianie `cookies.txt` (np. po odświeżeniu cookies YouTube) zachowaj linie `.tokfm.pl`, inaczej bot przestanie pobierać płatne odcinki i zgłosi wygaśnięcie sesji TOK FM.

- [ ] **Step 1: Wprowadź zmiany w drzewie roboczym**

Edytuj `README.md` narzędziem Edit (trzy zmiany wyżej). Zmiany właściciela zostają nietknięte.

- [ ] **Step 2: Zbuduj wersję do indeksu: HEAD plus tylko zmiany TOK FM**

```bash
S=/tmp/claude-1000/-mnt-c-code-ytdown/21f66977-7ac3-4a2f-810b-870db8592331/scratchpad
git show HEAD:README.md > "$S/README.tokfm.md"
# apply the same three TOK FM edits to "$S/README.tokfm.md" with the Edit tool
blob=$(git hash-object -w "$S/README.tokfm.md")
git update-index --cacheinfo 100644,"$blob",README.md
```

- [ ] **Step 3: Sprawdź, co trafi do commita**

Run: `git diff --cached --stat && git diff --cached README.md`
Expected: w `README.md` wyłącznie dodane linie TOK FM. `git diff README.md` (drzewo względem indeksu) nadal pokazuje zmiany właściciela.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-11-18-tokfm-podcast-design.md docs/superpowers/plans/2026-11-18-tokfm-podcast.md
git commit -m "Document TOK FM episode support and its cookies"
```

---

### Task 4: Sprawdzenie na żywo z cookies właściciela (lokalnie)

**Files:** brak zmian w repo. Skrypt i pliki tymczasowe tylko w scratchpadzie.

To jedyne zadanie, które łączy się z TOK FM. Jedynym odcinkiem jest 200878, wskazany przez właściciela. Nie badaj innych id.

- [ ] **Step 1: Napisz skrypt w scratchpadzie**

Skrypt `$S/tokfm_live_check.py` uruchamiany z katalogu repo przez `.venv/bin/python $S/tokfm_live_check.py <tryb>` (repo w `sys.path` przez `sys.path.insert(0, os.getcwd())`). Tryby:
- `with-session`: kopiuje `$S/tokfm_probe/cookies.txt` do `$S/cookies.live.txt` (chmod 600; yt-dlp nadpisuje plik cookies przy zamknięciu). Wywołuje `YoutubeDL({"quiet": True, "cookiefile": ...}).extract_info(EPISODE_URL, download=False)` i drukuje tylko `extractor_key`, `title`, `duration`, `formats[0]["ext"]`, `formats[0].get("filesize")` oraz host adresu audio (bez ścieżki z tokenem).
- `no-session`: to samo bez `cookiefile`; drukuje treść błędu.
- `series`: `https://audycje.tokfm.pl/audycja/687,Dluzsza-rozmowa` bez cookies; drukuje treść błędu.
- `download`: z sesją, `format="bestaudio"`, `outtmpl` w `$S/live/`; potem `ffprobe -v error -show_entries format=duration -of csv=p=0 <plik>`, a na koniec usunięcie pliku i `$S/cookies.live.txt`.

- [ ] **Step 2: Uruchom tryby i porównaj wyniki**

Run: `for m in with-session no-session series download; do .venv/bin/python $S/tokfm_live_check.py $m; done`
Expected:
- `with-session`: `extractor_key=TokFM`, tytuł zaczyna się od „Dłuższa rozmowa - Antrophic”, `duration=2536`, `ext=mp3`, `filesize=20287696`, host `storage.tuba.fm`;
- `no-session`: błąd zawiera `TOK FM premium session missing or expired` i `HTTP 200`;
- `series`: błąd zawiera `TOK FM: only episode links are supported`;
- `download`: plik ok. 20 MB, `ffprobe` daje ok. 2536 s.

Jeśli `filesize` to `None`, serwer nie oddał `Content-Length` na HEAD. Zgłoś to w raporcie, nie obchodź po cichu. Ścieżkę zastępczą (np. GET z `Range: bytes=0-0`) dodaje się tylko po decyzji właściciela.

- [ ] **Step 3: Posprzątaj**

Run: `rm -rf "$S/live" "$S/cookies.live.txt"; ls "$S"`
Expected: brak pobranego MP3 i kopii cookies. Oryginalny `tokfm_probe/cookies.txt` zostaje.

---

### Task 5: Pełne testy i wdrożenie na rpi5a (za zgodą właściciela)

**Files:** brak zmian w repo. Na rpi5a: `~/ytdown/cookies.txt` (poza gitem).

- [ ] **Step 1: Pełny zestaw testów lokalnie**

Run: `rm -rf /tmp/pytest-of-pi && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider --maxfail=1000 2>&1 | grep -E "^(FAILED|ERROR) | passed| failed" > "$S/final-tests.txt"; diff <(grep -E "^(FAILED|ERROR) " "$S/baseline-tests.txt" | cut -d' ' -f2 | sort) <(grep -E "^(FAILED|ERROR) " "$S/final-tests.txt" | cut -d' ' -f2 | sort) && tail -1 "$S/final-tests.txt"`
Expected: pusty `diff`, czyli te same porażki co w Task 1, Step 1. Liczba `passed` jest większa o nowe testy.

- [ ] **Step 2: Zapytaj właściciela o zgodę**

Dwa osobne pytania:
1. Czy wypchnąć `develop` i wdrożyć na rpi5a?
2. Czy dopisać jego ciasteczko `PHPSESSID` z `.tokfm.pl` do `~/ytdown/cookies.txt` na rpi5a (z kopią zapasową pliku)?

Bez zgody na (1) zakończ plan na tym kroku. Bez zgody na (2) wdrożenie przejdzie, ale płatne odcinki będą zgłaszać brak sesji, dopóki właściciel sam nie doda cookies.

- [ ] **Step 3: Push i sprawdzenie dryfu na rpi5a**

Run: `git push origin develop && ssh rpi5a 'cd ~/ytdown && git status --short && git log -1 --format=%h'`
Expected: na rpi5a czysty checkout na `c72fbdb`, poza nieśledzonymi plikami runtime (`restart.sh`, `spotify_oauth.json` itd.). Przy zmodyfikowanych plikach śledzonych zatrzymaj się i postępuj według notatki o dryfie (kopia zapasowa, potem `reset`), ale tylko po ponownej zgodzie właściciela.

- [ ] **Step 4: Pull, testy i wykrycie wtyczki na rpi5a**

Run: `ssh rpi5a 'cd ~/ytdown && git pull --ff-only origin develop && venv/bin/python -m pytest tests/ -q -p no:cacheprovider --maxfail=1000 2>&1 | tail -15 && venv/bin/python -c "import yt_dlp; yt_dlp.YoutubeDL({\"quiet\": True}); from yt_dlp.globals import plugin_ies; print(sorted(plugin_ies.value))"'`
Expected: wynik bazowy (1471 passed plus 7 porażek MCP ze środowiska) powiększony o nowe testy, bez nowych porażek. Na końcu `['TokFMIE', 'TokFMNonEpisodeIE']`.

- [ ] **Step 5: Dopisz cookies TOK FM (tylko przy zgodzie na pytanie 2)**

Jedno wywołanie z `set -e`: najpierw kopia zapasowa, a gdy kopia się nie uda, nic nie jest zmieniane. Linia ciasteczka idzie przez stdin, więc nie trafia do wiersza poleceń ani na ekran.

```bash
awk -F'\t' '$1 ~ /tokfm\.pl$/ && $6 == "PHPSESSID"' "$S/tokfm_probe/cookies.txt" | ssh rpi5a 'set -e; cd ~/ytdown
line=$(cat); [ -n "$line" ]
if [ -f cookies.txt ]; then cp -p cookies.txt "cookies.txt.bak-$(date +%Y%m%d-%H%M%S)"; else printf "# Netscape HTTP Cookie File\n" > cookies.txt; fi
chmod 600 cookies.txt
sed -i "/tokfm\.pl\t.*\tPHPSESSID\t/d" cookies.txt
[ -z "$(tail -c1 cookies.txt)" ] || echo >> cookies.txt
printf "%s\n" "$line" >> cookies.txt
grep -c tokfm cookies.txt'
```

Expected: wypisana liczba to `1`. Wartość ciasteczka nie pojawia się w żadnym wyjściu. Kopia `cookies.txt.bak-<data>` istnieje, jeśli plik był wcześniej.

- [ ] **Step 6: Restart i kontrola logów**

Run: `ssh rpi5a 'sudo systemctl restart ytdown.service && sleep 5 && systemctl is-active ytdown.service && journalctl -u ytdown.service --since "-2 min" --no-pager | grep -c "api.telegram.org/bot"; journalctl -u ytdown.service --since "-2 min" --no-pager | grep -i "error while importing\|traceback" || echo clean'`
Expected: `active`, `0`, `clean`.

- [ ] **Step 7: Próba w Telegramie (właściciel)**

Poproś właściciela o wysłanie botowi:
- linku do odcinka 200878. Oczekiwane: tytuł, „Czas trwania: 42:16”, menu podcastu bez FLAC i bez zakresu czasowego; „Audio (MP3)” przysyła plik.
- linku `https://audycje.tokfm.pl/audycja/687,Dluzsza-rozmowa`. Oczekiwane: komunikat „TOK FM: wyślij link do konkretnego odcinka…”.

Potem sprawdź `journalctl -u ytdown.service --since "-10 min" | grep -i tokfm` pod kątem błędów.
