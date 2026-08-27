# Media Downloader Telegram Bot

Bot Telegram do pobierania video/audio z YouTube, Vimeo, TikTok, Instagram, LinkedIn i X z funkcjami transkrypcji i podsumowań AI.

## Funkcje

### Podstawowe
- **Multi-platform**: pobieranie z YouTube, Vimeo, TikTok, Instagram, LinkedIn (via yt-dlp), podcastów Spotify oraz utworów dopasowanych w YT Music
- Pobieranie video w różnych formatach (1080p, 720p, 480p, 360p)
- Ekstrakcja ścieżek audio (MP3, M4A, FLAC, WAV, Opus)
- Automatyczna transkrypcja audio (Groq API - Whisper Large v3)
- **Napisy YouTube jako źródło transkrypcji** — natychmiastowe pobieranie napisów (manualnych lub automatycznych) bez zużycia tokenów AI
- Generowanie podsumowań transkrypcji (Claude API - Haiku 4.5)
- **Transkrypcja przesłanych plików audio** — wiadomości głosowe, pliki audio i dokumenty audio (np. notatki głosowe z WhatsApp)
- **Transkrypcja przesłanych plików video** — ekstrakcja audio z MP4, MKV, AVI, MOV, WebM
- Ochrona dostępu kodem PIN
- Interfejs wiersza poleceń (CLI) z pełnym wsparciem dla wyboru formatu, jakości i audio
- Bot Telegram z interaktywnym menu
- Warunkowe menu per platforma (np. TikTok: ukryty FLAC i zakres czasowy)

### Obsługiwane platformy
| Platforma | Domeny | Uwagi |
|-----------|--------|-------|
| YouTube | youtube.com, youtu.be, music.youtube.com | Pełne wsparcie (video, audio, napisy, zakres czasowy) |
| Vimeo | vimeo.com, player.vimeo.com | Video, audio, transkrypcja |
| TikTok | tiktok.com, vm.tiktok.com, m.tiktok.com | Uproszczone menu (krótkie video) |
| Instagram | instagram.com | Reels i posty video przez yt-dlp. Zdjęcia i karuzele wymagają dodatkowo `instaloader` oraz `cookies.txt` |
| LinkedIn | linkedin.com | Posty video. Wymaga cookies.txt |
| X (Twitter) | x.com, twitter.com, mobile.twitter.com | Video z tweetów. Treści oznaczone jako Sensitive wymagają cookies.txt |
| Spotify | open.spotify.com | Odcinki podcastów oraz linki do utworów, albumów i playlist. Muzyka jest dopasowywana do oficjalnych publikacji YT Music/YouTube; natywne wideo podcastów wymaga cookies Spotify |

### Bezpieczeństwo
- Rate limiting - max 10 requestów/minutę per użytkownik
- Limit rozmiaru plików - max 1GB
- Walidacja URL - whitelist domen (YouTube, Vimeo, TikTok, Instagram, LinkedIn, Spotify), wymagany HTTPS
- Blokada po 3 nieudanych próbach PIN (15 minut)
- Logowanie nieudanych prób PIN + powiadomienia Telegram do admina
- Walidacja format_id przed przekazaniem do yt-dlp
- Walidacja zakresu czasowego względem długości filmu
- Komenda `/logout` do zakończenia sesji
- Wsparcie dla zmiennych środowiskowych
- JSON persistence dla autoryzowanych użytkowników
- Historia pobrań z rozróżnieniem sukcesów i błędów

### Zarządzanie plikami
- Automatyczne czyszczenie plików starszych niż 24h
- Agresywne czyszczenie (6h) gdy mało miejsca na dysku (<5GB)
- Monitoring przestrzeni dyskowej
- Katalogi per użytkownik (chat_id)

## Wymagania

- Python 3.11+
- ffmpeg (zainstalowany w systemie)
- deno (wymagany przez yt-dlp do rozwiązywania YouTube JS challenges)
- Poetry (opcjonalnie, zalecane) lub pip

### Zależności opcjonalne

- `pyrogram` - potrzebny do pobierania dużych plików z Telegrama przez MTProto
- `instaloader` - potrzebny do zdjęć i karuzel z Instagrama

Instalacja opcjonalnych dodatków:

```bash
pip install pyrogram
pip install instaloader
```

## Instalacja

