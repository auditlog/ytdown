# YouTube Downloader Telegram Bot

Bot Telegram do pobierania filmów z YouTube z funkcjami transkrypcji i podsumowań przy użyciu AI.

## Funkcje

### Podstawowe
- Pobieranie filmów YouTube w różnych formatach wideo (1080p, 720p, 480p, 360p)
- Ekstrakcja ścieżek audio (MP3, M4A, FLAC, WAV, Opus)
- Automatyczna transkrypcja audio (Groq API - Whisper Large v3)
- **Napisy YouTube jako źródło transkrypcji** — natychmiastowe pobieranie napisów (manualnych lub automatycznych) bez zużycia tokenów AI
- Generowanie podsumowań transkrypcji (Claude API - Haiku 4.5)
- **Transkrypcja przesłanych plików audio** — wiadomości głosowe, pliki audio i dokumenty audio (np. notatki głosowe z WhatsApp)
- Ochrona dostępu kodem PIN
- Interfejs wiersza poleceń (CLI) z pełnym wsparciem dla wyboru formatu, jakości i audio
- Bot Telegram z interaktywnym menu

### Bezpieczeństwo
- Rate limiting - max 10 requestów/minutę per użytkownik
- Limit rozmiaru plików - max 1GB
- Walidacja URL - tylko HTTPS YouTube (youtube.com, youtu.be, music.youtube.com)
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
```

**UWAGA**: Plik `api_key.md` jest ignorowany przez git - nie commituj go do repozytorium!

### Opcja 3: Zmienne środowiskowe (najbezpieczniejsze)

**Linux/macOS/WSL:**
```bash
export TELEGRAM_BOT_TOKEN="twój_token"
export GROQ_API_KEY="twój_klucz"
export CLAUDE_API_KEY="twój_klucz"
export PIN_CODE="12345678"
export ADMIN_CHAT_ID="twój_telegram_user_id"
```

**Windows (PowerShell):**
```powershell
$env:TELEGRAM_BOT_TOKEN="twój_token"
$env:GROQ_API_KEY="twój_klucz"
$env:CLAUDE_API_KEY="twój_klucz"
$env:PIN_CODE="12345678"
$env:ADMIN_CHAT_ID="twój_telegram_user_id"
```

### Jak uzyskać ADMIN_CHAT_ID?

`ADMIN_CHAT_ID` to Twój numeryczny identyfikator użytkownika Telegram. Bot wysyła na ten ID powiadomienia o nieudanych próbach logowania i blokadach. Aby go poznać:

1. Napisz do bota [@userinfobot](https://t.me/userinfobot) na Telegramie
2. Bot odpowie Twoim ID (np. `123456789`)
3. Wpisz ten numer jako `ADMIN_CHAT_ID` w konfiguracji

Parametr jest opcjonalny — bez niego bot działa normalnie, ale nie wysyła powiadomień bezpieczeństwa.

### Cookies YouTube (opcjonalne, przy blokadzie anty-botowej)

Jeśli YouTube blokuje pobieranie komunikatem "Sign in to confirm you're not a bot", potrzebny jest plik `cookies.txt`:

1. Zainstaluj rozszerzenie **"Get cookies.txt LOCALLY"** w przeglądarce (Chrome/Firefox)
2. Wejdź na youtube.com (zalogowany na konto Google)
3. Wyeksportuj cookies do pliku `cookies.txt`
4. Umieść plik w głównym katalogu projektu (`ytdown/cookies.txt`)

**UWAGA**: Plik `cookies.txt` zawiera dane sesji YouTube — nie udostępniaj go i nie commituj do repozytorium! Jest ignorowany przez git.

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

### Pobieranie z YouTube
1. Znajdź swojego bota na Telegramie
2. Wyślij `/start`
3. Wprowadź kod PIN
4. Wyślij link do filmu YouTube
5. Wybierz format i jakość
6. Opcjonalnie: wybierz transkrypcję lub streszczenie
   - Jeśli film ma napisy YouTube — możesz wybrać gotowe napisy (natychmiastowo, 0 tokenów) lub transkrypcję AI (minuty, tokeny Groq/Claude)

### Transkrypcja plików audio
1. Wyślij wiadomość głosową, plik audio lub dokument audio (np. notatkę głosową z WhatsApp)
2. Wybierz opcję: "Transkrypcja" lub "Transkrypcja + Podsumowanie"
3. Obsługiwane formaty: OGG, OPUS, MP3, M4A, WAV, FLAC, WebM
4. Limit rozmiaru: 20 MB (ograniczenie Telegram Bot API)

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
│   ├── config.py                   # Konfiguracja i zarządzanie użytkownikami
│   ├── security.py                 # Rate limiting, walidacja URL, bezpieczeństwo
│   ├── cleanup.py                  # Czyszczenie plików i monitoring dysku
│   ├── transcription.py            # Transkrypcja (Groq) i podsumowania (Claude)
│   ├── downloader.py               # Pobieranie z YouTube (yt-dlp)
│   ├── cli.py                      # Interfejs wiersza poleceń
│   ├── telegram_commands.py        # Handlery komend Telegram (/start, /help, etc.)
│   └── telegram_callbacks.py       # Handlery callbacków (przyciski, pobieranie)
├── setup_config.py                 # Narzędzie konfiguracyjne
├── tests/                          # Testy (~274 testów)
│   ├── conftest.py                 # Współdzielone fixtures
│   ├── test_security.py            # Testy bezpieczeństwa
│   ├── test_security_unit.py       # Testy PIN, blokowania, logowania
│   ├── test_telegram_commands.py   # Testy komend, powiadomień admina
│   ├── test_telegram_callbacks.py  # Testy callbacków, pobierania
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
- Tylko HTTPS YouTube
- Blokada po złym PIN
- Autoryzacja zapisywana w JSON

## Ograniczenia

- Max 20MB dla pojedynczej części transkrypcji (większe pliki są dzielone automatycznie)
- Max 20MB dla przesyłanych plików audio (limit Telegram Bot API dla pobierania plików przez bota)
- Telegram limit: 50MB dla plików, 4096 znaków dla wiadomości
- Korekta AI transkrypcji: do ~4.5h materiału audio (powyżej automatycznie pomijana)
- Podsumowanie AI: do ~14h materiału audio (powyżej automatycznie pomijane)
- Sama transkrypcja (Whisper) i napisy YouTube działają bez limitu długości

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

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - pobieranie z YouTube
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) - API Telegram
- [Groq](https://groq.com/) - transkrypcja audio (Whisper)
- [Anthropic Claude](https://www.anthropic.com/) - generowanie podsumowań (Haiku 4.5)

## Language Policy

- **Bot Interface**: Polish - all user interactions and messages
- **Development**: English - code, comments, documentation
