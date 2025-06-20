# TODO List - YouTube Downloader Telegram Bot

## 🔴 Krytyczne (Stabilność i Bezpieczeństwo)

### 1. Ochrona przed nadużyciami
- [x] Implementacja rate limiting per użytkownik (max requests/minute)
- [x] Dodanie maksymalnego rozmiaru pliku do pobrania (1GB)
- [x] Walidacja URL przed przetwarzaniem (regex + whitelist domen)
- [ ] Walidacja format_id przed przekazaniem do yt-dlp
- [x] Timeout dla długich operacji pobierania

### 2. Zarządzanie przestrzenią dyskową
- [x] Automatyczne czyszczenie starych plików (>24h)
- [x] Monitorowanie wolnej przestrzeni dyskowej
- [ ] Limit przestrzeni per użytkownik
- [ ] Kompresja plików przed archiwizacją

### 3. Podstawowe zabezpieczenia
- [x] Dodanie `api_key.md` do `.gitignore` (PRIORYTET!)
- [x] Ustawienie uprawnień pliku: `chmod 600 api_key.md` (ostrzeżenie)
- [x] Opcjonalne: wsparcie dla zmiennych środowiskowych
- [x] Walidacja konfiguracji i ostrzeżenia bezpieczeństwa
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
- [ ] Napisy/subtitles download
- [ ] Thumbnail extraction
- [ ] Audio normalization
- [ ] Video compression options

### 9. UI/UX
- [ ] Multi-language support
- [ ] Customizowane komunikaty błędów
- [ ] Tutorial dla nowych użytkowników
- [ ] Statystyki użycia dla użytkownika
- [ ] Możliwość zmiany ustawień (jakość, format domyślny)
- [x] Menu komend w Telegram (/start, /help, /status, /cleanup)
- [ ] User preferences storage (JSON)
- [ ] Download history tracking

## 🟢 Niski priorytet (Nice to have)

### 10. Zaawansowane funkcje
- [ ] Webhook mode zamiast polling (oszczędność zasobów)
- [ ] Prosty web interface dla lokalnego użytku
- [ ] Eksport statystyk użycia
- [ ] Backup/restore konfiguracji
- [ ] Wsparcie dla proxy
- [ ] Content categorization/tagging
- [ ] Search in downloaded files
- [ ] Favorites/collections system
- [ ] Duplicate detection
- [ ] Smart quality selection based on content

### 11. AI & Intelligence
- [ ] Auto-transcription language detection
- [ ] Content summarization improvements
- [ ] Auto-chaptering for long videos
- [ ] Sentiment analysis of content
- [ ] Language translation of transcripts
- [ ] Smart content recommendations
- [ ] Auto-tagging by content type

### 12. Monitoring & Enterprise
- [ ] Prometheus metrics export
- [ ] Grafana dashboards
- [ ] Error alerting system
- [ ] Usage trends analysis
- [ ] Performance monitoring
- [ ] LDAP/SSO integration
- [ ] Advanced permissions system
- [ ] Audit logs

### 13. Dla chętnych (overkill dla lokalnego użytku)
- [ ] Docker deployment
- [ ] Pełna baza danych PostgreSQL
- [ ] Kubernetes ready
- [ ] Multi-tenant architecture
- [ ] OAuth2 authentication
- [ ] React Native mobile app
- [ ] Offline viewing capabilities
- [ ] Push notifications
- [ ] CDN integration for large files

## 📋 Rekomendowana kolejność implementacji (dla lokalnego użytku)

### Faza 1 (Natychmiastowe) - COMPLETED ✅
1. ✅ Dodanie `.gitignore` z `api_key.md`
2. ✅ Rate limiting (prosta implementacja)
3. ✅ Limit rozmiaru plików
4. ✅ Walidacja URL

### Faza 2 (Stabilność) - COMPLETED ✅
1. ✅ Automatyczne czyszczenie plików
2. ✅ Monitoring przestrzeni dyskowej
3. ✅ Timeout dla operacji
4. ✅ Lepsze logowanie błędów
5. ✅ Menu komend w Telegram

