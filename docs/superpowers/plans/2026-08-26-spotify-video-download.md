# Pobieranie wideo ze Spotify — plan wdrożenia

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pobieranie odcinków podcastów wideo bezpośrednio z CDN Spotify, jako opcja obok istniejącego przepływu audio przez iTunes/YouTube.

**Architecture:** Nowy moduł niskopoziomowy `bot/spotify_video.py` (ciasteczko → token ze strony embed → manifest v6 → URL-e segmentów → równoległe pobieranie → mux) plus warstwa aplikacyjna `bot/services/spotify_video_service.py`. Wpięcie w istniejący routing callbacków przez nowy prefiks `spv_`. Gotowy plik trafia w istniejącą ścieżkę wysyłki (MTProto / podział 7z) bez żadnych zmian.

**Tech Stack:** Python 3.12, `requests`, `ffmpeg` (przez `subprocess`), `python-telegram-bot`, `pytest`. Bez nowych zależności.

**Spec:** `docs/superpowers/specs/2026-08-26-spotify-video-download-design.md`

## Global Constraints

- **Tylko H.264 + AAC.** VP9 i Opus są w manifeście, ale ignorujemy je — Telegram odtwarza H.264 natywnie, VP9 w MP4 potrafi się nie wyświetlić.
- **Endpoint manifestu to wyłącznie v6:** `https://spclient.wg.spotify.com/manifests/v6/json/sources/{manifest_id}/options/supports_drm`. Wersje v7 i v8 zwracają 404. Zweryfikowane 2026-08-26.
- **`segment_timestamp` liczony jest w sekundach**, nie milisekundach: 0, 4, 8, … Zweryfikowane na żywym CDN.
- **`requires_drm == True` → czysta odmowa pobrania wideo.** Nie budujemy dekryptora. Ta gałąź to egzekwowanie granicy projektowej w kodzie.
- **Język:** komunikaty dla użytkownika po polsku, kod, komentarze, nazwy i commity po angielsku.
- **Testy nie wykonują żądań sieciowych** i nie wymagają pliku ciasteczek. Całe IO jest mockowane.
- **Rozmiary plików liczymy w `1024 * 1024`**, zgodnie z resztą bota (`file_size_mb = os.path.getsize(path) / (1024 * 1024)`). Wartości w specyfikacji podano w MB dziesiętnych; ten sam szacunek w MiB to 621 / 336 / 183 / 103.
- **Gałąź `develop`.** Nigdy nie commituj na `main`. Nie dodawaj `Co-Authored-By` ani wzmianek o AI.
- Uruchamianie testów: `python -m pytest tests/ -q`

---

## Struktura plików

| Plik | Odpowiedzialność |
|---|---|
| `bot/spotify_video.py` (nowy) | ciasteczko, token, manifest, profile, URL-e, pobieranie segmentów, mux, napisy |
| `bot/services/spotify_video_service.py` (nowy) | orkiestracja dla Telegrama, komunikaty błędów po polsku |
| `tests/fixtures/spotify_embed.html` (nowy) | zapisana strona embed z wyczyszczonym tokenem |
| `tests/fixtures/spotify_manifest.json` (nowy) | zapisany manifest v6 z wyczyszczonymi tokenami |
| `tests/test_spotify_video.py` (nowy) | testy funkcji czystych i downloadera |
| `tests/test_spotify_video_service.py` (nowy) | testy orkiestracji |
| `bot/config.py` | `+ SPOTIFY_COOKIES_FILE` |
| `bot/session_store.py` | `+ SessionState.spotify_video` i aktualizacja sprawdzenia pustej sesji |
| `bot/handlers/callback_parsing.py` | `+ parse_spotify_video_callback()` |
| `bot/handlers/common_ui.py` | `+ build_spotify_episode_keyboard()` |
| `bot/handlers/inbound_media.py` | rozpoznanie wideo w `extracted_process_spotify_episode()` |
| `bot/handlers/spotify_callbacks.py` | `+ download_spotify_video()` |
| `bot/telegram_callbacks.py` | gałąź `spv_*` przed blokiem `dl_` |

---

### Task 1: Konfiguracja i wczytywanie ciasteczka

**Files:**
- Modify: `bot/config.py:44` (tuż po `COOKIES_FILE`)
- Create: `bot/spotify_video.py`
- Test: `tests/test_spotify_video.py`

**Interfaces:**
- Consumes: nic
- Produces: `SPOTIFY_COOKIES_FILE: str`, `load_spotify_cookie(cookies_file: str = SPOTIFY_COOKIES_FILE) -> str | None`, `SpotifyVideoError(Exception)`, `SpotifyVideoCancelled(Exception)`

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for bot.spotify_video."""

import pytest

from bot import spotify_video as sv


def test_load_spotify_cookie_reads_sp_dc(tmp_path):
    jar = tmp_path / "spotify_cookies.txt"
    jar.write_text(
        "# Netscape HTTP Cookie File\n"
        "\n"
        ".spotify.com\tTRUE\t/\tTRUE\t1819277774\tsp_key\taecaf1e4\n"
        ".spotify.com\tTRUE\t/\tTRUE\t1819277774\tsp_dc\tAQDKCX3CHO2D\n",
        encoding="utf-8",
    )
    assert sv.load_spotify_cookie(str(jar)) == "AQDKCX3CHO2D"


def test_load_spotify_cookie_returns_none_for_missing_file(tmp_path):
    assert sv.load_spotify_cookie(str(tmp_path / "absent.txt")) is None


def test_load_spotify_cookie_returns_none_without_sp_dc(tmp_path):
    jar = tmp_path / "spotify_cookies.txt"
    jar.write_text(
        ".spotify.com\tTRUE\t/\tTRUE\t1819277774\tsp_key\taecaf1e4\n",
        encoding="utf-8",
    )
    assert sv.load_spotify_cookie(str(jar)) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_spotify_video.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.spotify_video'`

- [ ] **Step 3: Add the config entry**

W `bot/config.py`, bezpośrednio pod definicją `COOKIES_FILE`:

```python
# Path to the Spotify cookie jar. Deliberately separate from COOKIES_FILE:
# sp_dc authenticates the Spotify web player and has nothing to do with yt-dlp,
# so mixing the two jars would be misleading and risky.
SPOTIFY_COOKIES_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "spotify_cookies.txt",
)
```

- [ ] **Step 4: Write the module skeleton and the cookie loader**

Utwórz `bot/spotify_video.py`:

```python
"""Direct Spotify video episode download.

Resolves a Spotify episode to its DRM-free segmented video manifest and
downloads the segments straight from Spotify's CDN.

Requires a Spotify session cookie (sp_dc) exported to SPOTIFY_COOKIES_FILE.
Does not require Spotify Web API credentials — the embed page carries every
piece of metadata this pipeline needs.

