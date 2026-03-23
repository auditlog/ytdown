# Media Downloader Telegram Bot

Bot Telegram do pobierania video/audio z YouTube, Vimeo, TikTok, Instagram i LinkedIn z funkcjami transkrypcji i podsumowań AI.

## Funkcje

### Podstawowe
- **Multi-platform**: pobieranie z YouTube, Vimeo, TikTok, Instagram, LinkedIn (via yt-dlp), Spotify podcasty (via iTunes/YouTube)
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
| Spotify | open.spotify.com | Odcinki podcastów. Wymaga SPOTIFY_CLIENT_ID/SECRET. Audio z iTunes lub YouTube |

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

- Python 3.12+
- ffmpeg (zainstalowany w systemie)
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

### Pliki runtime i lokalne artefakty

Te pliki nie są częścią kodu aplikacji i powinny pozostać lokalne:

- `.env`
- `api_key.md`
- `cookies.txt`
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
2. Bot automatycznie wyszuka audio w iTunes (priorytet — bezpośredni MP3) lub na YouTube (fallback)
3. Wybierz opcję: Audio (MP3), Transkrypcja lub Transkrypcja + Podsumowanie
4. Wymaga skonfigurowania `SPOTIFY_CLIENT_ID` i `SPOTIFY_CLIENT_SECRET`

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
│   ├── __init__.py                 # Eksporty pakietu
│   ├── config.py                   # Bootstrap konfiguracji + aktywny cache autoryzacji runtime
│   ├── runtime.py                  # Kontener AppRuntime, config accessors i auth helpery
│   ├── session_store.py            # SessionStore — chat-scoped state w pamięci
│   ├── session_context.py          # Shared session bridge (auth state, flow fields)
│   ├── repositories.py             # Persystencja JSON (authorized_users, history)
│   ├── security.py                 # Rate limiting, walidacja URL, bezpieczeństwo
│   ├── cleanup.py                  # Czyszczenie plików i monitoring dysku
│   ├── transcription.py            # Transkrypcja (Groq) i podsumowania (Claude)
│   ├── downloader.py               # Pobieranie mediów z platform (yt-dlp)
│   ├── spotify.py                  # Rozwiązywanie Spotify podcastów (iTunes/YouTube)
│   ├── mtproto.py                  # Upload dużych plików przez MTProto (Pyrogram)
│   ├── cli.py                      # Interfejs wiersza poleceń
│   ├── telegram_commands.py        # Cienki wrapper kompatybilności — deleguje do handler layer
│   ├── telegram_callbacks.py       # Router callbacków — wrappery kompatybilności
│   ├── handlers/                   # Wydzielone flow handlery (bez cross-importów do routerów)
│   │   ├── command_access.py       # Auth/admin/info: /start, PIN, /logout, /help, /status
│   │   ├── inbound_media.py        # Intake URL-i, upload audio/video, playlist entry
│   │   ├── download_callbacks.py   # Download flow, progress, playlist, Instagram, Spotify
│   │   ├── transcription_callbacks.py # Transkrypcja, napisy, podsumowania
│   │   ├── callback_parsing.py     # Parsery callback payload (download, summary)
│   │   └── common_ui.py           # Centralny hub UI: klawiatury, Markdown, formatowanie
│   └── services/                   # Logika biznesowa niezależna od Telegrama
│       ├── auth_service.py         # PIN, login/logout, security state reset
│       ├── download_service.py     # Planowanie i wykonywanie pobrań
│       ├── playlist_service.py     # Obsługa playlist (budowanie, pobieranie itemów)
│       ├── spotify_service.py      # Resolving odcinków Spotify (iTunes/YouTube)
│       └── transcription_service.py # Artefakty transkrypcji i podsumowań
├── setup_config.py                 # Narzędzie konfiguracyjne
├── tests/                          # Testy (~460 testów)
│   ├── conftest.py                 # Współdzielone fixtures
│   ├── test_security.py            # Testy bezpieczeństwa
│   ├── test_security_unit.py       # Testy PIN, blokowania, security reset
│   ├── test_telegram_commands.py   # Testy komend, powiadomień admina
│   ├── test_telegram_callbacks.py  # Testy callbacków, pobierania
│   ├── test_auth_service.py        # Testy auth service (PIN, logout)
│   ├── test_runtime.py             # Testy runtime auth helperów
│   ├── test_session_store.py       # Testy SessionStore i session cleanup
│   ├── test_repositories.py        # Testy persystencji JSON
│   ├── test_spotify.py             # Testy Spotify podcastów
│   ├── test_downloader.py          # Testy downloadera, walidacji czasu
│   ├── test_download_history.py    # Testy historii pobrań
│   ├── test_cli.py                 # Testy CLI
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

## Rozwiązywanie problemów

**Bot nie odpowiada:**
- Sprawdź czy token jest poprawny
- Sprawdź połączenie internetowe
- Sprawdź logi w konsoli

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