### Faza 3 (Następne kroki) - 1-2 tygodnie
1. **Persystencja autoryzacji** - JSON storage dla authorized_users
2. **Historia pobrań** - tracking i statystyki
3. **User preferences** - ulubione formaty, jakości
4. **Format validation** - walidacja format_id
5. **Error handling improvements** - retry mechanism

### Faza 4 (Nowe funkcje) - 2-3 tygodnie
1. **Playlist support** - YouTube playlists (first 10-50 videos)
2. **Time ranges** - custom start/end times (--ss --to)
3. **More platforms** - Vimeo, Twitter/X integration
4. **Advanced downloads** - subtitles, thumbnails
5. **Resume capability** - przerwane pobierania

### Faza 5 (Zaawansowane) - 1-2 miesiące
1. **Web interface** - Flask/FastAPI dashboard
2. **User management** - multiple PINs, quotas
3. **AI improvements** - better summarization, language detection
4. **Content organization** - tagging, search, favorites

### Faza 6 (Enterprise) - 3-6 miesięcy
1. **Monitoring** - Prometheus, Grafana
2. **Mobile app** - React Native companion
3. **Multi-platform** - Instagram, TikTok support
4. **Intelligence** - content analysis, recommendations

## 🛠️ Praktyczne narzędzia i implementacje

### Już zaimplementowane ✅
- **Rate Limiting**: ✅ Dict + timestamp (10 req/min per user)
- **File Management**: ✅ Threading + auto-cleanup (24h)
- **Config**: ✅ JSON + environment variables + .env support
- **Security**: ✅ PIN validation, token format checking
- **Disk Management**: ✅ Multi-method disk usage detection

### Rekomendowane dla następnych faz
- **Storage**: JSON files lub `tinydb` (lżejsze niż SQLite)
- **Testing**: `pytest` dla podstawowych testów
- **Monitoring**: Proste logi do pliku + alerty Telegram
- **Queue System**: `asyncio.Queue` dla concurrent downloads
- **Progress Tracking**: Real-time progress via websockets
- **Content Analysis**: `spacy` lub `transformers` dla NLP
- **Web Framework**: `FastAPI` + `Jinja2` dla web interface
- **Mobile**: `React Native` + `Expo` dla cross-platform app

### Quick wins (1-2 dni każda)
- **JSON storage** for authorized users (`authorized_users.json`)
- **Download history** tracking (`download_history.json`)
- **Custom time ranges** (`--ss 00:30 --to 05:45` via yt-dlp)
- **Format validation** (sprawdzanie format_id przed download)
- **Basic playlist** support (first 10 videos)

## 📝 Praktyczne uwagi i status

### Status obecny (2024-12) ✅
- **Stabilność**: ✅ Rate limiting, cleanup, monitoring - DONE
- **Bezpieczeństwo**: ✅ PIN auth, config validation, file permissions - DONE  
- **Core features**: ✅ Download, transcription, summarization - WORKING
- **UI/UX**: ✅ Telegram commands menu, admin controls - DONE

### Następne priorytety
- **Persystencja**: JSON storage dla sesji (restart-safe)
- **Historia**: Tracking pobrań i statystyki użycia
- **Playlist**: YouTube playlist support (high demand)
- **Time ranges**: Custom start/end times dla długich materiałów
- **More platforms**: Vimeo, Twitter/X (expanding utility)

### Ogólne zasady
- **KISS principle**: Keep It Simple - nie przekombinuj dla lokalnego użytku
- **Backup**: Regularnie backupuj `api_key.md` (poza repo!)
- **Testuj na małych plikach**: Zanim pobierzesz 4K film
- **Monitoruj dysk**: ✅ Auto-monitoring już działa

## 🎯 Rekomendacja na najbliższy czas

**Week 1**: JSON storage dla authorized_users (restart-safe sessions)
**Week 2**: Download history tracking + basic statistics  
**Week 3**: YouTube playlist support (first 10-50 videos)
**Week 4**: Custom time ranges for video segments

## 🌐 Language Policy Note

- **User-facing content**: All bot messages and interactions in Polish
- **Technical content**: All code, comments, documentation, and configuration in English
- **Rationale**: International development standards with localized user experience