### Opcja 1: Instalacja z Poetry (zalecane)

```bash
# Klonuj repozytorium
git clone https://github.com/auditlog/ytdown.git
cd ytdown

# Zainstaluj Poetry (jeśli nie masz)
curl -sSL https://install.python-poetry.org | python3 -
# lub na Windows: (Invoke-WebRequest -Uri https://install.python-poetry.org -UseBasicParsing).Content | py -

# Zainstaluj zależności projektu
poetry install

# Aktywuj środowisko wirtualne
poetry shell

# Lub uruchom bezpośrednio przez Poetry
poetry run python main.py
```

Poetry instaluje zależności z `pyproject.toml`. Biblioteki opcjonalne, takie jak `instaloader`, doinstaluj osobno, jeśli chcesz używać tych funkcji.

### Opcja 2: Instalacja z pip (tradycyjna)

```bash
# Klonuj repozytorium
git clone https://github.com/auditlog/ytdown.git
cd ytdown

# Utwórz środowisko wirtualne
python -m venv venv
source venv/bin/activate  # Linux/macOS
# lub: venv\Scripts\activate  # Windows

# Zainstaluj zależności
pip install -r requirements.txt
```

Jeśli chcesz obsługi MTProto lub zdjęć/karuzel z Instagrama, doinstaluj również zależności opcjonalne:

```bash
pip install instaloader
```

### Instalacja deno

Deno jest wymagany przez yt-dlp jako JavaScript runtime do rozwiązywania YouTube JS challenges (szyfrowanie podpisów URL i n-parameter). Bez niego część filmów YouTube zwróci błąd "This video is not available".

```bash
curl -fsSL https://deno.land/install.sh | sh
```

Po instalacji upewnij się, że `~/.deno/bin` jest w PATH.