Episodes whose manifest reports requires_drm are refused outright; this module
never attempts to circumvent content protection.
"""

from __future__ import annotations

import logging
import os

from bot.config import SPOTIFY_COOKIES_FILE


class SpotifyVideoError(Exception):
    """Raised when the Spotify video pipeline cannot complete a step."""


class SpotifyVideoCancelled(Exception):
    """Raised when a download is aborted through a JobCancellation handle."""


def load_spotify_cookie(cookies_file: str = SPOTIFY_COOKIES_FILE) -> str | None:
    """Extract the sp_dc value from a Netscape-format cookie jar.

    Returns None when the file is absent, unreadable, or carries no sp_dc
    entry — callers turn that into a user-facing setup hint.
    """

    if not cookies_file or not os.path.exists(cookies_file):
        return None

    try:
        with open(cookies_file, encoding="utf-8") as file_obj:
            for line in file_obj:
                if line.startswith("#"):
                    continue
                fields = line.rstrip("\n").split("\t")
                # Netscape format: domain, flag, path, secure, expiry, name, value
                if len(fields) >= 7 and fields[5] == "sp_dc":
                    return fields[6] or None
    except OSError as exc:
        logging.error("Cannot read Spotify cookie jar %s: %s", cookies_file, exc)

    return None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_spotify_video.py -q`
Expected: PASS (3 testy)

- [ ] **Step 6: Commit**

```bash
git add bot/config.py bot/spotify_video.py tests/test_spotify_video.py
git commit -m "Add Spotify cookie jar config and loader"
```

---

### Task 2: Odczyt danych ze strony embed

**Files:**
- Modify: `bot/spotify_video.py`
- Create: `tests/fixtures/spotify_embed.html`
- Test: `tests/test_spotify_video.py`

**Interfaces:**
- Consumes: `SpotifyVideoError` z Task 1
- Produces: `EmbedData` (frozen dataclass: `access_token: str`, `manifest_id: str | None`, `title: str`, `show_name: str`, `duration_ms: int`, `has_video: bool`, `requires_drm: bool`), `parse_embed_html(html: str) -> EmbedData | None`, `fetch_embed_data(episode_id: str, sp_dc: str) -> EmbedData`

Ścieżki w `__NEXT_DATA__` potwierdzone empirycznie 2026-08-26:
`props.pageProps.state.settings.session.accessToken`,
`props.pageProps.state.data.entity.{title,subtitle,duration,hasVideo}`,
`props.pageProps.state.data.defaultAudioFileObject.video[0].{manifestId,requiresDRM}`.

- [ ] **Step 1: Create the fixture**

Utwórz `tests/fixtures/spotify_embed.html`:

```html
<!DOCTYPE html><html><head><title>Spotify Embed</title></head><body>
<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"state":{"data":{"entity":{"type":"episode","title":"Testowy odcinek","subtitle":"Testowy podcast","duration":32000,"hasVideo":true,"isPlayable":true},"defaultAudioFileObject":{"format":"MP4_128_CBCS","video":[{"manifestId":"cdc59c43c0e85cefb87ad38ee0439f11","requiresDRM":false}]}},"settings":{"rtl":false,"session":{"accessToken":"FAKE_ACCESS_TOKEN","isAnonymous":false}}}}}}</script>
</body></html>
```

- [ ] **Step 2: Write the failing test**

Dopisz do `tests/test_spotify_video.py`:

```python
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def _embed_html() -> str:
    return (FIXTURES / "spotify_embed.html").read_text(encoding="utf-8")


def test_parse_embed_html_extracts_all_fields():
    data = sv.parse_embed_html(_embed_html())
    assert data.access_token == "FAKE_ACCESS_TOKEN"
    assert data.manifest_id == "cdc59c43c0e85cefb87ad38ee0439f11"
    assert data.title == "Testowy odcinek"
    assert data.show_name == "Testowy podcast"
    assert data.duration_ms == 32000
    assert data.has_video is True
    assert data.requires_drm is False


def test_parse_embed_html_returns_none_without_next_data():
    assert sv.parse_embed_html("<html><body>nothing here</body></html>") is None


def test_parse_embed_html_handles_audio_only_episode():
    html = _embed_html().replace('"hasVideo":true', '"hasVideo":false').replace(
        '"video":[{"manifestId":"cdc59c43c0e85cefb87ad38ee0439f11","requiresDRM":false}]',
        '"video":[]',
    )
    data = sv.parse_embed_html(html)
    assert data.has_video is False
    assert data.manifest_id is None


def test_parse_embed_html_flags_drm_protected_video():
    html = _embed_html().replace('"requiresDRM":false', '"requiresDRM":true')
    assert sv.parse_embed_html(html).requires_drm is True
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_spotify_video.py -q`
Expected: FAIL — `AttributeError: module 'bot.spotify_video' has no attribute 'parse_embed_html'`

- [ ] **Step 4: Implement**

Dopisz do `bot/spotify_video.py` (rozszerz importy o `json`, `re`, `dataclass`, `requests`):

```python
import json
import re
from dataclasses import dataclass

import requests

EMBED_URL = "https://open.spotify.com/embed/episode/{episode_id}"

# Spotify serves different payloads to non-browser agents; a realistic UA keeps
# the embed page rendering the __NEXT_DATA__ blob we parse.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_NEXT_DATA_PATTERN = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)


@dataclass(frozen=True)
class EmbedData:
    """Episode metadata and web-player token read from the embed page."""

    access_token: str
    manifest_id: str | None
    title: str
    show_name: str
    duration_ms: int
    has_video: bool
    requires_drm: bool


def parse_embed_html(html: str) -> EmbedData | None:
    """Parse the embed page's __NEXT_DATA__ blob into an EmbedData.

    Returns None when the blob is absent or malformed, which in practice means
    Spotify changed the page shape — callers surface that explicitly rather
    than treating it as "episode not found".
    """

    match = _NEXT_DATA_PATTERN.search(html)
    if not match:
        return None

    try:
        payload = json.loads(match.group(1))
        state = payload["props"]["pageProps"]["state"]
        entity = state["data"]["entity"]
    except (ValueError, KeyError, TypeError):
        return None

    access_token = (
        state.get("settings", {}).get("session", {}).get("accessToken", "")
    )
    video_entries = (
        state["data"].get("defaultAudioFileObject", {}).get("video") or []
    )
    first_video = video_entries[0] if video_entries else {}

    return EmbedData(
        access_token=access_token,
        manifest_id=first_video.get("manifestId"),
        title=entity.get("title", ""),
        show_name=entity.get("subtitle", ""),
        duration_ms=int(entity.get("duration") or 0),
        has_video=bool(entity.get("hasVideo")),
        requires_drm=bool(first_video.get("requiresDRM")),
    )


def fetch_embed_data(episode_id: str, sp_dc: str) -> EmbedData:
    """Fetch and parse the embed page for one episode.

    The sp_dc cookie is what makes the returned token non-anonymous; without it
    Spotify still renders the page but the token cannot reach video manifests.
    """

    response = requests.get(
        EMBED_URL.format(episode_id=episode_id),
        headers={"User-Agent": BROWSER_USER_AGENT},
        cookies={"sp_dc": sp_dc},
        timeout=25,
    )
    response.raise_for_status()

    data = parse_embed_html(response.text)
    if data is None:
        raise SpotifyVideoError(
            "Spotify embed page did not contain the expected __NEXT_DATA__ blob"
        )
    return data
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_spotify_video.py -q`
Expected: PASS (7 testów)

- [ ] **Step 6: Commit**

```bash
git add bot/spotify_video.py tests/test_spotify_video.py tests/fixtures/spotify_embed.html
git commit -m "Read Spotify episode metadata and token from embed page"
```

---

### Task 3: Manifest, profile i szacowanie rozmiaru

**Files:**
- Modify: `bot/spotify_video.py`
- Create: `tests/fixtures/spotify_manifest.json`
- Test: `tests/test_spotify_video.py`

**Interfaces:**
- Consumes: `SpotifyVideoError`
- Produces: `Profile` (frozen dataclass: `id: int`, `width: int`, `height: int`, `codec: str`, `max_bitrate: int`, `mime_type: str`), `fetch_video_manifest(manifest_id: str, access_token: str) -> dict`, `list_profiles(manifest: dict) -> list[Profile]`, `find_audio_profile_id(manifest: dict) -> int`, `manifest_duration_ms(manifest: dict) -> int`, `estimate_size_mb(profile: Profile, duration_ms: int) -> float`, stała `BITRATE_TO_AVERAGE_RATIO = 0.45`

- [ ] **Step 1: Create the fixture**

Utwórz `tests/fixtures/spotify_manifest.json`. Szablony zachowują prawdziwy kształt (placeholdery i parametry query), a wartości tokenów są sztuczne. `end_time_millis` skrócono do 32 s, żeby testy operowały na ośmiu segmentach:

```json
{
  "contents": [
    {
      "encoding_id": "b3b3dce09fa711f1b83ccf1e1a200f9f",
      "segment_length": 4,
      "start_time_millis": 0,
      "end_time_millis": 32000,
      "profiles": [
        {"id": 15, "audio_bitrate": 96000, "audio_codec": "mp4a.40.2", "file_type": "mp4", "max_bitrate": 100114, "mime_type": "audio/mp4"},
        {"id": 20, "audio_bitrate": 96000, "audio_codec": "opus", "file_type": "mp4", "max_bitrate": 112126, "mime_type": "audio/mp4"},
        {"id": 2, "file_type": "mp4", "max_bitrate": 1419290, "mime_type": "video/mp4", "video_codec": "avc1.4d401f", "video_height": 480, "video_width": 854},
        {"id": 1, "file_type": "mp4", "max_bitrate": 2605250, "mime_type": "video/mp4", "video_codec": "avc1.4d4020", "video_height": 720, "video_width": 1280},
        {"id": 0, "file_type": "mp4", "max_bitrate": 4810372, "mime_type": "video/mp4", "video_codec": "avc1.4d402a", "video_height": 1080, "video_width": 1920},
        {"id": 17, "file_type": "mp4", "max_bitrate": 2678294, "mime_type": "video/mp4", "video_codec": "vp9", "video_height": 720, "video_width": 1280}
      ],
      "encryption_infos": []
    }
  ],
  "start_time_millis": 0,
  "end_time_millis": 32000,
  "initialization_template": "v1/origins/ORIGIN/sources/SRC/encodings/ENC/profiles/{{profile_id}}/inits/{{file_type}}?token=FAKE_TOKEN&fauth=FAKE_FAUTH",
  "segment_template": "v1/origins/ORIGIN/sources/SRC/encodings/ENC/profiles/{{profile_id}}/{{segment_timestamp}}.{{file_type}}?token=FAKE_TOKEN&fauth=FAKE_FAUTH",
  "subtitle_template": "v1.1/sources/SRC/{{language_code}}.webvtt?__token__=FAKE&fauth=FAKE_FAUTH",
  "base_urls": ["https://video-fa.scdn.co/segments/", "https://video-cf.spotifycdn.com/segments/"],
  "subtitle_base_urls": ["https://subtitles.spotifycdn.com/subtitles/"],
  "subtitle_language_codes": ["pl-pl"]
}
```

- [ ] **Step 2: Write the failing test**

```python
import json


def _manifest() -> dict:
    return json.loads((FIXTURES / "spotify_manifest.json").read_text(encoding="utf-8"))


def test_list_profiles_returns_only_h264_sorted_by_height():
    profiles = sv.list_profiles(_manifest())
    assert [p.height for p in profiles] == [1080, 720, 480]
    assert all(p.codec.startswith("avc1") for p in profiles)


def test_list_profiles_excludes_vp9_and_audio():
    ids = {p.id for p in sv.list_profiles(_manifest())}
    assert 17 not in ids, "VP9 must be excluded"
    assert 15 not in ids and 20 not in ids, "audio profiles must be excluded"


def test_find_audio_profile_id_prefers_aac():
    assert sv.find_audio_profile_id(_manifest()) == 15


def test_manifest_duration_ms():
    assert sv.manifest_duration_ms(_manifest()) == 32000


def test_estimate_size_mb_uses_measured_ratio():
    profile = next(p for p in sv.list_profiles(_manifest()) if p.height == 720)
    # 2605250 bps * 0.45 / 8 = 146545 B/s over 2406 s -> ~336 MiB
    assert sv.estimate_size_mb(profile, 2406000) == pytest.approx(336, abs=3)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_spotify_video.py -q`
Expected: FAIL — `AttributeError: module 'bot.spotify_video' has no attribute 'list_profiles'`

- [ ] **Step 4: Implement**

```python
MANIFEST_URL = (
    "https://spclient.wg.spotify.com/manifests/v6/json/sources/"
    "{manifest_id}/options/supports_drm"
)

# The manifest's max_bitrate is a peak, not an average. Measured against the
# live CDN on 2026-08-26: the 720p profile delivered 145 KB/s against a
# 325 KB/s peak, so real size lands near 45% of the naive max_bitrate figure.
BITRATE_TO_AVERAGE_RATIO = 0.45

# Telegram plays H.264 natively; VP9 inside MP4 often fails to render.
_H264_CODEC_PREFIX = "avc1"
_AAC_CODEC_PREFIX = "mp4a"


@dataclass(frozen=True)
class Profile:
    """One selectable video rendition from the manifest."""

    id: int
    width: int
    height: int
    codec: str
    max_bitrate: int
    mime_type: str


def _manifest_content(manifest: dict) -> dict:
    contents = manifest.get("contents") or []
    if not contents:
        raise SpotifyVideoError("Spotify manifest carries no contents entry")
    return contents[0]


def fetch_video_manifest(manifest_id: str, access_token: str) -> dict:
    """Fetch the segmented video manifest for one episode.

    Only API version v6 exists — v7 and v8 return 404 as of 2026-08-26.
    """

    response = requests.get(
        MANIFEST_URL.format(manifest_id=manifest_id),
        headers={
            "Authorization": f"Bearer {access_token}",
            "app-platform": "WebPlayer",
            "User-Agent": BROWSER_USER_AGENT,
            "Accept": "application/json",
        },
        timeout=25,
    )
    if response.status_code == 404:
        raise SpotifyVideoError(
            "Spotify manifest endpoint v6 returned 404 — the API shape changed"
        )
    response.raise_for_status()
    return response.json()


def list_profiles(manifest: dict) -> list[Profile]:
    """Return selectable H.264 video profiles, highest resolution first."""

    profiles = []
    for raw in _manifest_content(manifest).get("profiles", []):
        codec = raw.get("video_codec", "")
        if not codec.startswith(_H264_CODEC_PREFIX):
            continue
        profiles.append(
            Profile(
                id=int(raw["id"]),
                width=int(raw.get("video_width") or 0),
                height=int(raw.get("video_height") or 0),
                codec=codec,
                max_bitrate=int(raw.get("max_bitrate") or 0),
                mime_type=raw.get("mime_type", "video/mp4"),
            )
        )
    return sorted(profiles, key=lambda p: p.height, reverse=True)


def find_audio_profile_id(manifest: dict) -> int:
    """Return the AAC audio profile id from the video manifest.

    This track is DRM-free even though the episode's standalone audio file is
    MP4_128_CBCS (encrypted) — verified 2026-08-26.
    """

    for raw in _manifest_content(manifest).get("profiles", []):
        if raw.get("audio_codec", "").startswith(_AAC_CODEC_PREFIX):
            return int(raw["id"])
    raise SpotifyVideoError("Spotify manifest carries no AAC audio profile")


def manifest_duration_ms(manifest: dict) -> int:
    """Return the episode duration covered by the manifest, in milliseconds."""

    content = _manifest_content(manifest)
    return int(content.get("end_time_millis", 0)) - int(
        content.get("start_time_millis", 0)
    )


def estimate_size_mb(profile: Profile, duration_ms: int) -> float:
    """Estimate the download size in MiB for one profile.

    Reported in MiB to match how the rest of the bot computes file_size_mb.
    """

    average_bytes_per_second = profile.max_bitrate * BITRATE_TO_AVERAGE_RATIO / 8
    return average_bytes_per_second * (duration_ms / 1000) / (1024 * 1024)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_spotify_video.py -q`
Expected: PASS (12 testów)

- [ ] **Step 6: Commit**

```bash
git add bot/spotify_video.py tests/test_spotify_video.py tests/fixtures/spotify_manifest.json
git commit -m "Fetch Spotify video manifest and expose H.264 profiles"
```

---

### Task 4: Budowa URL-i segmentów

**Files:**
- Modify: `bot/spotify_video.py`
- Test: `tests/test_spotify_video.py`

**Interfaces:**
- Consumes: `_manifest_content`, `manifest_duration_ms`
- Produces: `build_track_urls(manifest: dict, profile_id: int) -> tuple[list[str], list[list[str]]]` — zwraca listę kandydatów URL dla init oraz listę list kandydatów dla kolejnych segmentów. Każda wewnętrzna lista to ten sam segment na kolejnych CDN-ach, w kolejności z `base_urls`.

- [ ] **Step 1: Write the failing test**

```python
def test_build_track_urls_generates_one_candidate_per_cdn():
    init_urls, segment_urls = sv.build_track_urls(_manifest(), profile_id=1)
    assert len(init_urls) == 2
    assert init_urls[0].startswith("https://video-fa.scdn.co/segments/")
    assert init_urls[1].startswith("https://video-cf.spotifycdn.com/segments/")


def test_build_track_urls_substitutes_profile_and_file_type():
    init_urls, _ = sv.build_track_urls(_manifest(), profile_id=1)
    assert "/profiles/1/inits/mp4?" in init_urls[0]
    assert "{{" not in init_urls[0]


def test_build_track_urls_counts_segments_from_duration():
    _, segment_urls = sv.build_track_urls(_manifest(), profile_id=1)
    # 32000 ms / 4 s per segment
    assert len(segment_urls) == 8


def test_build_track_urls_uses_second_based_timestamps():
    _, segment_urls = sv.build_track_urls(_manifest(), profile_id=1)
    assert "/profiles/1/0.mp4?" in segment_urls[0][0]
    assert "/profiles/1/4.mp4?" in segment_urls[1][0]
    assert "/profiles/1/28.mp4?" in segment_urls[7][0]


def test_build_track_urls_preserves_query_parameters():
    _, segment_urls = sv.build_track_urls(_manifest(), profile_id=1)
    assert "token=FAKE_TOKEN" in segment_urls[0][0]
    assert "fauth=FAKE_FAUTH" in segment_urls[0][0]


def test_build_track_urls_rounds_partial_final_segment_up():
    manifest = _manifest()
    manifest["contents"][0]["end_time_millis"] = 30000
    _, segment_urls = sv.build_track_urls(manifest, profile_id=1)
    # 30 s / 4 s = 7.5 -> 8 segments, the last one short
    assert len(segment_urls) == 8
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_spotify_video.py -q`
Expected: FAIL — `AttributeError: module 'bot.spotify_video' has no attribute 'build_track_urls'`

- [ ] **Step 3: Implement**

Dodaj `import math` do importów, następnie:

```python
def _expand_template(template: str, base_urls: list[str], **placeholders) -> list[str]:
    """Fill a manifest URL template and prefix it with every CDN base URL."""

    path = template
    for name, value in placeholders.items():
        path = path.replace("{{%s}}" % name, str(value))
    return [base + path for base in base_urls]


def build_track_urls(
    manifest: dict, profile_id: int
) -> tuple[list[str], list[list[str]]]:
    """Build init and segment URL candidates for one profile.

    Each returned segment is a list of equivalent URLs, one per CDN in
    base_urls, so the downloader can fail over without rebuilding anything.
    """

    content = _manifest_content(manifest)
    base_urls = manifest.get("base_urls") or []
    if not base_urls:
        raise SpotifyVideoError("Spotify manifest carries no base_urls")

    segment_length = int(content.get("segment_length") or 0)
    if segment_length <= 0:
        raise SpotifyVideoError("Spotify manifest carries no segment_length")

    file_type = "mp4"

    init_urls = _expand_template(
        manifest["initialization_template"],
        base_urls,
        profile_id=profile_id,
        file_type=file_type,
    )

    # segment_timestamp is whole seconds counted from the episode start
    # (0, 4, 8, ...), not milliseconds — verified against the live CDN.
    duration_seconds = manifest_duration_ms(manifest) / 1000
    segment_count = math.ceil(duration_seconds / segment_length)

    segment_urls = [
        _expand_template(
            manifest["segment_template"],
            base_urls,
            profile_id=profile_id,
            file_type=file_type,
            segment_timestamp=index * segment_length,
        )
        for index in range(segment_count)
    ]

    return init_urls, segment_urls
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_spotify_video.py -q`
Expected: PASS (18 testów)

- [ ] **Step 5: Commit**

```bash
git add bot/spotify_video.py tests/test_spotify_video.py
git commit -m "Build Spotify segment URLs with per-CDN failover candidates"
```

---

### Task 5: Pobieranie ścieżki segmentów

**Files:**
- Modify: `bot/spotify_video.py`
- Test: `tests/test_spotify_video.py`

**Interfaces:**
- Consumes: `SpotifyVideoError`, `SpotifyVideoCancelled`, `build_track_urls`
- Produces: `download_track(init_urls: list[str], segment_urls: list[list[str]], dest_path: str, *, progress_cb=None, cancellation=None, workers: int = 8, batch_size: int = 32) -> str`, `SEGMENT_WORKERS = 8`, `SEGMENT_BATCH_SIZE = 32`

`progress_cb` przyjmuje `(done: int, total: int)`. `cancellation` to `bot.jobs.JobCancellation` — sprawdzane jest `cancellation.event.is_set()`.

- [ ] **Step 1: Write the failing test**

```python
def test_download_track_writes_init_then_segments_in_order(tmp_path, monkeypatch):
    fetched = {
        "https://cdn/init": b"INIT",
        "https://cdn/0": b"AAA",
        "https://cdn/1": b"BBB",
        "https://cdn/2": b"CCC",
    }
    monkeypatch.setattr(sv, "_fetch_bytes", lambda url, timeout=30: fetched[url])

    dest = tmp_path / "video.mp4"
    sv.download_track(
        ["https://cdn/init"],
        [["https://cdn/0"], ["https://cdn/1"], ["https://cdn/2"]],
        str(dest),
        batch_size=2,
    )
    assert dest.read_bytes() == b"INITAAABBBCCC"


def test_download_track_falls_over_to_second_cdn(tmp_path, monkeypatch):
    def fake_fetch(url, timeout=30):
        if "primary" in url:
            raise sv.requests.RequestException("primary down")
        return b"OK"

    monkeypatch.setattr(sv, "_fetch_bytes", fake_fetch)
    dest = tmp_path / "video.mp4"
    sv.download_track(
        ["https://primary/init", "https://backup/init"],
        [["https://primary/0", "https://backup/0"]],
        str(dest),
    )
    assert dest.read_bytes() == b"OKOK"


def test_download_track_reports_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(sv, "_fetch_bytes", lambda url, timeout=30: b"X")
    seen = []
    sv.download_track(
        ["https://cdn/init"],
        [["https://cdn/%d" % i] for i in range(5)],
        str(tmp_path / "v.mp4"),
        progress_cb=lambda done, total: seen.append((done, total)),
        batch_size=2,
    )
    assert seen[-1] == (5, 5)


def test_download_track_raises_after_exhausting_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sv, "_fetch_bytes",
        lambda url, timeout=30: (_ for _ in ()).throw(sv.requests.RequestException("boom")),
    )
    monkeypatch.setattr(sv.time, "sleep", lambda seconds: None)
    with pytest.raises(sv.SpotifyVideoError):
        sv.download_track(
            ["https://cdn/init"], [["https://cdn/0"]], str(tmp_path / "v.mp4")
        )


def test_download_track_honours_cancellation(tmp_path, monkeypatch):
    monkeypatch.setattr(sv, "_fetch_bytes", lambda url, timeout=30: b"X")

    class _Cancelled:
        class event:
            @staticmethod
            def is_set():
                return True

    with pytest.raises(sv.SpotifyVideoCancelled):
        sv.download_track(
            ["https://cdn/init"],
            [["https://cdn/0"]],
            str(tmp_path / "v.mp4"),
            cancellation=_Cancelled(),
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_spotify_video.py -q`
Expected: FAIL — `AttributeError: module 'bot.spotify_video' has no attribute 'download_track'`

- [ ] **Step 3: Implement**

Dodaj `import time` oraz `from concurrent.futures import ThreadPoolExecutor, as_completed`, następnie:

```python
SEGMENT_WORKERS = 8
SEGMENT_BATCH_SIZE = 32
SEGMENT_ATTEMPTS = 3


def _fetch_bytes(url: str, timeout: int = 30) -> bytes:
    """Fetch one URL and return its body. Separated out so tests can stub it."""

    response = requests.get(
        url, headers={"User-Agent": BROWSER_USER_AGENT}, timeout=timeout
    )
    response.raise_for_status()
    return response.content


def _fetch_with_failover(url_candidates: list[str]) -> bytes:
    """Fetch one segment, trying every CDN before backing off and retrying."""

    last_error: Exception | None = None
    for attempt in range(SEGMENT_ATTEMPTS):
        for url in url_candidates:
            try:
                return _fetch_bytes(url)
            except requests.RequestException as exc:
                last_error = exc
        if attempt < SEGMENT_ATTEMPTS - 1:
            time.sleep(2 ** attempt)
    raise SpotifyVideoError(f"Segment download failed: {last_error}")


def _raise_if_cancelled(cancellation) -> None:
    if cancellation is not None and cancellation.event.is_set():
        raise SpotifyVideoCancelled("Download cancelled by user")


def download_track(
    init_urls: list[str],
    segment_urls: list[list[str]],
    dest_path: str,
    *,
    progress_cb=None,
    cancellation=None,
    workers: int = SEGMENT_WORKERS,
    batch_size: int = SEGMENT_BATCH_SIZE,
) -> str:
    """Download one media track (video or audio) into a single file.

    Segments are fetched concurrently but written in order. Work is done in
    batches so at most batch_size segments are held in memory at once —
    a full episode would otherwise need hundreds of megabytes of RAM or a
    thousand temporary files.
    """

    total = len(segment_urls)
    _raise_if_cancelled(cancellation)

    with open(dest_path, "wb") as out:
        out.write(_fetch_with_failover(init_urls))

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for start in range(0, total, batch_size):
                _raise_if_cancelled(cancellation)
                batch = segment_urls[start:start + batch_size]

                futures = {
                    pool.submit(_fetch_with_failover, candidates): offset
                    for offset, candidates in enumerate(batch)
                }
                chunks: dict[int, bytes] = {}
                for future in as_completed(futures):
                    chunks[futures[future]] = future.result()

                for offset in range(len(batch)):
                    out.write(chunks[offset])

                if progress_cb is not None:
                    progress_cb(min(start + batch_size, total), total)

    return dest_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_spotify_video.py -q`
Expected: PASS (23 testy)

- [ ] **Step 5: Commit**

```bash
git add bot/spotify_video.py tests/test_spotify_video.py
git commit -m "Add concurrent Spotify segment downloader with CDN failover"
```

---

### Task 6: Mux i pobieranie napisów

**Files:**
- Modify: `bot/spotify_video.py`
- Test: `tests/test_spotify_video.py`

**Interfaces:**
- Consumes: `SpotifyVideoError`, `_fetch_bytes`, `_expand_template`
- Produces: `mux(video_path: str, audio_path: str, out_path: str) -> str`, `subtitle_languages(manifest: dict) -> list[str]`, `fetch_subtitles(manifest: dict, language_code: str, dest_path: str) -> str | None`

- [ ] **Step 1: Write the failing test**

```python
def test_subtitle_languages_reads_manifest():
    assert sv.subtitle_languages(_manifest()) == ["pl-pl"]


def test_subtitle_languages_empty_when_absent():
    manifest = _manifest()
    del manifest["subtitle_language_codes"]
    assert sv.subtitle_languages(manifest) == []


def test_fetch_subtitles_writes_vtt(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sv, "_fetch_bytes",
        lambda url, timeout=30: b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nCzesc\n",
    )
    dest = tmp_path / "subs.vtt"
    result = sv.fetch_subtitles(_manifest(), "pl-pl", str(dest))
    assert result == str(dest)
    assert dest.read_text(encoding="utf-8").startswith("WEBVTT")


def test_fetch_subtitles_returns_none_for_unknown_language(tmp_path):
    assert sv.fetch_subtitles(_manifest(), "de-de", str(tmp_path / "s.vtt")) is None


def test_mux_invokes_ffmpeg_with_stream_copy(tmp_path, monkeypatch):
    calls = {}

    class _Result:
        returncode = 0
        stderr = b""

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        (tmp_path / "out.mp4").write_bytes(b"MUXED")
        return _Result()

    monkeypatch.setattr(sv.subprocess, "run", fake_run)
    out = sv.mux(str(tmp_path / "v.mp4"), str(tmp_path / "a.mp4"), str(tmp_path / "out.mp4"))
    assert out == str(tmp_path / "out.mp4")
    assert "-c" in calls["cmd"] and "copy" in calls["cmd"]


def test_mux_raises_ffmpeg_missing(tmp_path, monkeypatch):
    def raise_missing(cmd, **kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(sv.subprocess, "run", raise_missing)
    with pytest.raises(sv.SpotifyVideoError) as exc:
        sv.mux(str(tmp_path / "v.mp4"), str(tmp_path / "a.mp4"), str(tmp_path / "o.mp4"))
    assert str(exc.value) == "ffmpeg_missing"


def test_mux_raises_when_ffmpeg_fails(tmp_path, monkeypatch):
    class _Result:
        returncode = 1
        stderr = b"boom"

    monkeypatch.setattr(sv.subprocess, "run", lambda cmd, **kwargs: _Result())
    with pytest.raises(sv.SpotifyVideoError):
        sv.mux(str(tmp_path / "v.mp4"), str(tmp_path / "a.mp4"), str(tmp_path / "o.mp4"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_spotify_video.py -q`
Expected: FAIL — `AttributeError: module 'bot.spotify_video' has no attribute 'subtitle_languages'`

- [ ] **Step 3: Implement**

Dodaj `import subprocess`, następnie:

```python
MUX_TIMEOUT_SECONDS = 600


def subtitle_languages(manifest: dict) -> list[str]:
    """Return subtitle language codes the manifest offers."""

    return list(manifest.get("subtitle_language_codes") or [])


def fetch_subtitles(manifest: dict, language_code: str, dest_path: str) -> str | None:
    """Download one WebVTT subtitle track. Returns None when unavailable."""

    if language_code not in subtitle_languages(manifest):
        return None

    base_urls = manifest.get("subtitle_base_urls") or []
    template = manifest.get("subtitle_template")
    if not base_urls or not template:
        return None

    candidates = _expand_template(template, base_urls, language_code=language_code)
    try:
        payload = _fetch_with_failover(candidates)
    except SpotifyVideoError as exc:
        logging.warning("Spotify subtitle download failed: %s", exc)
        return None

    with open(dest_path, "wb") as file_obj:
        file_obj.write(payload)
    return dest_path


def mux(video_path: str, audio_path: str, out_path: str) -> str:
    """Combine the video and audio tracks without re-encoding."""

    try:
        result = subprocess.run(
            [
                "ffmpeg", "-v", "error",
                "-i", video_path,
                "-i", audio_path,
                "-c", "copy",
                "-y", out_path,
            ],
            capture_output=True,
            timeout=MUX_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise SpotifyVideoError("ffmpeg_missing") from exc

    if result.returncode != 0:
        detail = (result.stderr or b"").decode("utf-8", "replace")[:200]
        raise SpotifyVideoError(f"ffmpeg mux failed: {detail}")
    return out_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_spotify_video.py -q`
Expected: PASS (30 testów)

- [ ] **Step 5: Commit**

```bash
git add bot/spotify_video.py tests/test_spotify_video.py
git commit -m "Add ffmpeg mux and WebVTT subtitle fetch for Spotify video"
```

---

### Task 7: Warstwa serwisu

**Files:**
- Create: `bot/services/spotify_video_service.py`
- Test: `tests/test_spotify_video_service.py`

**Interfaces:**
- Consumes: całe publiczne API `bot.spotify_video`, `bot.spotify.parse_spotify_episode_url`
- Produces: `VideoEpisode` (frozen dataclass: `episode_id: str`, `title: str`, `show_name: str`, `duration_ms: int`, `manifest: dict`, `profiles: list[Profile]`, `subtitle_languages: list[str]`), `resolve_video_episode(url: str, *, cookies_file: str | None = None) -> VideoEpisode | None`, `get_video_error_message(reason: str) -> str`, `download_episode_media(...) -> str`

`resolve_video_episode` zwraca `None`, gdy odcinek nie ma wideo. Sytuacje wymagające komunikatu zgłasza jako `SpotifyVideoError` z jednym z kodów: `no_cookie`, `expired_session`, `api_changed`, `drm_protected`.

- [ ] **Step 1: Write the failing test**

Utwórz `tests/test_spotify_video_service.py`:

```python
"""Unit tests for bot.services.spotify_video_service."""

import json
from pathlib import Path

import pytest

from bot import spotify_video as sv
from bot.services import spotify_video_service as svs

FIXTURES = Path(__file__).parent / "fixtures"


def _manifest() -> dict:
    return json.loads((FIXTURES / "spotify_manifest.json").read_text(encoding="utf-8"))


def _embed(**overrides) -> sv.EmbedData:
    values = dict(
        access_token="FAKE",
        manifest_id="cdc59c43",
        title="Testowy odcinek",
        show_name="Testowy podcast",
        duration_ms=32000,
        has_video=True,
        requires_drm=False,
    )
    values.update(overrides)
    return sv.EmbedData(**values)


def test_resolve_video_episode_returns_none_for_non_episode_url(monkeypatch):
    monkeypatch.setattr(svs, "load_spotify_cookie", lambda path=None: "sp_dc_value")
    assert svs.resolve_video_episode("https://example.com/foo") is None


def test_resolve_video_episode_raises_without_cookie(monkeypatch):
    monkeypatch.setattr(svs, "load_spotify_cookie", lambda path=None: None)
    with pytest.raises(sv.SpotifyVideoError) as exc:
        svs.resolve_video_episode("https://open.spotify.com/episode/25NlRLSIHjtfU47zm4FGm4")
    assert str(exc.value) == "no_cookie"


def test_resolve_video_episode_returns_none_for_audio_only(monkeypatch):
    monkeypatch.setattr(svs, "load_spotify_cookie", lambda path=None: "sp_dc_value")
    monkeypatch.setattr(svs, "fetch_embed_data", lambda eid, cookie: _embed(has_video=False))
    assert svs.resolve_video_episode("https://open.spotify.com/episode/25NlRLSIHjtfU47zm4FGm4") is None


def test_resolve_video_episode_refuses_drm_protected(monkeypatch):
    monkeypatch.setattr(svs, "load_spotify_cookie", lambda path=None: "sp_dc_value")
    monkeypatch.setattr(svs, "fetch_embed_data", lambda eid, cookie: _embed(requires_drm=True))
    with pytest.raises(sv.SpotifyVideoError) as exc:
        svs.resolve_video_episode("https://open.spotify.com/episode/25NlRLSIHjtfU47zm4FGm4")
    assert str(exc.value) == "drm_protected"


def test_resolve_video_episode_builds_episode(monkeypatch):
    monkeypatch.setattr(svs, "load_spotify_cookie", lambda path=None: "sp_dc_value")
    monkeypatch.setattr(svs, "fetch_embed_data", lambda eid, cookie: _embed())
    monkeypatch.setattr(svs, "fetch_video_manifest", lambda mid, token: _manifest())

    episode = svs.resolve_video_episode(
        "https://open.spotify.com/episode/25NlRLSIHjtfU47zm4FGm4"
    )
    assert episode.title == "Testowy odcinek"
    assert [p.height for p in episode.profiles] == [1080, 720, 480]
    assert episode.subtitle_languages == ["pl-pl"]


def test_get_video_error_message_covers_every_reason():
    for reason in ("no_cookie", "expired_session", "api_changed", "drm_protected", "ffmpeg_missing"):
        message = svs.get_video_error_message(reason)
        assert message and message != reason


def test_get_video_error_message_drm_refuses_clearly():
    assert "DRM" in svs.get_video_error_message("drm_protected")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_spotify_video_service.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.services.spotify_video_service'`

- [ ] **Step 3: Implement**

Utwórz `bot/services/spotify_video_service.py`:

```python
"""Spotify video application service built on bot.spotify_video helpers."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

