# TODO List - YouTube Downloader Telegram Bot

## 🔴 Krytyczne (Stabilność i Bezpieczeństwo)

### 1. Ochrona przed nadużyciami
- [ ] Implementacja rate limiting per użytkownik (max requests/minute)
- [ ] Dodanie maksymalnego rozmiaru pliku do pobrania (np. 500MB)
- [ ] Walidacja URL przed przetwarzaniem (regex + whitelist domen)
- [ ] Walidacja format_id przed przekazaniem do yt-dlp
- [ ] Timeout dla długich operacji pobierania

### 2. Zarządzanie przestrzenią dyskową
- [ ] Automatyczne czyszczenie starych plików (>24h)
- [ ] Monitorowanie wolnej przestrzeni dyskowej
- [ ] Limit przestrzeni per użytkownik
- [ ] Kompresja plików przed archiwizacją

### 3. Podstawowe zabezpieczenia
- [ ] Dodanie `api_key.md` do `.gitignore` (PRIORYTET!)
- [ ] Ustawienie uprawnień pliku: `chmod 600 api_key.md`
- [ ] Opcjonalne: wsparcie dla zmiennych środowiskowych
- [ ] Logowanie prób nieautoryzowanego dostępu

## 🟠 Wysoki priorytet (Architektura)

### 4. Modularyzacja kodu
- [ ] Podział na moduły:
  - `bot/handlers.py` - handlery Telegram
  - `core/downloader.py` - logika pobierania
  - `core/transcription.py` - transkrypcja audio
  - `core/summarization.py` - generowanie podsumowań
  - `utils/config.py` - zarządzanie konfiguracją
  - `utils/security.py` - autoryzacja i bezpieczeństwo
  - `models/user.py` - model użytkownika
- [ ] Utworzenie klas dla głównych komponentów
- [ ] Implementacja dependency injection

### 5. Persystencja danych (opcjonalne dla lokalnego użytku)
- [ ] Prosty plik JSON dla authorized_users (restart-safe)
- [ ] Historia pobrań w formacie CSV/JSON
- [ ] Cache transkrypcji w plikach
- [ ] Opcjonalnie: SQLite dla bardziej zaawansowanych potrzeb

### 6. Optymalizacja pobierania
- [ ] Równoległe pobieranie segmentów (dla dużych plików)
- [ ] Resume capability dla przerwanych pobrań
- [ ] Progress bar z ETA
- [ ] Opcjonalnie: asynchroniczne operacje dla wielu użytkowników

## 🟡 Średni priorytet (Funkcjonalność)

### 7. Ulepszone zarządzanie plikami
- [ ] Smart cache - przechowywanie popularnych plików
- [ ] Automatyczna konwersja do mniejszych formatów gdy przekroczony limit
- [ ] ZIP dla wielu plików
- [ ] Statystyki wykorzystania przestrzeni dyskowej

### 8. Nowe funkcje
- [ ] Wsparcie dla playlist YouTube
- [ ] Możliwość wyboru zakresu czasowego do pobrania
- [ ] Batch processing dla wielu URL
- [ ] Wsparcie dla innych platform (Vimeo, Dailymotion)
- [ ] Możliwość anulowania długich operacji

### 9. UI/UX
- [ ] Multi-language support
- [ ] Customizowane komunikaty błędów
- [ ] Tutorial dla nowych użytkowników
- [ ] Statystyki użycia dla użytkownika
- [ ] Możliwość zmiany ustawień (jakość, format domyślny)

## 🟢 Niski priorytet (Nice to have)

### 10. Zaawansowane funkcje
- [ ] Webhook mode zamiast polling (oszczędność zasobów)
- [ ] Prosty web interface dla lokalnego użytku
- [ ] Eksport statystyk użycia
- [ ] Backup/restore konfiguracji
- [ ] Wsparcie dla proxy

### 11. Dla chętnych (overkill dla lokalnego użytku)
- [ ] Docker deployment
- [ ] Pełna baza danych PostgreSQL
- [ ] Kubernetes ready
- [ ] Multi-tenant architecture
- [ ] OAuth2 authentication

## 📋 Rekomendowana kolejność implementacji (dla lokalnego użytku)

### Faza 1 (Natychmiastowe) - 1-2 dni
1. Dodanie `.gitignore` z `api_key.md`
2. Rate limiting (prosta implementacja)
3. Limit rozmiaru plików
4. Walidacja URL

### Faza 2 (Stabilność) - 1 tydzień
1. Automatyczne czyszczenie plików
2. Monitoring przestrzeni dyskowej
3. Timeout dla operacji
4. Lepsze logowanie błędów

### Faza 3 (Jakość kodu) - 2 tygodnie
1. Podział na 3-4 główne moduły
2. Prosty JSON storage dla sesji
3. Testy podstawowych funkcji
4. Dokumentacja kodu

### Faza 4 (Nowe funkcje) - 2 tygodnie
1. Wsparcie dla playlist
2. Resume downloads
3. Batch processing
4. Statystyki użycia

### Faza 5 (Nice to have) - opcjonalnie
1. Web interface
2. Webhook mode
3. Docker (jeśli planujesz deployment)
4. Dodatkowe platformy

## 🛠️ Praktyczne narzędzia dla lokalnego użytku

- **Rate Limiting**: Prosta implementacja w pamięci (dict + timestamp)
- **File Management**: `schedule` lub `APScheduler` dla auto-cleanup
- **Storage**: JSON files lub `tinydb` (lżejsze niż SQLite)
- **Testing**: `pytest` dla podstawowych testów
- **Monitoring**: Proste logi do pliku + alerty Telegram
- **Config**: Zmienne środowiskowe + `python-dotenv`

## 📝 Praktyczne uwagi

- **Najpierw stabilność**: Zacznij od rate limiting i czyszczenia plików
- **KISS principle**: Keep It Simple - nie przekombinuj dla lokalnego użytku
- **Backup**: Regularnie backupuj `api_key.md` (poza repo!)
- **Testuj na małych plikach**: Zanim pobierzesz 4K film
- **Monitoruj dysk**: Ustaw alerty gdy <10GB wolnego miejsca

## 🌐 Language Policy Note

- **User-facing content**: All bot messages and interactions in Polish
- **Technical content**: All code, comments, documentation, and configuration in English
- **Rationale**: International development standards with localized user experience