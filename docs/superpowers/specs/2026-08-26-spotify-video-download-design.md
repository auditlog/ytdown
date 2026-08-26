# Pobieranie wideo ze Spotify — projekt

Data: 2026-08-26
Status: zatwierdzony do implementacji
Gałąź: `develop`

## 1. Cel

Rozszerzyć bota o pobieranie odcinków podcastów wideo bezpośrednio z CDN Spotify,
jako **opcję obok** istniejącego przepływu audio. Dotychczasowa ścieżka
(dopasowanie w iTunes/YouTube) pozostaje nienaruszona.

Odcinek referencyjny użyty w badaniu:
`https://open.spotify.com/episode/25NlRLSIHjtfU47zm4FGm4`
(„55. Instagramizacja startupów", podcast „Design i Biznes", 40,1 min).

## 2. Stan obecny

`bot/spotify.py` rozwiązuje link do odcinka przez metadane ze Spotify Web API,
a następnie szuka odpowiednika w iTunes (bezpośredni MP3) lub na YouTube (yt-dlp).
Ze Spotify nie jest pobierany żaden bajt. Wymaga `SPOTIFY_CLIENT_ID` i
`SPOTIFY_CLIENT_SECRET`. `PlatformConfig` dla Spotify ma `is_podcast=True`, więc
`build_main_keyboard()` pokazuje wyłącznie przyciski audio i transkrypcji.

## 3. Ustalenia z badania wykonalności

Wszystkie poniższe punkty zostały zweryfikowane empirycznie 2026-08-26.

### 3.1 Brak DRM na wideo

Trzy niezależne potwierdzenia dla odcinka referencyjnego:

| Sprawdzenie | Wynik |
|---|---|
| `video[0].requiresDRM` w danych odcinka | `false` |
| `contents[0].encryption_infos` w manifeście | `[]` (pusta lista) |
| Boxy `pssh` / `cenc` / `sinf` w pobranych segmentach | brak |
| `ffprobe` na sklejonych segmentach | `h264 1280x720` + `aac 48 kHz stereo`, czyta bez deszyfrowania |

Pobieranie tych plików nie wymaga obchodzenia zabezpieczeń technicznych.

**Uwaga odwrotna niż intuicyjna:** główne audio odcinka (`defaultAudioFileObject`)
ma format `MP4_128_CBCS`, czyli **jest** szyfrowane. Ścieżka audio wewnątrz
manifestu wideo (profil AAC) szyfrowana **nie jest**.

### 3.2 Łańcuch dostępu

1. **Token** — `GET https://open.spotify.com/embed/episode/{id}` z ciasteczkiem
   `sp_dc` zwraca HTML zawierający `__NEXT_DATA__` z polem `"accessToken"`
   oraz `"isAnonymous": false`.
   Endpoint `/get_access_token` zwraca **403** (wygaszony przez Spotify).
   Endpoint `/api/token` zwraca **400** z komunikatem o niedozwolonym użyciu.
2. **manifestId** — z tego samego HTML:
   `defaultAudioFileObject.video[0].manifestId`.
3. **Manifest** — `GET https://spclient.wg.spotify.com/manifests/v6/json/sources/{manifestId}/options/supports_drm`
   z nagłówkiem `Authorization: Bearer {token}`.
   **Wersje v7 i v8 zwracają 404. Działa wyłącznie v6.**
4. **Segmenty** — `base_urls` + `initialization_template` / `segment_template`
   z manifestu. Placeholdery: `{{profile_id}}`, `{{file_type}}`,
   `{{segment_timestamp}}`.
5. **Mux** — `ffmpeg -c copy` łączy ścieżki bez rekompresji.

### 3.3 Kształt manifestu

- `segment_length`: 4 sekundy. `segment_timestamp` wyrażany **w sekundach**
  (0, 4, 8, …), nie w milisekundach.
- Dla odcinka 2406 s daje to ~602 segmenty na ścieżkę, ~1204 żądania łącznie.
- `base_urls` zawiera **dwa** CDN-y: `video-fa.scdn.co` i `video-cf.spotifycdn.com`.
- URL-e są podpisane parametrami `token` i `fauth` z czasem wygaśnięcia —
  manifest trzeba pobierać świeży przed każdym pobraniem.
- `subtitle_language_codes` wystawia gotowe napisy WebVTT (tu: `pl-pl`, 58 KB,
  poprawna polska transkrypcja z timestampami).

### 3.4 Dostępne profile