import requests

from bot.config import SPOTIFY_COOKIES_FILE
from bot.downloader_validation import sanitize_filename
from bot.spotify import parse_spotify_episode_url
from bot.spotify_video import (
    Profile,
    SpotifyVideoError,
    build_track_urls,
    download_track,
    estimate_size_mb,
    fetch_embed_data,
    fetch_video_manifest,
    find_audio_profile_id,
    list_profiles,
    load_spotify_cookie,
    manifest_duration_ms,
    mux,
    subtitle_languages,
)


@dataclass(frozen=True)
class VideoEpisode:
    """A Spotify episode resolved to its downloadable video manifest."""

    episode_id: str
    title: str
    show_name: str
    duration_ms: int
    manifest: dict
    profiles: list[Profile]
    subtitle_languages: list[str]


_ERROR_MESSAGES = {
    "no_cookie": (
        "Brak pliku z ciasteczkami Spotify.\n\n"
        "Aby pobierać wideo, wyeksportuj ciasteczka z zalogowanej sesji "
        "open.spotify.com do pliku:\n"
        "spotify_cookies.txt (format Netscape)\n\n"
        "Potrzebne jest ciasteczko sp_dc."
    ),
    "expired_session": (
        "Sesja Spotify wygasła.\n\n"
        "Zaloguj się ponownie na open.spotify.com i wyeksportuj ciasteczka "
        "jeszcze raz do pliku spotify_cookies.txt."
    ),
    "api_changed": (
        "Spotify zmieniło swoje API — pobieranie wideo chwilowo nie działa.\n\n"
        "To nie jest problem z siecią ani z Twoim kontem. "
        "Zgłoś to administratorowi bota."
    ),
    "drm_protected": (
        "Ten odcinek jest chroniony DRM — pobranie wideo nie jest możliwe.\n\n"
        "Możesz pobrać sam dźwięk lub transkrypcję."
    ),
    "ffmpeg_missing": (
        "Brak programu ffmpeg na serwerze — nie mogę połączyć obrazu z dźwiękiem.\n\n"
        "Zgłoś to administratorowi bota."
    ),
}


