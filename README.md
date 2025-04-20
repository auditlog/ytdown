# YouTube Downloader Telegram

Bot do pobierania filmów z YouTube z możliwością transkrypcji i tworzenia podsumowań treści.

## Funkcje

- Pobieranie filmów z YouTube w różnych formatach wideo
- Pobieranie tylko ścieżki dźwiękowej w formatach MP3, M4A, FLAC
- Automatyczna transkrypcja audio z wykorzystaniem API Groq (Whisper)
- Generowanie podsumowań transkrypcji z wykorzystaniem API Claude
- Zabezpieczenie dostępu kodem PIN
- Interfejs konsolowy i bot Telegram

## Wymagania

- Python 3.7+
- yt-dlp
- mutagen
- python-telegram-bot
- ffmpeg (zainstalowany w systemie)

## Instalacja

```bash
pip install yt-dlp mutagen python-telegram-bot requests
```

Upewnij się, że masz zainstalowany ffmpeg w systemie.

## Konfiguracja

Utwórz plik `api_key.md` w głównym katalogu z następującą zawartością:

TELEGRAM_BOT_TOKEN=twój_token_bota
GROQ_API_KEY=twój_klucz_api_groq
CLAUDE_API_KEY=twój_klucz_api_claude
PIN_CODE=12345678
