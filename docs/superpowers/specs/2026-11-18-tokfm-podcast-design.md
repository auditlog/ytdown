# Spec: Pobieranie audycji TOK FM

- **Data:** 2026-11-18
- **Gałąź:** `develop`
- **Status:** zaakceptowany przez właściciela (2026-11-18)
- **Rozmiar:** mała zmiana (krótki projekt)

## 1. Cel i powód

Właściciel chce wysłać botowi link do odcinka z `audycje.tokfm.pl` (np.
`audycje.tokfm.pl/podcast/200878,Antrophic-Nvidia-...`) i dostać z powrotem
audio. Większość odcinków jest płatna (premium). Właściciel ma konto premium,
na które loguje się przez Wyborczą.

Właściciel nie powiedział, do czego służą nagrania. Przyjmujemy: link → audio
z tymi samymi opcjami, które bot ma dla innych podcastów (MP3, transkrypcja,
streszczenie). To nasze założenie, nie decyzja właściciela.

## 2. Cele, nie-cele, anty-cele

**Cele**
- Link do odcinka TOK FM działa w bocie tak jak link do odcinka Castbox:
  tytuł, czas trwania, menu formatów audio, transkrypcja, streszczenia,
  historia pobrań.
- Płatne odcinki są pobierane w sesji konta właściciela (cookies).
- Gdy sesja wygaśnie, bot mówi o tym wprost i podpowiada, co zrobić.

**Nie-cele**
- Pobieranie całych serii lub list odcinków (`/audycja/...`).
- Logowanie hasłem z poziomu bota.
- Strumień na żywo radia TOK FM.
- Obsługa TOK FM w kopii serwera MCP (`~/ytdown-mcp`). Ta kopia nie zawiera
  katalogu głównego repo, więc nie zobaczy wtyczki. Można to zrobić później.

**Anty-cele (porażka, nawet jeśli „działa”)**
- Pobieranie płatnego odcinka bez ważnej sesji premium. Dziś `/gets` oddaje
  pełny plik bez logowania (sprawdzone na odcinku 200878). Nie wolno z tego
  korzystać: przy braku sesji bot ma odmówić, a nie pobrać „bo się da”.
- Hasło lub cookies w repo, w logach albo w komunikatach bota.

## 3. Ograniczenia (ustalone podczas rozpoznania)

- yt-dlp nie ma ekstraktora TOK FM.
- Odtwarzacz pobiera adres pliku zapytaniem `POST https://audycje.tokfm.pl/gets`
  z nagłówkiem `X-Tuba: tokfm` i ciałem `{"pid": <id>, "st": "tokfm"}`.
  Odpowiedź: `{"url": "https://storage.tuba.fm/podcast/<token>/<hex>/.../<id>.mp3", ...}`.
  Adres jest podpisany i krótko ważny. Plik to MP3 64 kb/s (42 min ≈ 20 MB).
- Stan dostępu: `GET /premium/blokada?podcast=<id>` zwraca `204`, gdy sesja ma
  dostęp, i `200` z planszą blokady, gdy go nie ma.
- Sesję niesie ciasteczko `PHPSESSID` z domeny `.tokfm.pl`. Samo wystarcza dla
  starego i nowego backendu. Nie wiadomo, jak długo żyje po stronie serwera.
- Strona odcinka ma przy przycisku odtwarzania atrybuty `data-id`,
  `data-podcast-title` i `data-only_for_premium` (`true`/`false`).
- Logowanie Wyborczej (`login.wyborcza.pl`) korzysta z reCAPTCHA Enterprise.
  Automat w Chromium bez okna dostał `not-authorized`, dlatego bot nie loguje
  się hasłem.
- Na rpi5a działa yt-dlp 2026.07.04, a usługa uruchamia
  `/home/pi/ytdown/main.py`, więc katalog repo jest w `sys.path`.

## 4. Podejścia