def get_video_error_message(reason: str) -> str:
    """Map a resolution failure code to a user-facing Polish message."""

    return _ERROR_MESSAGES.get(
        reason,
        "Nie udało się przygotować wideo z tego odcinka Spotify.",
    )


def resolve_video_episode(
    url: str, *, cookies_file: str | None = None
) -> VideoEpisode | None:
    """Resolve a Spotify episode URL to its video manifest.

    Returns None when the URL is not an episode link or the episode simply has
    no video — both are ordinary outcomes that fall back to the audio flow.
    Raises SpotifyVideoError carrying a reason code when the user needs to act.
    """

    episode_id = parse_spotify_episode_url(url)
    if not episode_id:
        return None

    cookie = load_spotify_cookie(cookies_file or SPOTIFY_COOKIES_FILE)
    if not cookie:
        raise SpotifyVideoError("no_cookie")

    try:
        embed = fetch_embed_data(episode_id, cookie)
    except requests.RequestException as exc:
        logging.error("Spotify embed request failed: %s", exc)
        raise SpotifyVideoError("api_changed") from exc

    if not embed.has_video or not embed.manifest_id:
        return None

    # Enforced boundary: protected content is refused, never circumvented.
    if embed.requires_drm:
        raise SpotifyVideoError("drm_protected")

    if not embed.access_token:
        raise SpotifyVideoError("expired_session")

    try:
        manifest = fetch_video_manifest(embed.manifest_id, embed.access_token)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        raise SpotifyVideoError(
            "expired_session" if status in (401, 403) else "api_changed"
        ) from exc
    except requests.RequestException as exc:
        raise SpotifyVideoError("api_changed") from exc

    return VideoEpisode(
        episode_id=episode_id,
        title=embed.title,
        show_name=embed.show_name,
        duration_ms=embed.duration_ms or manifest_duration_ms(manifest),
        manifest=manifest,
        profiles=list_profiles(manifest),
        subtitle_languages=subtitle_languages(manifest),
    )


