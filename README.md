# YouTube Downloader Telegram Bot 🎬

Bot Telegram do pobierania filmów z YouTube z funkcjami transkrypcji i podsumowań przy użyciu AI.

## 🚀 Funkcje

### Podstawowe
- 📥 Pobieranie filmów YouTube w różnych formatach wideo
- 🎵 Ekstrakcja ścieżek audio (MP3, M4A, FLAC, WAV, Opus)
- 📝 Automatyczna transkrypcja audio (Groq API - Whisper)
- 📋 Generowanie podsumowań transkrypcji (Claude API)
- 🔒 Ochrona dostępu kodem PIN
- 💻 Interfejs konsolowy i bot Telegram

### Bezpieczeństwo (NOWE!)
- 🛡️ Rate limiting - max 10 requestów/minutę per użytkownik
- 📏 Limit rozmiaru plików - max 500MB
- 🔗 Walidacja URL - tylko HTTPS YouTube
- ⏱️ Timeout dla operacji - 30s z automatycznymi retry
- 🔐 Wsparcie dla zmiennych środowiskowych
- 🚫 Blokada po 3 nieudanych próbach PIN (15 minut)

## 📋 Wymagania

- Python 3.7+
- ffmpeg (zainstalowany w systemie)

## 🛠️ Instalacja

```bash
# Klonuj repozytorium
git clone https://github.com/yourusername/ytdown.git
cd ytdown

# Zainstaluj zależności
pip install yt-dlp mutagen python-telegram-bot requests

# Lub użyj requirements.txt (jeśli istnieje)
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

## ⚙️ Konfiguracja

### Opcja 1: Plik konfiguracyjny (łatwiejsze)

Utwórz plik `api_key.md` w głównym katalogu:

```
TELEGRAM_BOT_TOKEN=twój_token_bota
GROQ_API_KEY=twój_klucz_groq
CLAUDE_API_KEY=twój_klucz_claude
PIN_CODE=12345678
```

⚠️ **WAŻNE**: Plik `api_key.md` jest już w `.gitignore` - nie commituj go do repozytorium!

### Opcja 2: Zmienne środowiskowe (bezpieczniejsze)

**Linux/macOS/WSL:**
```bash
export TELEGRAM_BOT_TOKEN="twój_token"
export GROQ_API_KEY="twój_klucz"
export CLAUDE_API_KEY="twój_klucz"
export PIN_CODE="12345678"
```

**Windows (Command Prompt):**
```cmd
set TELEGRAM_BOT_TOKEN=twój_token
set GROQ_API_KEY=twój_klucz
set CLAUDE_API_KEY=twój_klucz
set PIN_CODE=12345678
```

**Windows (PowerShell):**
```powershell
$env:TELEGRAM_BOT_TOKEN="twój_token"
$env:GROQ_API_KEY="twój_klucz"
$env:CLAUDE_API_KEY="twój_klucz"
$env:PIN_CODE="12345678"
```

## 🚀 Uruchomienie

### Bot Telegram
```bash
python youtube_downloader_telegram.py
```

### Tryb CLI (interfejs tekstowy)
```bash
python youtube_downloader_telegram.py --cli --url https://youtube.com/watch?v=...
```

### Testy bezpieczeństwa
```bash
python test_security.py
```

## 📱 Używanie bota Telegram

1. Znajdź swojego bota na Telegramie
2. Wyślij `/start`
3. Wprowadź 8-cyfrowy kod PIN
4. Wyślij link do filmu YouTube
5. Wybierz format i jakość
6. Poczekaj na pobranie

## 🛡️ Bezpieczeństwo

- ✅ Klucze API w `.gitignore`
- ✅ Rate limiting (10 req/min)
- ✅ Limit plików (500MB)
- ✅ Tylko HTTPS YouTube
- ✅ Blokada po złym PIN
- ✅ Timeout połączeń

Szczegóły w pliku [SECURITY_NOTES.md](SECURITY_NOTES.md)

## 📁 Struktura projektu

```
ytdown/
├── youtube_downloader_telegram.py  # Główna aplikacja
├── test_security.py               # Testy bezpieczeństwa
├── api_key.md                     # Konfiguracja (w .gitignore)
├── .gitignore                     # Ignorowane pliki
├── README.md                      # Ten plik
├── SECURITY_NOTES.md              # Uwagi bezpieczeństwa
├── PRD.md                         # Specyfikacja produktu
├── TODO.md                        # Lista zadań
└── downloads/                     # Pobrane pliki (w .gitignore)
    └── [chat_id]/                 # Pliki per użytkownik
```

## 🤝 Wkład w projekt

1. Fork repozytorium
2. Stwórz branch (`git checkout -b feature/AmazingFeature`)
3. Commit zmiany (`git commit -m 'Add AmazingFeature'`)
4. Push do branch (`git push origin feature/AmazingFeature`)
5. Otwórz Pull Request

## ⚠️ Ograniczenia

- Max 25MB dla pojedynczej części transkrypcji
- Telegram limit: 50MB dla plików, 4096 znaków dla wiadomości
- Sesje użytkowników tylko w pamięci (tracone po restarcie)

## 🐛 Rozwiązywanie problemów

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

## 📄 Licencja

Ten projekt jest dostępny na licencji MIT.

## 🙏 Podziękowania

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - pobieranie z YouTube
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) - API Telegram
- [Groq](https://groq.com/) - transkrypcja audio
- [Anthropic Claude](https://www.anthropic.com/) - generowanie podsumowań