### Instalacja ffmpeg

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install ffmpeg
```

**macOS:**
```bash
brew install ffmpeg
```

**Windows:**
- Pobierz z [ffmpeg.org](https://ffmpeg.org/download.html)
- Rozpakuj i dodaj do PATH

## Konfiguracja

### Opcja 1: Interaktywna konfiguracja (zalecane)

```bash
python setup_config.py
```

### Opcja 2: Plik konfiguracyjny

Utwórz plik `api_key.md` w głównym katalogu:

```
TELEGRAM_BOT_TOKEN=twój_token_bota
GROQ_API_KEY=twój_klucz_groq
CLAUDE_API_KEY=twój_klucz_claude
PIN_CODE=12345678
ADMIN_CHAT_ID=twój_telegram_user_id
SPOTIFY_CLIENT_ID=twój_spotify_client_id
SPOTIFY_CLIENT_SECRET=twój_spotify_client_secret
```

`PIN_CODE` musi mieć dokładnie 8 cyfr.

Klucze Spotify uzyskasz na [Spotify Developer Dashboard](https://developer.spotify.com/) — utwórz aplikację z Web API.
Są potrzebne **wyłącznie** starszej ścieżce audio (iTunes/YouTube). Pobieranie wideo prosto ze Spotify
ich nie używa — wymaga za to pliku `spotify_cookies.txt` (patrz [Cookies](#cookies-opcjonalne-dla-platform-wymagających-logowania)).

**UWAGA**: Plik `api_key.md` jest ignorowany przez git - nie commituj go do repozytorium!

### Opcja 3: Zmienne środowiskowe (najbezpieczniejsze)

**Linux/macOS/WSL:**
```bash
export TELEGRAM_BOT_TOKEN="twój_token"
export GROQ_API_KEY="twój_klucz"
export CLAUDE_API_KEY="twój_klucz"
export PIN_CODE="12345678"
export ADMIN_CHAT_ID="twój_telegram_user_id"
export SPOTIFY_CLIENT_ID="twój_spotify_client_id"
export SPOTIFY_CLIENT_SECRET="twój_spotify_client_secret"
```

**Windows (PowerShell):**
```powershell
$env:TELEGRAM_BOT_TOKEN="twój_token"
$env:GROQ_API_KEY="twój_klucz"
$env:CLAUDE_API_KEY="twój_klucz"
$env:PIN_CODE="12345678"
$env:ADMIN_CHAT_ID="twój_telegram_user_id"
$env:SPOTIFY_CLIENT_ID="twój_spotify_client_id"
$env:SPOTIFY_CLIENT_SECRET="twój_spotify_client_secret"
```

### Jak uzyskać ADMIN_CHAT_ID?

`ADMIN_CHAT_ID` to Twój numeryczny identyfikator użytkownika Telegram. Bot wysyła na ten ID powiadomienia o nieudanych próbach logowania i blokadach. Aby go poznać:

1. Napisz do bota [@userinfobot](https://t.me/userinfobot) na Telegramie
2. Bot odpowie Twoim ID (np. `123456789`)
3. Wpisz ten numer jako `ADMIN_CHAT_ID` w konfiguracji

Parametr jest opcjonalny — bez niego bot działa normalnie, ale nie wysyła powiadomień bezpieczeństwa.

### Cookies (opcjonalne, dla platform wymagających logowania)

Plik `cookies.txt` jest potrzebny gdy:
- YouTube blokuje pobieranie komunikatem "Sign in to confirm you're not a bot"
- Instagram wymaga logowania do treści
- LinkedIn wymaga logowania do postów video
- TikTok blokuje pobieranie bez sesji

Dodatkowo:
- zdjęcia i karuzele z Instagrama wymagają zainstalowanego `instaloader`
- duże pliki z Telegrama wymagają `pyrogram` oraz `TELEGRAM_API_ID` i `TELEGRAM_API_HASH`

Jak uzyskać cookies:
1. Zainstaluj rozszerzenie **"Get cookies.txt LOCALLY"** w przeglądarce (Chrome/Firefox)
2. Zaloguj się na daną platformę (YouTube, Instagram, LinkedIn, TikTok)
3. Wyeksportuj cookies do pliku `cookies.txt`
4. Umieść plik w głównym katalogu projektu (`ytdown/cookies.txt`)

Bot automatycznie wykrywa brak cookies i wyświetla odpowiedni komunikat.

**UWAGA**: Plik `cookies.txt` zawiera dane sesji — nie udostępniaj go i nie commituj do repozytorium! Jest ignorowany przez git.

#### Osobny plik cookies dla Spotify (`spotify_cookies.txt`)

Pobieranie wideo i audio prosto ze Spotify korzysta z **osobnego** słoika ciasteczek —
`spotify_cookies.txt` w głównym katalogu projektu (ścieżkę zmienia klucz `SPOTIFY_COOKIES_FILE`).
Rozpoznawana jest również typowa nazwa eksportu `open.spotify.com_cookies.txt`.
Jest celowo oddzielony od `cookies.txt`: ciasteczko `sp_dc` uwierzytelnia odtwarzacz webowy
Spotify i nie ma nic wspólnego z yt-dlp, więc mieszanie obu plików byłoby mylące.

Jak go przygotować:
1. Zaloguj się na `open.spotify.com` w przeglądarce
2. Wyeksportuj ciasteczka tej domeny do formatu Netscape (to samo rozszerzenie co wyżej)
3. Zapisz plik jako `ytdown/spotify_cookies.txt` — musi zawierać wpis `sp_dc`

Ta ścieżka **nie wymaga** `SPOTIFY_CLIENT_ID` ani `SPOTIFY_CLIENT_SECRET` — komplet metadanych
odcinka pochodzi ze strony embed. Klucze Web API są nadal potrzebne wyłącznie starszej ścieżce
audio (dopasowanie odcinka w iTunes lub na YouTube), która obsługuje odcinki bez wideo.

Link do pojedynczego utworu wyświetla wybór MP3/M4A po dopasowaniu tytułu, wykonawcy i czasu
do YT Music. Link do albumu lub playlisty otwiera stronicowane menu wielokrotnego wyboru
(maksymalnie 500 pozycji). Zaznaczone utwory można wysłać pojedynczo albo spakować jako MP3/M4A
do archiwów 7z po 50, 100 lub wszystkie naraz. Każde archiwum jest dodatkowo dzielone na
wolumeny do około 1 GB. Odczyt playlist wymaga jednorazowego połączenia konta właściciela lub
współpracownika przez Spotify OAuth. Bot nie pobiera chronionego strumienia muzycznego Spotify
i nie obchodzi DRM.

#### Autoryzacja playlist Spotify (OAuth)

Same cookies `sp_dc` uwierzytelniają odtwarzacz embed, ale nie nadają botowi uprawnień Web API
do playlist użytkownika. Dla playlist skonfiguruj:

```text
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
SPOTIFY_REDIRECT_URI=https://twoj-host.example/spotify/callback
SPOTIFY_OAUTH_CALLBACK_HOST=127.0.0.1
SPOTIFY_OAUTH_CALLBACK_PORT=8091
```

Adres `SPOTIFY_REDIRECT_URI` musi być wpisany identycznie w Redirect URIs aplikacji w Spotify
Developer Dashboard. Publiczne HTTPS powinno przekazywać tę ścieżkę do lokalnego listenera
HTTP na skonfigurowanym porcie. Następnie administrator używa `/spotify_login` w Telegramie
i zatwierdza dwa zakresy tylko do odczytu: `playlist-read-private` oraz
`playlist-read-collaborative`. Token jest zapisywany w `spotify_oauth.json` z uprawnieniami
`600` i automatycznie odświeżany. `/spotify_logout` usuwa lokalną autoryzację.

**UWAGA**: `spotify_cookies.txt` to żywe poświadczenie sesji — traktuj go jak hasło.
Wzorce `*cookies*.txt` są ignorowane przez git.

### Pliki runtime i lokalne artefakty

Te pliki nie są częścią kodu aplikacji i powinny pozostać lokalne:

- `.env`
- `api_key.md`
- `cookies.txt`
- `spotify_cookies.txt`
- `spotify_oauth.json`
- `authorized_users.json`
- `download_history.json`
- `downloads/`
- `backup/`

Repozytorium powinno zawierać kod, testy i konfigurację projektu, ale nie lokalne sekrety, backupy ani dane runtime.

## Uruchomienie

### Bot Telegram
```bash
python main.py
# lub (Poetry):
poetry run python main.py
```

### Tryb CLI (interfejs tekstowy)

#### Opcje wiersza poleceń

| Opcja | Opis | Domyślnie |
|-------|------|-----------|
| `--cli` | Uruchom w trybie wiersza poleceń (wymagane) | - |
| `--url <URL>` | URL filmu YouTube | - |
| `--list-formats` | Wyświetl dostępne formaty bez pobierania | - |
| `--format <ID>` | Pobierz konkretny format (ID z listy formatów) | najlepsza jakość |
| `--format auto` | Automatyczny wybór najlepszej jakości | - |
| `--audio-only` | Pobierz tylko ścieżkę audio | - |
| `--audio-format <FORMAT>` | Format audio: mp3, m4a, wav, flac, opus, vorbis | mp3 |
| `--audio-quality <QUALITY>` | Jakość audio (0-9 dla vorbis/opus, 0-330 dla mp3) | 192 |
| `--start <TIMESTAMP>` | Czas rozpoczęcia klipu (SS, MM:SS, HH:MM:SS) | - |
| `--to <TIMESTAMP>` | Czas zakończenia klipu (SS, MM:SS, HH:MM:SS) | - |

#### Przykłady użycia

```bash
# Pobierz film w najlepszej jakości
python main.py --cli --url "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# Wyświetl dostępne formaty
python main.py --cli --url "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --list-formats