def build_quality_options(episode: VideoEpisode) -> list[dict[str, Any]]:
    """Describe the selectable video qualities with estimated sizes."""

    return [
        {
            "height": profile.height,
            "profile_id": profile.id,
            "size_mb": estimate_size_mb(profile, episode.duration_ms),
        }
        for profile in episode.profiles
    ]


async def download_episode_media(
    *,
    episode: VideoEpisode,
    height: int | None,
    output_dir: str,
    executor: Any,
    progress_cb=None,
    cancellation=None,
) -> str:
    """Download an episode as muxed video, or as audio only when height is None.

    Returns the path to the finished file.
    """

    loop = asyncio.get_event_loop()
    base_name = sanitize_filename(episode.title or "spotify_episode")
    audio_profile_id = find_audio_profile_id(episode.manifest)

    audio_path = os.path.join(output_dir, f"{base_name}.audio.mp4")
    audio_init, audio_segments = build_track_urls(episode.manifest, audio_profile_id)

    if height is None:
        final_audio = os.path.join(output_dir, f"{base_name}.m4a")
        await loop.run_in_executor(
            executor,
            lambda: download_track(
                audio_init, audio_segments, final_audio,
                progress_cb=progress_cb, cancellation=cancellation,
            ),
        )
        return final_audio

    profile = next((p for p in episode.profiles if p.height == height), None)
    if profile is None:
        raise SpotifyVideoError("api_changed")

    video_path = os.path.join(output_dir, f"{base_name}.video.mp4")
    video_init, video_segments = build_track_urls(episode.manifest, profile.id)

    try:
        await loop.run_in_executor(
            executor,
            lambda: download_track(
                video_init, video_segments, video_path,
                progress_cb=progress_cb, cancellation=cancellation,
            ),
        )
        await loop.run_in_executor(
            executor,
            lambda: download_track(
                audio_init, audio_segments, audio_path, cancellation=cancellation
            ),
        )
        out_path = os.path.join(output_dir, f"{base_name}.mp4")
        await loop.run_in_executor(executor, lambda: mux(video_path, audio_path, out_path))
        return out_path
    finally:
        for temp_path in (video_path, audio_path):
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_spotify_video_service.py -q`
Expected: PASS (7 testów)

- [ ] **Step 5: Commit**

```bash
git add bot/services/spotify_video_service.py tests/test_spotify_video_service.py
git commit -m "Add Spotify video application service"
```

---

### Task 8: Stan sesji i parser callbacków

**Files:**
- Modify: `bot/session_store.py:87` (pole obok `spotify_resolved`) oraz `bot/session_store.py:226` (sprawdzenie pustej sesji)
- Modify: `bot/handlers/callback_parsing.py`
- Test: `tests/test_session_store.py`, nowy plik testowy nie jest potrzebny — dopisz do istniejących

**Interfaces:**
- Consumes: nic
- Produces: `SessionState.spotify_video: dict[str, Any] | None`, `parse_spotify_video_callback(data) -> dict | None` zwracające `{"media_type": "video", "height": 720}` albo `{"media_type": "audio", "height": None}`

- [ ] **Step 1: Write the failing test**

Dopisz do `tests/test_session_store.py`:

```python
def test_session_state_holds_spotify_video():
    from bot.session_store import SessionStore

    store = SessionStore()
    store.set_field(42, "spotify_video", {"episode_id": "abc"})
    assert store.get_field(42, "spotify_video") == {"episode_id": "abc"}
    store.pop_field(42, "spotify_video", None)
    assert store.get_field(42, "spotify_video") is None
