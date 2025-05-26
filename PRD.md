# Product Requirements Document (PRD)
## YouTube Downloader Telegram Bot

### 1. Executive Summary

YouTube Downloader Telegram Bot to aplikacja umożliwiająca pobieranie treści z YouTube poprzez interfejs Telegram. Bot oferuje zaawansowane funkcje transkrypcji audio i generowania podsumowań przy użyciu AI, zapewniając jednocześnie bezpieczny dostęp poprzez system autoryzacji PIN.

### 2. Problem Statement

Użytkownicy potrzebują prostego i bezpiecznego sposobu na:
- Pobieranie filmów i audio z YouTube
- Transkrybowanie treści audio
- Generowanie podsumowań długich nagrań
- Dostęp do tych funkcji przez popularny komunikator (Telegram)

### 3. Solution Overview

Bot Telegram integrujący:
- yt-dlp do pobierania mediów
- Groq API (Whisper) do transkrypcji
- Claude API do generowania podsumowań
- System autoryzacji PIN dla kontroli dostępu

### 4. Target Users

- **Primary Users**: Osoby prywatne potrzebujące pobierać i przetwarzać treści z YouTube
- **Secondary Users**: Mali przedsiębiorcy, content creatorzy, studenci

### 5. Core Features

#### 5.1 Pobieranie Mediów
- **Video Download**
  - Różne formaty i rozdzielczości
  - Automatyczny wybór najlepszej jakości
  - Wsparcie dla custom formatów
  
- **Audio Extraction**
  - Formaty: MP3, M4A, FLAC, WAV, Opus, Vorbis
  - Konfigurowalna jakość (bitrate)
  - Automatyczna konwersja

#### 5.2 Transkrypcja Audio
- Wykorzystanie Groq API (model Whisper Large v3)
- Automatyczne dzielenie dużych plików (>25MB)
- Inteligentne wykrywanie punktów ciszy dla optymalnego podziału
- Łączenie transkrypcji z wielu części

#### 5.3 Generowanie Podsumowań
- 4 typy podsumowań:
  1. Krótkie podsumowanie
  2. Szczegółowe podsumowanie
  3. Podsumowanie w punktach
  4. Podział zadań na osoby
- Wykorzystanie Claude API (model Haiku)

#### 5.4 Bezpieczeństwo
- System autoryzacji 8-cyfrowym kodem PIN
- Blokada po 3 nieudanych próbach (15 minut)
- Automatyczne usuwanie wiadomości z PIN
- Sesje użytkowników w pamięci

#### 5.5 Interfejs Użytkownika
- Inline keyboard dla łatwej nawigacji
- Informacje o postępie pobierania
- Obsługa błędów z przyjaznymi komunikatami
- Wsparcie dla markdown w wiadomościach

### 6. Technical Architecture

#### 6.1 Technology Stack
- **Language**: Python 3.7+
- **Bot Framework**: python-telegram-bot
- **Media Processing**: yt-dlp, ffmpeg
- **Audio Analysis**: mutagen
- **AI APIs**: Groq (transkrypcja), Claude (podsumowania)

#### 6.2 File Structure
```
ytdown/
├── youtube_downloader_telegram.py  # Main application
├── api_key.md                      # Configuration file
├── README.md                        # Documentation
├── downloads/                       # Downloaded files directory
│   └── [chat_id]/                  # Per-chat subdirectories
└── archive/                         # Backup files
```

#### 6.3 Configuration
- Prosty plik tekstowy (api_key.md) z kluczami API
- Format: KLUCZ=WARTOŚĆ
- Wymagane klucze:
  - TELEGRAM_BOT_TOKEN
  - GROQ_API_KEY
  - CLAUDE_API_KEY
  - PIN_CODE

### 7. User Flow

1. **Start**: Użytkownik wysyła /start
2. **Authorization**: Podaje 8-cyfrowy PIN
3. **URL Input**: Wysyła link YouTube
4. **Format Selection**: Wybiera format przez inline keyboard
5. **Processing**: Bot pobiera/przetwarza plik
6. **Delivery**: Otrzymuje plik/transkrypcję/podsumowanie

### 8. API Integrations

#### 8.1 Telegram Bot API
- Webhook/polling dla odbierania wiadomości
- Inline keyboards dla interakcji
- File upload dla dostarczania mediów

#### 8.2 Groq API
- Endpoint: /openai/v1/audio/transcriptions
- Model: whisper-large-v3
- Format: multipart/form-data

#### 8.3 Claude API
- Endpoint: /v1/messages
- Model: claude-3-haiku-20240307
- Max tokens: 4096

### 9. Security & Privacy

- PIN przechowywany w plain text (wymaga poprawy)
- Brak szyfrowania kluczy API
- Pliki użytkowników segregowane po chat_id
- Brak automatycznego czyszczenia starych plików

### 10. Performance Requirements

- Obsługa plików do ~25MB bez dzielenia
- Timeout dla długich operacji
- Asynchroniczne przetwarzanie (python-telegram-bot)

### 11. Limitations

- Brak persystencji autoryzacji (restart = utrata sesji)
- Synchroniczne pobieranie plików
- Brak bazy danych
- Limit 4096 znaków dla wiadomości Telegram
- Brak wsparcia dla playlist

### 12. Future Enhancements

- Implementacja bazy danych (SQLite/PostgreSQL)
- Szyfrowanie wrażliwych danych
- Queue system dla długich zadań
- Web dashboard
- Wsparcie dla innych platform (nie tylko YouTube)
- Automatyczne czyszczenie starych plików
- Rate limiting per użytkownik
- Webhook mode zamiast polling

### 13. Success Metrics

- Czas odpowiedzi < 2s dla podstawowych operacji
- Sukces rate pobierania > 95%
- Dokładność transkrypcji > 90%
- Zero wycieków danych użytkowników

### 14. Dependencies

- yt-dlp (regularnie aktualizowane)
- python-telegram-bot
- mutagen
- ffmpeg (system dependency)
- requests
- curses (dla CLI mode)

### 15. Deployment

- Pojedynczy plik Python
- Minimalne wymagania systemowe
- Kompatybilny z Linux/Windows/macOS
- Może działać na VPS, Raspberry Pi, lub lokalnie

### 16. Language Policy

- **User Communication**: Polish language for all bot interactions with users
- **Code & Documentation**: English language for:
  - All code comments and docstrings
  - Configuration files
  - Technical documentation (README, PRD, TODO)
  - Variable names and function names
  - Git commits and pull requests
- **Rationale**: Maintains international code standards while providing localized user experience