# Pobierz konkretny format (np. 137 = 1080p)
python main.py --cli --url "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --format 137

# Pobierz samo audio jako MP3
python main.py --cli --url "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --audio-only

# Pobierz audio w formacie FLAC z najwyższą jakością
python main.py --cli --url "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --audio-only --audio-format flac --audio-quality 0

# Pobierz fragment filmu (od 1:30 do 5:00)
python main.py --cli --url "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --start 1:30 --to 5:00
```

### Testy
```bash
# Uruchom wszystkie testy
python -m pytest tests/

# Uruchom z widocznym postępem
python -m pytest tests/ -v

# Uruchom konkretny plik testowy
python -m pytest tests/test_subtitles.py -v
```

## Komendy bota Telegram

| Komenda | Opis |
|---------|------|
| `/start` | Rozpocznij korzystanie z bota |
| `/help` | Pomoc i instrukcje |
| `/status` | Sprawdź przestrzeń dyskową i statystyki |
| `/history` | Historia pobrań i statystyki użytkownika |
| `/cleanup` | Ręczne usunięcie starych plików |
| `/users` | Zarządzanie autoryzowanymi użytkownikami |
| `/spotify_login` | Połącz konto Spotify do odczytu prywatnych i współdzielonych playlist (administrator) |
| `/spotify_logout` | Usuń zapisaną autoryzację Spotify (administrator) |
| `/logout` | Wyloguj się z bota (zakończ sesję) |

## Używanie bota

### Pobieranie video/audio z platform
1. Znajdź swojego bota na Telegramie
2. Wyślij `/start`
3. Wprowadź kod PIN
4. Wyślij link z obsługiwanej platformy (YouTube, Vimeo, TikTok, Instagram, LinkedIn, Spotify)
5. Wybierz format i jakość
6. Opcjonalnie: wybierz transkrypcję lub streszczenie
   - Jeśli film ma napisy YouTube — możesz wybrać gotowe napisy (natychmiastowo, 0 tokenów) lub transkrypcję AI (minuty, tokeny Groq/Claude)

### Podcasty Spotify
1. Wyślij link do odcinka podcastu ze Spotify (`open.spotify.com/episode/...`)
2. Bot sprawdza, czy odcinek ma wideo, i buduje klawiaturę z faktycznie dostępnych źródeł —
   przycisk, który przy obecnej konfiguracji nie zadziałałby, nie jest pokazywany
3. Dla odcinków wideo (wymagany `spotify_cookies.txt`): `Video 1080p`, `720p`, `480p`, `320p`
   oraz `Audio (M4A) — Spotify`, pobierane bezpośrednio z CDN Spotify. Etykieta przycisku
   zawiera szacowany rozmiar pliku
4. Dla odcinków bez wideo (lub bez ciasteczek): starsza ścieżka audio — bot wyszuka odcinek
   w iTunes (priorytet — bezpośredni MP3) lub na YouTube (fallback). Wymaga
   `SPOTIFY_CLIENT_ID` i `SPOTIFY_CLIENT_SECRET`
5. Transkrypcja: gdy odcinek ma napisy WebVTT, bot używa ich jako źródła transkrypcji —
   wynik jest natychmiast, bez kosztu API i bez limitu długości nagrania. Gdy napisów nie ma,
   bot pobiera ścieżkę audio i przepuszcza ją przez Groq, tak jak dotąd
6. Odcinki oznaczone jako chronione DRM są odrzucane — bot nie obchodzi zabezpieczeń

**Wysyłka plików wideo wymaga skonfigurowanego MTProto.** Każda jakość wideo przekracza limit
50 MB Telegram Bot API: dla odcinka trwającego ~40 minut jest to około 103 MiB (320p),
192 MiB (480p), 336 MiB (720p) i 621 MiB (1080p). Bez `TELEGRAM_API_ID` i `TELEGRAM_API_HASH`
(oraz zainstalowanego `pyrogram`) bot pobierze plik, ale nie będzie miał czym go wysłać i
poprosi o uzupełnienie konfiguracji. Podział pliku na wolumeny `7z` (dostępny na zwykłej ścieżce
pobierania, wymaga binarki `7z` w PATH) nie jest jeszcze podpięty do ścieżki Spotify wideo.

### Utwory, albumy i playlisty Spotify

1. Dla playlist prywatnych lub współdzielonych administrator jednorazowo wykonuje
   `/spotify_login` i zatwierdza dostęp tylko do odczytu.
2. Wyślij link `open.spotify.com/track/...`, `album/...` albo `playlist/...`.
3. Dla albumu lub playlisty zaznacz wybrane pozycje albo użyj **Zaznacz wszystkie**.
4. Wybierz pojedynczą wysyłkę MP3/M4A lub **MP3/M4A → paczki 7z**.
5. Dla archiwum wybierz 50, 100 albo wszystkie utwory w jednej logicznej paczce. Paczka
   przekraczająca około 1 GB zostanie automatycznie podzielona na wolumeny `.7z.001`,
   `.7z.002` itd.; wszystkie wolumeny trzeba zapisać w jednym katalogu i otworzyć `.001`.

Muzyka jest wyszukiwana przede wszystkim w YT Music na podstawie tytułu, wykonawcy i czasu
trwania. Bot nie pobiera strumienia muzycznego Spotify i nie obchodzi DRM.

### Transkrypcja plików audio
1. Wyślij wiadomość głosową, plik audio lub dokument audio (np. notatkę głosową z WhatsApp)
2. Wybierz opcję: "Transkrypcja" lub "Transkrypcja + Podsumowanie"
3. Obsługiwane formaty: OGG, OPUS, MP3, M4A, WAV, FLAC, WebM, AAC, AMR, CAF
4. Limit rozmiaru: 20 MB (ograniczenie Telegram Bot API)

### Transkrypcja plików video
1. Wyślij plik video (jako natywne video lub dokument)
2. Bot automatycznie wyekstrahuje audio (ffmpeg)
3. Wybierz opcję: "Transkrypcja" lub "Transkrypcja + Podsumowanie"
4. Obsługiwane formaty: MP4, MOV, MKV, AVI, WebM
5. Limit rozmiaru: 20 MB (ograniczenie Telegram Bot API)

## Typy streszczeń

Bot oferuje 4 typy streszczeń AI (Claude Haiku 4.5):
1. Krótkie podsumowanie
2. Szczegółowe podsumowanie
3. Punkty kluczowe
4. Lista zadań

## Struktura projektu

```
ytdown/
├── main.py                         # Entry point aplikacji
├── bot/                            # Główny pakiet aplikacji
│   ├── __init__.py                 # Publiczne API pakietu (moduły, nie funkcje)
│   ├── config.py                   # Bootstrap konfiguracji + aktywny cache autoryzacji runtime
│   ├── runtime.py                  # Kontener AppRuntime, config accessors i auth helpery
│   ├── session_store.py            # SessionStore — chat-scoped state w pamięci
│   ├── session_context.py          # Shared session bridge (auth state, flow fields)
│   ├── repositories.py             # Persystencja JSON (authorized_users, history)
│   ├── security.py                 # Fasada kompatybilności — deleguje do wyspecjalizowanych security_*
│   ├── security_limits.py          # Stałe limitów: rate limit, rozmiary plików, timeouty, playlist
│   ├── security_policy.py          # Walidacja URL, wykrywanie platform, estymacja rozmiaru
│   ├── security_throttling.py      # Rate limiting per użytkownik
│   ├── security_pin.py             # Blokowanie po nieudanych próbach PIN
│   ├── security_authorization.py   # Persystencja autoryzowanych użytkowników
│   ├── cleanup.py                  # Czyszczenie plików i monitoring dysku
│   ├── transcription.py            # Fasada kompatybilności — deleguje do wyspecjalizowanych transcription_*
│   ├── transcription_limits.py     # Stałe limitów, heurystyki tokenów, progi czasowe
│   ├── transcription_chunking.py   # Dzielenie MP3 na części (silence detection, split)
│   ├── transcription_providers.py  # Adaptery API: Groq Whisper i Claude (transkrypcja, korekta, podsumowania)
│   ├── transcription_pipeline.py   # Orkiestracja pipeline'u transkrypcji MP3
│   ├── downloader.py               # Fasada kompatybilności — deleguje do wyspecjalizowanych downloader_*
│   ├── downloader_core.py          # Standalone download (progress_hook, download_youtube_video) dla CLI
│   ├── downloader_validation.py    # Walidacja formatów, parsowanie czasu, sanityzacja nazw plików
│   ├── downloader_playlist.py      # Wykrywanie i pobieranie metadanych playlist YouTube
│   ├── downloader_metadata.py      # Pobieranie metadanych video (get_video_info)
│   ├── downloader_subtitles.py     # Napisy: wykrywanie, pobieranie i parsowanie VTT/SRT
│   ├── downloader_media.py         # Thumbnails, Instagram info, photo download, is_photo_entry
│   ├── spotify.py                  # Web API Spotify, metadane kolekcji i dopasowanie YT Music
│   ├── spotify_oauth.py            # OAuth playlist Spotify, callback, token i odświeżanie
│   ├── spotify_video.py            # Manifesty i pobieranie natywnego wideo Spotify
│   ├── mtproto.py                  # Upload dużych plików przez MTProto (Pyrogram)
│   ├── cli.py                      # Frontend CLI — korzysta z download_service (prepare/execute)
│   ├── telegram_commands.py        # Cienki wrapper kompatybilności — deleguje do handler layer
│   ├── telegram_callbacks.py       # Cienki wrapper kompatybilności — deleguje do handler layer
│   ├── handlers/                   # Wydzielone flow handlery (bez cross-importów do routerów)
│   │   ├── command_access.py       # Auth/admin/info: /start, PIN, /logout, /help, /status
│   │   ├── inbound_media.py        # Intake URL-i, routing platform, playlist entry
│   │   ├── inbound_audio.py        # Upload i przetwarzanie plików audio
│   │   ├── inbound_video.py        # Upload i przetwarzanie plików video
│   │   ├── download_callbacks.py   # Core download flow i progress
│   │   ├── spotify_callbacks.py    # Pobieranie odcinków Spotify (transkrypcja, podsumowania)
│   │   ├── spotify_auth_commands.py # /spotify_login i /spotify_logout
│   │   ├── spotify_collection_callbacks.py # Wybór utworów i paczek 7z Spotify
│   │   ├── playlist_callbacks.py   # Playlist callback flows (browse, download items)
│   │   ├── media_extras_callbacks.py # Instagram photos/videos, format list, Spotify summary
│   │   ├── transcription_callbacks.py # Transkrypcja, napisy, podsumowania
│   │   ├── time_range_callbacks.py # Menu zakresów czasowych i presety
│   │   ├── callback_parsing.py     # Parsery callback payload (download, summary)
│   │   ├── time_range.py           # Wspólny parser zakresów czasowych
│   │   └── common_ui.py           # Centralny hub UI: klawiatury, Markdown, formatowanie
│   └── services/                   # Logika biznesowa niezależna od Telegrama
│       ├── auth_service.py         # PIN, login/logout, security state reset
│       ├── download_service.py     # Planowanie i wykonywanie pobrań
│       ├── archive_service.py      # Pakowanie, wysyłka i wznawianie wolumenów 7z
│       ├── playlist_service.py     # Obsługa playlist (budowanie, pobieranie itemów)
│       ├── spotify_service.py      # Audio Spotify/YouTube Music i kolekcje
│       ├── spotify_archive_service.py # Grupowe archiwa 50/100/całość dla Spotify
│       ├── spotify_video_service.py # Natywne audio/wideo i napisy Spotify
│       └── transcription_service.py # Artefakty transkrypcji i podsumowań
├── setup_config.py                 # Narzędzie konfiguracyjne
├── tests/                          # Testy (~979 testów)
│   ├── conftest.py                 # Współdzielone fixtures
│   ├── test_security.py            # Testy bezpieczeństwa
│   ├── test_security_unit.py       # Testy PIN, blokowania, security reset
│   ├── telegram_commands_support.py # Wspólne helpery testów komend/handlerów
│   ├── test_command_access_handlers.py # Testy auth, PIN, admin, /help, /status
│   ├── test_inbound_media_handlers.py  # Testy URL intake, audio upload, platformy
│   ├── test_video_upload_handlers.py   # Testy upload/przetwarzania video
│   ├── telegram_callbacks_support.py # Wspólne helpery testów callbacków
│   ├── test_callback_common.py     # Testy routera callbacków, rate limit, sesji
│   ├── test_callback_download_handlers.py # Testy pobierania, playlist, time range
│   ├── test_callback_transcription_handlers.py # Testy transkrypcji, napisów, Spotify
│   ├── test_telegram_integration.py # Testy integracyjne cross-module (PIN→URL, callback routing)
│   ├── test_auth_service.py        # Testy auth service (PIN, logout)
│   ├── test_runtime.py             # Testy runtime auth helperów
│   ├── test_session_store.py       # Testy SessionStore i session cleanup
│   ├── test_repositories.py        # Testy persystencji JSON
│   ├── test_spotify.py             # Testy Spotify podcastów
│   ├── test_spotify_oauth.py       # Testy OAuth, callbacku i odświeżania tokenu
│   ├── test_spotify_tracks.py      # Testy utworów, albumów, playlist i YT Music
│   ├── test_spotify_archive_service.py # Testy grupowania i archiwów Spotify
│   ├── test_downloader.py          # Testy downloadera, walidacji czasu
│   ├── test_download_history.py    # Testy historii pobrań
│   ├── test_cli.py                 # Testy CLI
│   ├── test_download_service.py    # Testy planowania i wykonywania pobrań
│   ├── test_security_policy.py     # Testy walidacji URL i detekcji platform
│   ├── test_security_pin_module.py # Testy blokowania PIN
│   ├── test_security_throttling.py # Testy rate limitingu
│   ├── test_security_authorization.py # Testy manage_authorized_user + thread safety
│   ├── test_transcription_providers.py # Testy adapterów Groq/Claude (retry, extraction)
│   ├── test_transcription_pipeline.py  # Testy orkiestracji pipeline transkrypcji
│   ├── test_transcription_chunking.py  # Testy split_mp3, find_silence_points, get_part_number
│   ├── test_downloader_media.py    # Testy is_photo_entry, download_photo, thumbnails, Instagram
│   ├── test_concurrency.py         # Testy współbieżności (SessionStore, rate limit, PIN)
│   └── ...                         # Pozostałe testy
├── api_key.md                      # Konfiguracja (ignorowany przez git)
├── cookies.txt                     # Cookies YouTube (ignorowany przez git)
├── authorized_users.json           # Lista autoryzowanych użytkowników (ignorowany)
├── README.md                       # Ten plik
└── downloads/                      # Pobrane pliki (ignorowany)
    └── [chat_id]/                  # Pliki per użytkownik