```

Dopisz do `tests/test_telegram_callbacks.py` (lub utwórz `tests/test_callback_parsing.py`, jeśli wygodniej):

```python
from bot.handlers.callback_parsing import parse_spotify_video_callback


def test_parse_spotify_video_callback_video():
    assert parse_spotify_video_callback("spv_video_720p") == {
        "media_type": "video",
        "height": 720,
    }


def test_parse_spotify_video_callback_audio():
    assert parse_spotify_video_callback("spv_audio_m4a") == {
        "media_type": "audio",
        "height": None,
    }


def test_parse_spotify_video_callback_rejects_unknown_height():
    assert parse_spotify_video_callback("spv_video_144p") is None


def test_parse_spotify_video_callback_rejects_foreign_prefix():
    assert parse_spotify_video_callback("dl_video_720p") is None
    assert parse_spotify_video_callback(None) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_session_store.py tests/test_telegram_callbacks.py -q`
Expected: FAIL — `ImportError: cannot import name 'parse_spotify_video_callback'`

- [ ] **Step 3: Add the session field**

W `bot/session_store.py`, w dataclass `SessionState`, bezpośrednio pod `spotify_resolved`:

```python
    spotify_video: dict[str, Any] | None = None
```

Oraz w metodzie sprzątającej puste sesje, w łańcuchu warunków obok `session.spotify_resolved is None`:

```python
            and session.spotify_video is None
```

- [ ] **Step 4: Add the callback parser**

Dopisz do `bot/handlers/callback_parsing.py`:

```python
# Video heights the UI offers; anything else is a stale or forged callback.
SPOTIFY_VIDEO_HEIGHTS = (1080, 720, 480, 320)


def parse_spotify_video_callback(data):
    """Parses native Spotify video callback payloads.

    Expected formats:
      - spv_video_<height>p
      - spv_audio_m4a

    A dedicated prefix keeps the native Spotify path separate from the legacy
    dl_* flow, which routes through the iTunes/YouTube fallback.
    """
    if not isinstance(data, str) or not data.startswith("spv_"):
        return None

    if data == "spv_audio_m4a":
        return {"media_type": "audio", "height": None}

    parts = data.split("_")
    if len(parts) != 3 or parts[1] != "video":
        return None

    raw_height = parts[2]
    if not raw_height.endswith("p"):
        return None

    try:
        height = int(raw_height[:-1])
    except ValueError:
        return None

    if height not in SPOTIFY_VIDEO_HEIGHTS:
        return None

    return {"media_type": "video", "height": height}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_session_store.py tests/test_telegram_callbacks.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add bot/session_store.py bot/handlers/callback_parsing.py tests/
git commit -m "Add spotify_video session field and spv_ callback parser"
```

---

### Task 9: Klawiatura odcinka Spotify

**Files:**
- Modify: `bot/handlers/common_ui.py`
- Test: `tests/test_telegram_callbacks.py`

**Interfaces:**
- Consumes: `build_quality_options` z Task 7 (przekazywane jako zwykła lista słowników)
- Produces: `build_spotify_episode_keyboard(*, quality_options: list[dict], has_native_audio: bool, has_fallback_audio: bool) -> list`

- [ ] **Step 1: Write the failing test**

```python
from bot.handlers.common_ui import build_spotify_episode_keyboard


def _labels(keyboard):
    return [button.text for row in keyboard for button in row]


def test_spotify_keyboard_lists_qualities_with_sizes():
    keyboard = build_spotify_episode_keyboard(
        quality_options=[
            {"height": 1080, "profile_id": 0, "size_mb": 621.0},
            {"height": 720, "profile_id": 1, "size_mb": 336.0},
        ],
        has_native_audio=True,
        has_fallback_audio=False,
    )
    labels = _labels(keyboard)
    assert "Video 1080p (~621 MB)" in labels
    assert "Video 720p (~336 MB)" in labels


def test_spotify_keyboard_hides_fallback_audio_when_unavailable():
    keyboard = build_spotify_episode_keyboard(
        quality_options=[], has_native_audio=True, has_fallback_audio=False
    )
    labels = _labels(keyboard)
    assert "Audio (M4A) — Spotify" in labels
    assert not any("iTunes" in label for label in labels)


def test_spotify_keyboard_shows_fallback_audio_when_available():
    keyboard = build_spotify_episode_keyboard(
        quality_options=[], has_native_audio=False, has_fallback_audio=True
    )
    labels = _labels(keyboard)
    assert "Audio (MP3)" in labels
    assert "Audio (M4A) — Spotify" not in labels


def test_spotify_keyboard_always_offers_transcription():
    keyboard = build_spotify_episode_keyboard(
        quality_options=[], has_native_audio=False, has_fallback_audio=False
    )
    labels = _labels(keyboard)
    assert "Transkrypcja audio" in labels
    assert "Transkrypcja + Podsumowanie" in labels


def test_spotify_keyboard_uses_spv_callbacks():
    keyboard = build_spotify_episode_keyboard(
        quality_options=[{"height": 720, "profile_id": 1, "size_mb": 336.0}],
        has_native_audio=True,
        has_fallback_audio=False,
    )
    callbacks = [button.callback_data for row in keyboard for button in row]
    assert "spv_video_720p" in callbacks
    assert "spv_audio_m4a" in callbacks
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_telegram_callbacks.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_spotify_episode_keyboard'`

- [ ] **Step 3: Implement**

Dopisz do `bot/handlers/common_ui.py`, pod `build_main_keyboard`:

```python
def build_spotify_episode_keyboard(
    *,
    quality_options: list,
    has_native_audio: bool,
    has_fallback_audio: bool,
) -> list:
    """Build the keyboard for a Spotify episode from actually available sources.

    Buttons that would fail under the current configuration are omitted rather
    than shown and then erroring — the native video path needs a cookie jar,
    the legacy iTunes/YouTube path needs Web API credentials, and an episode
    may have either, both, or neither.
    """

    keyboard = []

    for option in quality_options:
        height = option["height"]
        size_mb = option["size_mb"]
        keyboard.append([
            InlineKeyboardButton(
                f"Video {height}p (~{size_mb:.0f} MB)",
                callback_data=f"spv_video_{height}p",
            )
        ])

    if has_native_audio:
        keyboard.append([
            InlineKeyboardButton("Audio (M4A) — Spotify", callback_data="spv_audio_m4a")
        ])

    if has_fallback_audio:
        keyboard.append([InlineKeyboardButton("Audio (MP3)", callback_data="dl_audio_mp3")])
        keyboard.append([InlineKeyboardButton("Audio (M4A)", callback_data="dl_audio_m4a")])

    keyboard.append([InlineKeyboardButton("Transkrypcja audio", callback_data="transcribe")])
    keyboard.append([
        InlineKeyboardButton("Transkrypcja + Podsumowanie", callback_data="transcribe_summary")
    ])

    return keyboard
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_telegram_callbacks.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bot/handlers/common_ui.py tests/test_telegram_callbacks.py
git commit -m "Build Spotify episode keyboard from available sources"
```

---

### Task 10: Rozpoznanie wideo przy odbiorze linku

**Files:**
- Modify: `bot/handlers/inbound_media.py:316-350` (`extracted_process_spotify_episode`)
- Test: `tests/test_telegram_integration.py`

**Interfaces:**
- Consumes: `resolve_video_episode`, `build_quality_options`, `get_video_error_message` z Task 7; `build_spotify_episode_keyboard` z Task 9
- Produces: sesja pod kluczem `spotify_video` zawierająca `{"episode_id", "title", "show_name", "duration_ms", "manifest", "subtitle_languages"}`

