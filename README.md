# YouTube Downloader Telegram Bot

Bot Telegram do pobierania filmów z YouTube z funkcjami transkrypcji i podsumowań przy użyciu AI.

## Funkcje

### Podstawowe
- Pobieranie filmów YouTube w różnych formatach wideo (1080p, 720p, 480p, 360p)
- Ekstrakcja ścieżek audio (MP3, M4A, FLAC, WAV, Opus)
- Automatyczna transkrypcja audio (Groq API - Whisper Large v3)
- Generowanie podsumowań transkrypcji (Claude API - Haiku 4.5)
- Ochrona dostępu kodem PIN
- Interfejs konsolowy i bot Telegram

### Bezpieczeństwo
- Rate limiting - max 10 requestów/minutę per użytkownik
- Limit rozmiaru plików - max 1GB
- Walidacja URL - tylko HTTPS YouTube (youtube.com, youtu.be, music.youtube.com)
- Blokada po 3 nieudanych próbach PIN (15 minut)
- Wsparcie dla zmiennych środowiskowych
- JSON persistence dla autoryzowanych użytkowników

### Zarządzanie plikami
- Automatyczne czyszczenie plików starszych niż 24h
- Agresywne czyszczenie (6h) gdy mało miejsca na dysku (<5GB)
- Monitoring przestrzeni dyskowej
- Katalogi per użytkownik (chat_id)

## Wymagania

- Python 3.12+
- ffmpeg (zainstalowany w systemie)
- Poetry (package manager)

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
pip install yt-dlp mutagen python-telegram-bot requests python-dotenv
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
```

**UWAGA**: Plik `api_key.md` jest ignorowany przez git - nie commituj go do repozytorium!

### Opcja 3: Zmienne środowiskowe (najbezpieczniejsze)

**Linux/macOS/WSL:**
```bash
export TELEGRAM_BOT_TOKEN="twój_token"
export GROQ_API_KEY="twój_klucz"
export CLAUDE_API_KEY="twój_klucz"
export PIN_CODE="12345678"
```

**Windows (PowerShell):**
```powershell
$env:TELEGRAM_BOT_TOKEN="twój_token"
$env:GROQ_API_KEY="twój_klucz"
$env:CLAUDE_API_KEY="twój_klucz"
$env:PIN_CODE="12345678"
```

## Uruchomienie

### Bot Telegram
```bash
python main.py
```

### Tryb CLI (interfejs tekstowy)
```bash
python main.py --cli --url https://youtube.com/watch?v=...
```

### Testy
```bash
python -m pytest tests/
# lub pojedyncze testy:
python tests/test_security.py
python tests/test_security_standalone.py
python tests/test_json_persistence.py
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

## Używanie bota

1. Znajdź swojego bota na Telegramie
2. Wyślij `/start`
3. Wprowadź 8-cyfrowy kod PIN
4. Wyślij link do filmu YouTube
5. Wybierz format i jakość
6. Opcjonalnie: wybierz transkrypcję lub streszczenie

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
├── tests/                          # Testy
│   ├── test_security.py            # Testy bezpieczeństwa (wymaga importów)
│   ├── test_security_standalone.py # Testy standalone (bez zależności)
│   ├── test_json_persistence.py    # Testy persystencji JSON
│   └── test_json_simple.py         # Proste testy JSON
├── api_key.md                      # Konfiguracja (ignorowany przez git)
├── authorized_users.json           # Lista autoryzowanych użytkowników (ignorowany)
├── README.md                       # Ten plik
├── SECURITY_NOTES.md               # Uwagi bezpieczeństwa
└── downloads/                      # Pobrane pliki (ignorowany)
    └── [chat_id]/                  # Pliki per użytkownik
```

## Bezpieczeństwo

- Klucze API w gitignore
- Rate limiting (10 req/min)
- Limit plików (1GB)
- Tylko HTTPS YouTube
- Blokada po złym PIN
- Autoryzacja zapisywana w JSON

Szczegóły w pliku [SECURITY_NOTES.md](SECURITY_NOTES.md)

## Ograniczenia

- Max 20MB dla pojedynczej części transkrypcji (większe pliki są dzielone automatycznie)
- Telegram limit: 50MB dla plików, 4096 znaków dla wiadomości
- Max 16384 tokenów dla streszczeń Claude

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