| Podejście | Za | Przeciw |
|---|---|---|
| **A. Wtyczka yt-dlp w repo** (wybrane przez właściciela) | Cały istniejący przepływ (menu, transkrypcja, historia, limity) działa bez zmian. Ładuje się automatycznie we wszystkich 17 miejscach, które tworzą `YoutubeDL`. | Zależy od mechanizmu wtyczek yt-dlp (namespace `yt_dlp_plugins` w `sys.path`; tak działają wtyczki z pip). |
| B. Osobna ścieżka jak Spotify | Pełna kontrola, bez zależności od wtyczek yt-dlp. | Dużo więcej kodu: osobne gałęzie w `inbound_media.py` i callbackach, ręczne podpinanie transkrypcji i historii. |
| Logowanie hasłem w bocie (odrzucone) | Brak ręcznego eksportu cookies. | reCAPTCHA odrzuca automat, a Chromium na Raspberry Pi to ciężka zależność. |
| Bez sesji (odrzucone) | Zero konfiguracji. | Opiera się na luce, którą TOK FM może zamknąć. Patrz anty-cele. |

## 5. Projekt

**Nowe pliki**
- `yt_dlp_plugins/extractor/tokfm.py`: klasa `TokFMIE(InfoExtractor)`.
  - `_VALID_URL`: `https?://audycje\.tokfm\.pl/podcast/(?P<id>\d+)` (slug po
    przecinku opcjonalny).
  - Pobiera stronę odcinka: tytuł, prowadzący, goście, flaga premium,
    miniatura.
  - Jeśli odcinek jest premium, sprawdza `/premium/blokada?podcast=<id>`.
    Wynik inny niż `204` oznacza `ExtractorError` z charakterystyczną frazą
    (np. `TOK FM premium session missing or expired`), bez pobierania pliku.
  - Pyta `/gets` i zwraca jeden format: `ext=mp3`, `acodec=mp3`,
    `vcodec=none`, `abr=64`.
  - Cookies dostaje z `YoutubeDL`, czyli z istniejącego `cookies.txt`. Nie
    czyta żadnego własnego pliku.
  - Bez `__init__.py` w `yt_dlp_plugins/` i `yt_dlp_plugins/extractor/`
    (to pakiety typu namespace).
- `bot/platforms/tokfm.py`: `PlatformConfig(name="tokfm", display_name="TOK FM",
  domains=("audycje.tokfm.pl",), is_podcast=True, hide_flac=True,
  hide_time_range=True, media_label="odcinku", requires_cookies=True,
  cookies_hint=<po polsku: wyeksportuj cookies z audycje.tokfm.pl po zalogowaniu>)`.
  Dopisany do `PLATFORMS` w `bot/platforms/__init__.py`.

**Zmiany w istniejących plikach**
- `bot/download_errors.py`: fraza z ekstraktora → komunikat po polsku, np.
  „Sesja TOK FM wygasła albo brak cookies. Zaloguj się na audycje.tokfm.pl,
  wyeksportuj cookies.txt i podmień plik na serwerze.”
- Link do serii (`/audycja/...`) → komunikat po polsku „wyślij link do
  konkretnego odcinka”, tak jak dziś dla kanału Castbox.
- `README.md`: wiersz TOK FM w tabeli platform oraz opis cookies.

**Wdrożenie (po akceptacji kodu)**
- Linie TOK FM z cookies właściciela trzeba dopisać do `~/ytdown/cookies.txt`
  na rpi5a. Zrobi to właściciel albo Claude za jego zgodą. Nie trafiają do git.

**Testy**
- Ekstraktor z podmienionymi odpowiedziami HTTP: odcinek darmowy, premium
  z sesją (`204`), premium bez sesji (`200` → błąd, brak zapytania `/gets`),
  zła odpowiedź `/gets`.
- Rozpoznanie linku: `detect_platform`, `validate_url`, link do serii.
- Mapowanie błędu sesji na komunikat po polsku.
- Test, że yt-dlp faktycznie znajduje wtyczkę, gdy katalog repo jest
  w `sys.path`.

## 6. Zostawione budowniczemu

- Czas trwania: z metadanych strony, jeśli są. Jeśli nie, oszacowanie
  z `Content-Length` przy 64 kb/s.
- Dokładne brzmienie komunikatów i podpowiedzi o cookies.
- Czy dopuścić też linki `audycje.tokfm.pl/player/v2/<id>`.