Kolejność jest istotna: rozpoznanie wideo idzie **pierwsze**, bo nie wymaga kluczy Web API. Stara ścieżka uruchamiana jest po nim i wyłącznie po to, żeby ustalić, czy pokazać zapasowe przyciski audio.

- [ ] **Step 1: Write the failing test**

```python
import pytest


@pytest.mark.asyncio
async def test_spotify_video_episode_stores_session_and_shows_qualities(monkeypatch):
    from bot.handlers import inbound_media as im
    from bot.services import spotify_video_service as svs

    episode = svs.VideoEpisode(
        episode_id="abc",
        title="Testowy odcinek",
        show_name="Testowy podcast",
        duration_ms=32000,
        manifest={"base_urls": []},
        profiles=[],
        subtitle_languages=["pl-pl"],
    )
    monkeypatch.setattr(im, "resolve_video_episode", lambda url: episode)
    monkeypatch.setattr(im, "build_quality_options", lambda ep: [
        {"height": 720, "profile_id": 1, "size_mb": 336.0}
    ])
    monkeypatch.setattr(im, "resolve_episode", _async_return(None))

    update, context = _make_update_context()
    await im.extracted_process_spotify_episode(update, context, "https://open.spotify.com/episode/abc")

    stored = context.user_data.get("spotify_video")
    assert stored["title"] == "Testowy odcinek"
```