```

## Bezpieczeństwo

- Pełny audyt bezpieczeństwa (15 poprawek: 1 krytyczna, 3 wysokie, 10 średnich, 1 niska)
- Klucze API w gitignore
- Cookies YouTube w gitignore (`cookies.txt`)
- Rate limiting (10 req/min)
- Limit plików (1GB)
- Tylko HTTPS (whitelist domen)
- Blokada po złym PIN
- Autoryzacja zapisywana w JSON

## Ograniczenia

- Max 20MB dla pojedynczej części transkrypcji (większe pliki są dzielone automatycznie)
- Max 20MB dla przesyłanych plików audio/video (limit Telegram Bot API dla pobierania plików przez bota)
- Telegram limit: 50MB dla plików, 4096 znaków dla wiadomości
- Korekta AI transkrypcji: do ~4.5h materiału audio (powyżej automatycznie pomijana)
- Podsumowanie AI: do ~14h materiału audio (powyżej automatycznie pomijane)
- Sama transkrypcja (Whisper) i napisy YouTube działają bez limitu długości
- Instagram, LinkedIn, TikTok mogą wymagać cookies.txt do pobierania
- Instagram zdjęcia/karuzele wymagają instaloader z ważną sesją w cookies.txt
- YouTube wymaga deno jako JS runtime — bez niego część filmów zwróci "This video is not available"

## Rozwiązywanie problemów

**Bot nie odpowiada:**
- Sprawdź czy token jest poprawny
- Sprawdź połączenie internetowe
- Sprawdź logi w konsoli

**"This video is not available" dla filmów YouTube:**
- Upewnij się, że deno jest zainstalowany i dostępny w PATH (`deno --version`)
- Zaktualizuj yt-dlp do najnowszej wersji (`pip install --upgrade yt-dlp`)
- YouTube wymaga JS runtime do rozwiązywania challenges — bez deno część filmów nie zadziała

**Błąd transkrypcji:**
- Sprawdź klucz API Groq
- Sprawdź rozmiar pliku audio

**Plik za duży:**
- Wybierz niższą jakość
- Pobierz tylko audio

**Brak miejsca na dysku:**
- Użyj `/cleanup` do usunięcia starych plików
- Sprawdź `/status` dla statystyk

## Wkład w projekt

1. Fork repozytorium
2. Stwórz branch (`git checkout -b feature/AmazingFeature`)
3. Commit zmiany (`git commit -m 'Add AmazingFeature'`)
4. Push do branch (`git push origin feature/AmazingFeature`)
5. Otwórz Pull Request

## Licencja

Ten projekt jest dostępny na licencji MIT.

## Podziękowania

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - pobieranie mediów z platform (YouTube, Vimeo, TikTok, Instagram, LinkedIn)
- [instaloader](https://github.com/instaloader/instaloader) - pobieranie zdjęć i karuzel z Instagrama
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) - API Telegram
- [Groq](https://groq.com/) - transkrypcja audio (Whisper)
- [Anthropic Claude](https://www.anthropic.com/) - generowanie podsumowań (Haiku 4.5)

## Language Policy

- **Bot Interface**: Polish - all user interactions and messages
- **Development**: English - code, comments, documentation