| id | Rozdzielczość | Kodek | max_bitrate |
|---|---|---|---|
| 0 | 1920x1080 | avc1 (H.264) | 4 810 372 |
| 1 | 1280x720 | avc1 (H.264) | 2 605 250 |
| 2 | 854x480 | avc1 (H.264) | 1 419 290 |
| 3 | 568x320 | avc1 (H.264) | 797 392 |
| 4 | 426x240 | avc1 (H.264) | 479 580 |
| 5 | 320x180 | avc1 (H.264) | 279 596 |
| 15 | — | AAC (mp4a.40.2) | 100 114 |
| 16–19 | 320p–1080p | VP9 | — |
| 20 | — | Opus | 112 126 |

Używamy **wyłącznie H.264 i AAC**. Telegram odtwarza H.264 natywnie; VP9
w kontenerze MP4 potrafi się nie wyświetlić.

## 4. Zakres

### W zakresie

- Pobieranie wideo H.264 w czterech jakościach: 1080p, 720p, 480p, 320p.
- Pobieranie audio AAC (M4A) bezpośrednio ze Spotify dla odcinków wideo.
- Wykorzystanie napisów WebVTT jako źródła transkrypcji, z odwrotem do Groq.
- Osobny plik ciasteczek dla Spotify.
- Klawiatura budowana z realnie dostępnych źródeł.

### Poza zakresem

VP9 i 4K; wznawianie przerwanego pobierania między sesjami; playlisty i całe
podcasty; spritemapy i seekpanele; linki `/track/` i `/album/` (muzyka na
Spotify **jest** chroniona DRM i pozostaje poza zasięgiem).

## 5. Architektura

| Plik | Rola |
|---|---|
| `bot/spotify_video.py` (nowy) | warstwa niska: ciasteczko, token, manifest, URL-e, segmenty, mux, napisy |
| `bot/services/spotify_video_service.py` (nowy) | warstwa aplikacyjna: orkiestracja dla Telegrama, komunikaty błędów |
| `bot/handlers/spotify_callbacks.py` | `+ download_spotify_video()` |
| `bot/handlers/common_ui.py` | `+ build_spotify_episode_keyboard()` |
| `bot/handlers/callback_parsing.py` | `+ parse_spotify_video_callback()` |
| `bot/handlers/inbound_media.py` | rozpoznanie wideo w `extracted_process_spotify_episode()` |
| `bot/telegram_callbacks.py` | gałąź `spv_*` przed blokiem `dl_` |
| `bot/config.py` | `+ SPOTIFY_COOKIES_FILE` |

Podział lustrzany wobec istniejącej pary `bot/spotify.py` +
`bot/services/spotify_service.py`. Nie wprowadzamy nowej konwencji.

### 5.1 API modułu niskiego poziomu

```python
load_spotify_cookie(path) -> str | None
fetch_embed_data(episode_id, sp_dc) -> EmbedData
fetch_video_manifest(manifest_id, token) -> dict
list_profiles(manifest) -> list[Profile]
build_track_urls(manifest, profile_id) -> tuple[str, list[str]]
download_track(init_url, seg_urls, dest, *, progress_cb, cancellation) -> str
mux(video_path, audio_path, out_path) -> str
fetch_subtitles(manifest, lang, dest) -> str | None
```

`EmbedData` niesie: `access_token`, `manifest_id`, `title`, `show_name`,
`duration_ms`, `has_video`, `requires_drm`.

`Profile` niesie: `id`, `width`, `height`, `codec`, `max_bitrate`, `mime_type`.
`list_profiles()` zwraca wyłącznie profile H.264, posortowane malejąco po wysokości.

Wszystko poza `download_track`, `mux` i `fetch_subtitles` to funkcje czyste —
testowalne bez sieci, na zapisanym manifeście. IO jest odizolowane.

## 6. Przepływ użytkownika

Po otrzymaniu linku bot wykonuje **jedno** zapytanie do strony embed i buduje
klawiaturę z faktycznie dostępnych źródeł. Ścieżka wideo **nie wymaga**
`SPOTIFY_CLIENT_ID` ani `SPOTIFY_CLIENT_SECRET` — komplet metadanych jest
w odpowiedzi embeda.

| Warunek | Klawiatura |
|---|---|
| ma wideo + ciasteczko | `Video 1080p`, `720p`, `480p`, `320p`, `Audio (M4A) — Spotify`, transkrypcja; dodatkowo stara ścieżka audio, jeśli klucze API są ustawione |
| ma wideo, brak ciasteczka | komunikat z instrukcją eksportu ciasteczek; stara ścieżka audio tylko wtedy, gdy klucze API są ustawione |
| brak wideo | dokładnie dzisiejsze zachowanie, bez zmian |