Pomocniki `_async_return` i `_make_update_context` zbuduj w stylu już obecnym w `tests/test_telegram_integration.py`; jeśli plik ich nie ma, dodaj minimalne wersje z `unittest.mock.AsyncMock` i `MagicMock`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_telegram_integration.py -q -k spotify_video`
Expected: FAIL — `AttributeError: module 'bot.handlers.inbound_media' has no attribute 'resolve_video_episode'`

- [ ] **Step 3: Implement**

W `bot/handlers/inbound_media.py` dopisz do importów:

```python
from bot.services.spotify_video_service import (
    build_quality_options,
    get_video_error_message,
    resolve_video_episode,
)
from bot.spotify_video import SpotifyVideoError
from bot.handlers.common_ui import build_spotify_episode_keyboard
```

Zastąp ciało `extracted_process_spotify_episode`:

```python
async def extracted_process_spotify_episode(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    """Resolves a Spotify episode URL and shows download options.

    Video resolution runs first because it needs only a session cookie, while
    the legacy iTunes/YouTube path needs Web API credentials. Both may apply;
    the keyboard is assembled from whichever actually resolved.
    """
    chat_id = update.effective_chat.id
    progress_message = await update.message.reply_text("Spotify: sprawdzanie odcinka...")

    _set_session_value(context, chat_id, "current_url", url, user_urls)

    video_episode = None
    video_error = None
    try:
        video_episode = await asyncio.get_event_loop().run_in_executor(
            None, lambda: resolve_video_episode(url)
        )
    except SpotifyVideoError as exc:
        video_error = str(exc)

    resolved = await resolve_episode(url)
    fallback_available = resolved is not None and resolved.get("source") in ("itunes", "youtube")

    if video_episode is None and not fallback_available:
        await progress_message.edit_text(
            get_video_error_message(video_error)
            if video_error
            else get_resolution_error_message(resolved)
            or "Nie udało się przygotować tego odcinka."
        )
        return

    quality_options = build_quality_options(video_episode) if video_episode else []

    if video_episode is not None:
        _set_session_context_value(
            context, chat_id, "spotify_video",
            {
                "episode_id": video_episode.episode_id,
                "title": video_episode.title,
                "show_name": video_episode.show_name,
                "duration_ms": video_episode.duration_ms,
                "manifest": video_episode.manifest,
                "subtitle_languages": video_episode.subtitle_languages,
            },
            legacy_key="spotify_video",
        )
    else:
        _clear_session_context_value(context, chat_id, "spotify_video", legacy_key="spotify_video")

    if fallback_available:
        _set_session_context_value(
            context, chat_id, "spotify_resolved", resolved, legacy_key="spotify_resolved"
        )

    title = video_episode.title if video_episode else resolved.get("title", "Nieznany odcinek")
    show_name = video_episode.show_name if video_episode else (resolved.get("show_name") or "")
    duration_ms = video_episode.duration_ms if video_episode else (resolved.get("duration") or 0) * 1000
    duration_seconds = duration_ms // 1000
    duration_str = f"{duration_seconds // 60}:{duration_seconds % 60:02d}" if duration_seconds else "?"

    show_info = f"\nPodcast: {escape_md(show_name)}" if show_name else ""
    source_line = "Źródło: Spotify (wideo)" if video_episode else f"Źródło audio: {'iTunes' if resolved.get('source') == 'itunes' else 'YouTube'}"
    notice = f"\n\n{get_video_error_message(video_error)}" if video_error and fallback_available else ""

    reply_markup = InlineKeyboardMarkup(
        build_spotify_episode_keyboard(
            quality_options=quality_options,
            has_native_audio=video_episode is not None,
            has_fallback_audio=fallback_available,
        )
    )
    await progress_message.edit_text(
        f"*{escape_md(title)}*{show_info}\n"
        f"Czas trwania: {duration_str}\n"
        f"{source_line}{notice}\n\n"
        f"Wybierz opcję:",
        reply_markup=reply_markup,
        parse_mode="Markdown",
    )
```

Upewnij się, że `import asyncio` jest obecny na górze pliku.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_telegram_integration.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bot/handlers/inbound_media.py tests/test_telegram_integration.py
git commit -m "Detect Spotify video episodes and offer quality options"
```

---

### Task 11: Routing callbacków i pobieranie

**Files:**
- Modify: `bot/handlers/spotify_callbacks.py`
- Modify: `bot/telegram_callbacks.py:154` (nowa gałąź przed `if data.startswith("dl_")`)
- Test: `tests/test_callback_transcription_handlers.py`

**Interfaces:**
- Consumes: `parse_spotify_video_callback` (Task 8), `download_episode_media`, `VideoEpisode` (Task 7)
- Produces: `download_spotify_video(update, context, session_data: dict, *, height: int | None) -> None`

- [ ] **Step 1: Write the failing test**

```python
import pytest


@pytest.mark.asyncio
async def test_download_spotify_video_sends_file(monkeypatch, tmp_path):
    from bot.handlers import spotify_callbacks as sc

    produced = tmp_path / "episode.mp4"
    produced.write_bytes(b"X" * 2048)

    async def fake_download(**kwargs):
        return str(produced)

    monkeypatch.setattr(sc, "download_episode_media", fake_download)

    update, context = _make_callback_update_context()
    await sc.download_spotify_video(
        update, context,
        {"episode_id": "abc", "title": "Odcinek", "manifest": {}, "duration_ms": 32000,
         "show_name": "Podcast", "subtitle_languages": []},
        height=720,
    )
    assert context.bot.send_video.await_count == 1


@pytest.mark.asyncio
async def test_download_spotify_video_reports_drm_refusal(monkeypatch):
    from bot.handlers import spotify_callbacks as sc
    from bot.spotify_video import SpotifyVideoError

    async def fake_download(**kwargs):
        raise SpotifyVideoError("drm_protected")

    monkeypatch.setattr(sc, "download_episode_media", fake_download)

    update, context = _make_callback_update_context()
    await sc.download_spotify_video(
        update, context,
        {"episode_id": "abc", "title": "Odcinek", "manifest": {}, "duration_ms": 32000,
         "show_name": "Podcast", "subtitle_languages": []},
        height=720,
    )
    text = update.callback_query.edit_message_text.await_args[0][0]
    assert "DRM" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_callback_transcription_handlers.py -q -k spotify_video`
Expected: FAIL — `AttributeError: module 'bot.handlers.spotify_callbacks' has no attribute 'download_spotify_video'`

- [ ] **Step 3: Implement the handler**

Dopisz do `bot/handlers/spotify_callbacks.py` (rozszerz importy):

```python
from bot.services.spotify_video_service import (
    VideoEpisode,
    download_episode_media,
    get_video_error_message,
)
from bot.spotify_video import SpotifyVideoCancelled, SpotifyVideoError
from bot.security_limits import TELEGRAM_UPLOAD_LIMIT_MB


async def download_spotify_video(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    session_data: dict,
    *,
    height: int | None,
):
    """Download a Spotify episode as video (or native audio) and send it."""

    query = update.callback_query
    chat_id = update.effective_chat.id
    title = session_data.get("title", "Spotify episode")

    async def update_status(text):
        await safe_edit_message(query, text)

    chat_download_path = os.path.join(DOWNLOAD_PATH, str(chat_id))
    os.makedirs(chat_download_path, exist_ok=True)

    episode = VideoEpisode(
        episode_id=session_data["episode_id"],
        title=title,
        show_name=session_data.get("show_name", ""),
        duration_ms=int(session_data.get("duration_ms") or 0),
        manifest=session_data["manifest"],
        profiles=[],
        subtitle_languages=session_data.get("subtitle_languages", []),
    )
    # Profiles are recomputed from the stored manifest so the session payload
    # stays JSON-friendly.
    from bot.spotify_video import list_profiles
    episode = replace(episode, profiles=list_profiles(episode.manifest))

    label = "wideo" if height else "audio"
    await update_status(f"Pobieranie {label} ze Spotify...")

    last_report = {"done": -1}

    def progress_cb(done, total):
        # Telegram rate-limits edits; only surface whole-percent changes.
        percent = int(done * 100 / total) if total else 100
        if percent != last_report["done"]:
            last_report["done"] = percent
            logging.debug("Spotify download %s: %d/%d", label, done, total)

    downloaded_path = None
    try:
        downloaded_path = await download_episode_media(
            episode=episode,
            height=height,
            output_dir=chat_download_path,
            executor=_executor,
            progress_cb=progress_cb,
        )

        file_size_mb = os.path.getsize(downloaded_path) / (1024 * 1024)
        await update_status(f"Pobieranie zakończone ({file_size_mb:.1f} MB).\n\nWysyłanie...")

        if file_size_mb > TELEGRAM_UPLOAD_LIMIT_MB:
            from bot.mtproto import mtproto_unavailability_reason, send_audio_mtproto, send_video_mtproto

            reason = mtproto_unavailability_reason()
            if reason is not None:
                await update_status(
                    f"Plik za duży dla Bot API ({file_size_mb:.0f} MB, "
                    f"limit: {TELEGRAM_UPLOAD_LIMIT_MB} MB).\n{reason}"
                )
                return
            if height is None:
                ok = await send_audio_mtproto(
                    chat_id, downloaded_path, title=title, caption=title[:200]
                )
            else:
                ok = await send_video_mtproto(
                    chat_id, downloaded_path, caption=title[:200]
                )
            if not ok:
                await update_status("Wysyłanie pliku przez MTProto nie powiodło się.")
                return
        else:
            with open(downloaded_path, "rb") as file_obj:
                if height is None:
                    await context.bot.send_audio(
                        chat_id=chat_id, audio=file_obj, title=title,
                        caption=title[:200], read_timeout=120, write_timeout=120,
                    )
                else:
                    await context.bot.send_video(
                        chat_id=chat_id, video=file_obj, caption=title[:200],
                        read_timeout=120, write_timeout=120,
                    )

        record_download_for(
            context, chat_id, title,
            _get_session_value(context, chat_id, "current_url", user_urls) or "",
            f"spotify_native_{'audio' if height is None else str(height) + 'p'}",
            file_size_mb,
        )
        await update_status(f"Gotowe: {title}")

    except SpotifyVideoCancelled:
        await update_status("Pobieranie anulowane.")
    except SpotifyVideoError as exc:
        await update_status(get_video_error_message(str(exc)))
    except Exception as exc:
        logging.error("Error downloading Spotify video: %s", exc)
        await update_status(f"Błąd pobierania: {str(exc)[:200]}")
    finally:
        if downloaded_path and os.path.exists(downloaded_path):
            try:
                os.remove(downloaded_path)
            except OSError:
                pass
```

Dodaj `from dataclasses import replace` do importów pliku.

- [ ] **Step 4: Wire the routing**

W `bot/telegram_callbacks.py`, **przed** `if data.startswith("dl_")` (linia ~154), wstaw:

```python
    if data.startswith("spv_"):
        video_data = parse_spotify_video_callback(data)
        if not video_data:
            await query.edit_message_text("Nieobsługiwany format. Spróbuj wybrać format ponownie.")
            return
        session_data = _get_session_context_value(
            context, chat_id, "spotify_video", legacy_key="spotify_video"
        )
        if not session_data:
            await query.edit_message_text("Sesja Spotify wygasła. Wyślij link ponownie.")
            return
        await download_spotify_video(
            update, context, session_data, height=video_data["height"]
        )
        return
```

Dopisz do importów pliku:

```python
from bot.handlers.callback_parsing import parse_spotify_video_callback
from bot.handlers.spotify_callbacks import download_spotify_video
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/ -q`
Expected: PASS — cały pakiet testów

- [ ] **Step 6: Commit**

```bash
git add bot/handlers/spotify_callbacks.py bot/telegram_callbacks.py tests/test_callback_transcription_handlers.py
git commit -m "Route spv_ callbacks to native Spotify video download"
```

---

### Task 12: Napisy jako źródło transkrypcji

**Files:**
- Modify: `bot/services/spotify_video_service.py`
- Modify: `bot/handlers/spotify_callbacks.py` (`_handle_transcription`)
- Test: `tests/test_spotify_video_service.py`

**Interfaces:**
- Consumes: `fetch_subtitles`, `subtitle_languages` (Task 6), `parse_subtitle_file` z `bot.downloader_subtitles`, `save_transcript_markdown` z `bot.services.transcription_service`
- Produces: `transcript_from_subtitles(*, episode: VideoEpisode, output_dir: str, sanitized_title: str) -> str | None` — zwraca ścieżkę do gotowego pliku `.md` albo `None`, gdy napisów nie ma

- [ ] **Step 1: Write the failing test**

```python
def test_transcript_from_subtitles_produces_markdown(monkeypatch, tmp_path):
    episode = svs.VideoEpisode(
        episode_id="abc", title="Odcinek", show_name="Podcast",
        duration_ms=32000, manifest=_manifest(), profiles=[],
        subtitle_languages=["pl-pl"],
    )

    def fake_fetch(manifest, language_code, dest_path):
        Path(dest_path).write_text(
            "WEBVTT\n\n00:00:00.350 --> 00:00:03.950\nPierwsza linia\n\n"
            "00:00:03.950 --> 00:00:08.710\nDruga linia\n",
            encoding="utf-8",
        )
        return dest_path

    monkeypatch.setattr(svs, "fetch_subtitles", fake_fetch)

    path = svs.transcript_from_subtitles(
        episode=episode, output_dir=str(tmp_path), sanitized_title="odcinek"
    )
    content = Path(path).read_text(encoding="utf-8")
    assert "Pierwsza linia" in content
    assert "Druga linia" in content
    assert "-->" not in content, "timestamps must be stripped"


def test_transcript_from_subtitles_returns_none_without_subtitles(tmp_path):
    episode = svs.VideoEpisode(
        episode_id="abc", title="Odcinek", show_name="Podcast",
        duration_ms=32000, manifest={}, profiles=[], subtitle_languages=[],
    )
    assert svs.transcript_from_subtitles(
        episode=episode, output_dir=str(tmp_path), sanitized_title="odcinek"
    ) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_spotify_video_service.py -q`
Expected: FAIL — `AttributeError: module has no attribute 'transcript_from_subtitles'`

- [ ] **Step 3: Implement**

Dopisz do `bot/services/spotify_video_service.py`:

```python
from bot.downloader_subtitles import parse_subtitle_file
from bot.services.transcription_service import save_transcript_markdown
from bot.spotify_video import fetch_subtitles


def transcript_from_subtitles(
    *, episode: VideoEpisode, output_dir: str, sanitized_title: str
) -> str | None:
    """Turn Spotify's WebVTT subtitles into a transcript artifact.

    Returns the markdown path, or None when the episode ships no subtitles —
    callers then fall back to the Groq audio transcription pipeline.
    """

    if not episode.subtitle_languages:
        return None

    language_code = episode.subtitle_languages[0]
    vtt_path = os.path.join(output_dir, f"{sanitized_title}.{language_code}.vtt")

    if fetch_subtitles(episode.manifest, language_code, vtt_path) is None:
        return None

    try:
        transcript_text = parse_subtitle_file(vtt_path)
    finally:
        if os.path.exists(vtt_path):
            try:
                os.remove(vtt_path)
            except OSError:
                pass

    if not transcript_text.strip():
        return None

    return save_transcript_markdown(
        title=episode.title,
        transcript_text=transcript_text,
        sanitized_title=sanitized_title,
        output_dir=output_dir,
    )
```

- [ ] **Step 4: Wire it into the transcription flow**

W `bot/handlers/spotify_callbacks.py`, w `_handle_transcription`, **przed** wywołaniem `run_transcription_with_progress`, wstaw skrót na napisy:

```python
    # Spotify ships ready WebVTT subtitles for many video episodes. Using them
    # skips Groq entirely: instant, free, and not bound by audio length limits.
    video_session = _get_session_context_value(
        context, chat_id, "spotify_video", legacy_key="spotify_video"
    )
    transcript_path = None
    if video_session:
        from bot.services.spotify_video_service import VideoEpisode, transcript_from_subtitles
        from bot.spotify_video import list_profiles

        episode = VideoEpisode(
            episode_id=video_session["episode_id"],
            title=video_session.get("title", title),
            show_name=video_session.get("show_name", ""),
            duration_ms=int(video_session.get("duration_ms") or 0),
            manifest=video_session["manifest"],
            profiles=list_profiles(video_session["manifest"]),
            subtitle_languages=video_session.get("subtitle_languages", []),
        )
        await update_status("Pobieranie napisów ze Spotify...")
        transcript_path = await asyncio.get_event_loop().run_in_executor(
            _executor,
            lambda: transcript_from_subtitles(
                episode=episode,
                output_dir=chat_download_path,
                sanitized_title=os.path.splitext(os.path.basename(downloaded_file_path))[0],
            ),
        )

    if transcript_path is None:
        transcript_path = await run_transcription_with_progress(
            source_path=downloaded_file_path,
            output_dir=chat_download_path,
            executor=_executor,
            status_callback=update_status,
        )
```

Usuń poprzednie, bezwarunkowe przypisanie `transcript_path = await run_transcription_with_progress(...)`. Dodaj `import asyncio` oraz import `_get_session_context_value` z `bot.session_context`, jeśli plik jeszcze go nie ma.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add bot/services/spotify_video_service.py bot/handlers/spotify_callbacks.py tests/test_spotify_video_service.py
git commit -m "Use Spotify WebVTT subtitles as transcript source with Groq fallback"
```

---

## Weryfikacja końcowa

- [ ] `python -m pytest tests/ -q` — cały pakiet przechodzi
- [ ] `git log --oneline -12` — dwanaście commitów, każdy z własnym testem
- [ ] Test ręczny z prawdziwym plikiem `spotify_cookies.txt`: wyślij botowi
      `https://open.spotify.com/episode/25NlRLSIHjtfU47zm4FGm4`, sprawdź że
      pojawiają się cztery jakości z szacunkami, pobierz 480p (~183 MB) i
      potwierdź, że plik odtwarza się z dźwiękiem.
- [ ] Test ręczny transkrypcji: wybierz „Transkrypcja audio" i potwierdź, że
      wynik pojawia się w sekundy (napisy), a nie po kilku minutach (Groq).
- [ ] Sprawdź, że odcinek **bez** wideo zachowuje się dokładnie jak dotąd.