Przyciski, które nie zadziałałyby przy obecnej konfiguracji, nie są pokazywane.

### 6.1 Szacowanie rozmiaru

Etykiety przycisków mapują się na profile z sekcji 3.4 następująco:
`1080p` = id 0 (1920x1080), `720p` = id 1 (1280x720), `480p` = id 2 (854x480),
`320p` = id 3 (568x320). Profile 240p i 180p nie są wystawiane — przy podcastach
wideo są nieużyteczne, a wydłużałyby klawiaturę. Ścieżka audio to zawsze profil id 15 (AAC).

Etykiety przycisków zawierają szacunek rozmiaru liczony jako
`0.45 * max_bitrate * czas_trwania / 8`.

Współczynnik 0,45 wyznaczono pomiarem: 720p dało 145 KB/s przy `max_bitrate`
odpowiadającym 325 KB/s. Szacunek 353 MB wobec zmierzonych 348 MB.

Dla odcinka referencyjnego: 1080p ~651 MB, 720p ~353 MB, 480p ~192 MB,
320p ~108 MB. Zmienna `max_bitrate` to wartość szczytowa, nie średnia —
stąd konieczność współczynnika.

### 6.2 Callbacki

Nowy prefiks `spv_`, obsługiwany **przed** blokiem `dl_`:
`spv_video_1080p`, `spv_video_720p`, `spv_video_480p`, `spv_video_320p`,
`spv_audio_m4a`.

Osobny prefiks zamiast dociążania `parse_download_callback()` — routing
`platform == "spotify"` w `telegram_callbacks.py` (linia ~165) przechwytuje dziś
wszystkie `dl_*`, a mieszanie ścieżki natywnej ze starą w jednym parserze
zaciemniłoby logikę.

Callbacki `transcribe`, `transcribe_summary` i `summary_option_*` **pozostają
bez zmian** — rozgałęzienie na napisy siedzi wewnątrz ich obsługi, dzięki czemu
UI transkrypcji wygląda identycznie jak dziś.

Dane sesji trzymane pod kluczem `spotify_video`, obok istniejącego
`spotify_resolved`.

## 7. Downloader segmentów

- `ThreadPoolExecutor`, 8 wątków.
- **Failover:** każdy segment próbuje `base_urls[0]`, przy niepowodzeniu
  `base_urls[1]`, następnie 3 podejścia z narastającym opóźnieniem.
- **Pamięć:** pobieranie partiami po 32 segmenty. W obrębie partii wyniki trafiają
  do słownika, po jej zakończeniu są dopisywane do pliku w kolejności.
  Zużycie ~20 MB zamiast 350 MB w RAM lub 1200 plików tymczasowych.
- **Postęp:** callback dławiony co 3 sekundy ze względu na limity Telegrama;
  format `Pobieranie wideo: 412/602`.
- **Anulowanie:** przez ten sam obiekt `cancellation`, którego używa
  `send_video_mtproto()`.
- **Mux:** `ffmpeg -c copy`, bez rekompresji.

### 7.1 Wysyłka

Nie wymaga nowego kodu. Gotowy plik trafia w istniejącą ścieżkę
`download_callbacks.py`: powyżej limitu Bot API przełączenie na MTProto
(`send_video_mtproto`), a przy przekroczeniu również limitu MTProto —
propozycja podziału na archiwum 7z.

## 8. Napisy jako źródło transkrypcji

Gdy manifest wystawia `subtitle_language_codes`, przyciski `Transkrypcja`
i `Transkrypcja + Podsumowanie` pobierają WebVTT i przepuszczają go przez
**istniejący** `parse_subtitle_file()` z `bot/downloader_subtitles.py`
(obsługuje WEBVTT, wycina timestampy i numery, deduplikuje powtórzone linie).

Wynik zapisuje `save_transcript_markdown()`, więc dalszy pipeline podsumowań
działa bez żadnych zmian. Brak napisów — cichy odwrót do Groq.

Zysk: wynik natychmiast zamiast kilku minut, zero kosztu API, brak ograniczenia
długości nagrania obowiązującego przy Groq.

## 9. Konfiguracja

Nowy klucz `SPOTIFY_COOKIES_FILE`, domyślnie `spotify_cookies.txt` w katalogu
repozytorium. **Osobny słoik**, nieużywany przez yt-dlp — `sp_dc` nie ma z yt-dlp
nic wspólnego, a mieszanie ich w `cookies.txt` byłoby mylące i ryzykowne.

`.gitignore` rozszerzono o wzorce `*cookies*.txt` i `*_cookies.txt`.
Wcześniejszy wpis dopasowywał wyłącznie dokładną nazwę `cookies.txt`, przez co
pliki takie jak `open.spotify.com_cookies.txt` groziły trafieniem do repozytorium
razem z żywymi poświadczeniami.

## 10. Obsługa błędów

Każdy przypadek dostaje konkretny komunikat po polsku. Zasada: żadnych cichych
niepowodzeń ani ogólnikowego „coś poszło nie tak".

| Sytuacja | Reakcja |
|---|---|
| brak lub niepoprawny plik ciasteczek | instrukcja eksportu ciasteczek |
| token odrzucony (401/403) | „Sesja Spotify wygasła — wyeksportuj ciasteczka ponownie" |
| manifest v6 zwraca 404 | jawny komunikat o zmianie API Spotify |
| `requires_drm == true` | **czysta odmowa** pobrania wideo + propozycja ścieżki audio |
| brak `ffmpeg` | komunikat o brakującej zależności systemowej |

Punkt z manifestem v6 jest najbardziej prawdopodobnym przyszłym miejscem awarii
(nieudokumentowany endpoint) i dlatego ma dawać czytelny sygnał diagnostyczny,
a nie wyglądać jak problem z siecią.

Gałąź `requires_drm` to egzekwowanie granicy projektowej w kodzie: obejścia DRM
nie budujemy, niezależnie od tego, kto jest właścicielem treści.

## 11. Testy

- `tests/test_spotify_video.py` — parsowanie słoika ciasteczek, wyciąganie danych
  z HTML embeda, budowa URL-i z szablonów, generowanie listy timestampów, wybór
  profilu, mapowanie profili na przyciski, szacowanie rozmiaru.
  Fixture: manifest z badania, z **wyczyszczonymi tokenami**.
- `tests/test_spotify_video_service.py` — orkiestracja z zamockowanym IO,
  w stylu istniejącego `tests/test_spotify_service.py`.
- Rozszerzenie `tests/test_telegram_callbacks.py` o routing `spv_*` oraz
  o przypadek wygasłej sesji.
- Rozszerzenie testów transkrypcji o gałąź „napisy zamiast Groq".

Żaden test nie może wykonywać realnych żądań sieciowych ani wymagać ciasteczek.

## 12. Ryzyka

| Ryzyko | Charakter | Reakcja |
|---|---|---|
| Regulamin Spotify | Wyciąganie tokenu ze strony embed jest niezgodne z Developer Terms; Spotify sygnalizuje to wprost w odpowiedzi 400 z `/api/token`. Realne ryzyko to zawieszenie konta, z którego pochodzi `sp_dc`. | Świadoma decyzja właściciela wdrożenia; udokumentowana tutaj. |
| Kruchość integracji | Endpoint v6, kształt HTML embeda i sposób osadzenia tokenu to nieudokumentowane wnętrzności. | Czytelna diagnostyka błędów (sekcja 10), testy na zapisanym manifeście. |
| Wygasanie URL-i | Podpisy segmentów mają czas życia. | Manifest pobierany świeżo przed każdym pobraniem; przy bardzo długich pobraniach możliwe odświeżenie w trakcie. |
| Rozmiar plików | 1080p dla 40 min to ~650 MB. | Szacunek w etykiecie przycisku; istniejąca ścieżka MTProto i podział 7z. |

## 13. Decyzje projektowe

**Własny downloader segmentów zamiast generowania MPD dla yt-dlp.** Manifest
podaje jawną listę plików — generyczny silnik DASH nic nie wnosi, a ręczne
składanie MPD z nieudokumentowanego JSON-a (escapowanie `&` w parametrach
`token` i `fauth`, mapowanie `$Time$`, wrażliwość na wersję yt-dlp) tworzy nową
klasę trudnych do zdiagnozowania błędów. Własny kod daje failover między dwoma
CDN-ami, dokładny postęp i czyste funkcje łatwe w testach, kosztem ~30 linii
logiki ponowień.

**Odrzucono `ffmpeg` z protokołem concat** — pobierałby 1204 segmenty szeregowo,
bez postępu i bez failoveru.

**Nowa ścieżka jako opcja, nie zamiennik.** Stary objazd przez iTunes/YouTube
pozostaje i obsługuje odcinki bez wideo oraz sytuacje bez ciasteczka.
