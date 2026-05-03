# X (Twitter) Platform Support — Design

**Status:** Approved for implementation
**Date:** 2026-04-23
**Scope:** Phase 1 of platform-registry refactor + X integration

## Problem

The project supports YouTube, Vimeo, TikTok, Instagram, LinkedIn, Castbox, and
Spotify. It does not accept URLs from X (formerly Twitter). Adding X is the
trigger for a broader cleanup: today, "what each platform does" is expressed
through `if platform == "..."` string checks scattered across at least four
files (`security_policy.py`, `common_ui.py`, `inbound_media.py`,
`command_access.py`) and hardcoded lists of platform names in two more
(`telegram_callbacks.py`, `command_access.py`). Every new platform grows that
surface.

This spec introduces a `bot/platforms/` registry that centralizes per-platform
configuration, adds X as a first-class platform, and migrates the "simple
video" platforms (TikTok, Vimeo, LinkedIn) onto the new registry. Platforms
with unique logic (Instagram, Castbox, Spotify, YouTube) get registry entries
but keep their current code paths — they are explicitly out of scope for the
migration in this phase.

## Scope Decisions

All decisions made during brainstorming, preserved here so implementation can
verify intent without re-deriving it.

| Decision | Choice | Rationale |
|---|---|---|
| Content types supported | Video only (single tweet) | Matches user's immediate need; mirrors TikTok scope. Images, carousels, and threads deferred to future phases. |
| Cookies handling | Lazy — attempt, explain on failure | Consistent with Instagram/LinkedIn. No pre-flight checks. |
| Menu layout for X | Mirror TikTok: hide FLAC and time-range | X videos are typically short. Long-form X Premium videos are rare and can be addressed later if needed. |
| Refactor scope | Phase 1: registry + simple-video platforms migrated; others get registry entries only | Full refactor of Instagram/podcasts/YouTube in one spec is too risky; each gets its own phase. |
| Per-platform cookies hints | Yes, `cookies_hint` field on `PlatformConfig` | User chose platform-specific messages over one generic fallback. |
| Platform in session | String name, not full config | Backward-compatible with current session shape; lookup on use. |

## Architecture

### New package: `bot/platforms/`

```
bot/platforms/
├── __init__.py      # registry + public lookup API
├── base.py          # PlatformConfig dataclass
├── youtube.py       # CONFIG only (no migration in Phase 1)
├── vimeo.py         # CONFIG — simple-video, migrated
├── tiktok.py        # CONFIG — simple-video, migrated
├── linkedin.py      # CONFIG — simple-video, migrated
├── x.py             # CONFIG — simple-video, NEW
├── instagram.py     # CONFIG only (no migration in Phase 1)
├── castbox.py       # CONFIG only (no migration in Phase 1)
└── spotify.py       # CONFIG only (no migration in Phase 1)
```

Each platform module exports exactly one symbol: `CONFIG: PlatformConfig`.
Platform modules contain **data only** — no logic, no side effects on import.
Future phases will move platform-specific functions (e.g., `get_instagram_post_info`)
into their respective modules.

### `bot/platforms/base.py`

```python
from dataclasses import dataclass

@dataclass(frozen=True, kw_only=True)
class PlatformConfig:
    name: str                    # stable identifier in code, e.g. "x", "tiktok"
    display_name: str            # shown in UI, e.g. "X", "TikTok"
    domains: tuple[str, ...]     # e.g. ("x.com", "twitter.com", "mobile.twitter.com")
    hide_flac: bool              # remove FLAC option from audio menu
    hide_time_range: bool        # remove "✂️ Zakres czasowy" button
    is_podcast: bool             # use podcast menu variant instead of video/audio
    media_label: str             # "filmie" | "odcinku" — for error phrasing
    requires_cookies: bool       # included in "X, Y, Z may need cookies.txt" message
    cookies_hint: str | None = None  # per-platform auth-error hint; None falls back to generic
```

Using `kw_only=True` + `frozen=True` enforces explicit construction and
immutability. All nine fields required per platform except `cookies_hint`.

### `bot/platforms/__init__.py`

```python
from bot.platforms import youtube, vimeo, tiktok, linkedin, x, instagram, castbox, spotify
from bot.platforms.base import PlatformConfig

PLATFORMS: tuple[PlatformConfig, ...] = (
    youtube.CONFIG, vimeo.CONFIG, tiktok.CONFIG, linkedin.CONFIG,
    x.CONFIG, instagram.CONFIG, castbox.CONFIG, spotify.CONFIG,
)

_BY_NAME: dict[str, PlatformConfig] = {p.name: p for p in PLATFORMS}
_BY_DOMAIN: dict[str, PlatformConfig] = {
    domain: p
    for p in PLATFORMS
    for domain in _expand_with_www(p.domains)
}

def get_platform(name: str) -> PlatformConfig | None:
    return _BY_NAME.get(name)

def detect_by_domain(host: str) -> PlatformConfig | None:
    return _BY_DOMAIN.get(host.lower())

def all_domains() -> frozenset[str]:
    return frozenset(_BY_DOMAIN.keys())
```

`_expand_with_www` mirrors the existing behavior in `security_policy.py:28-31`:
for any non-subdomain entry (one dot, e.g., `x.com`), also register `www.x.com`.
Subdomain entries (`mobile.twitter.com`) are not expanded.

### Example platform modules

```python
# bot/platforms/x.py
from bot.platforms.base import PlatformConfig

CONFIG = PlatformConfig(
    name="x",
    display_name="X",
    domains=("x.com", "twitter.com", "mobile.twitter.com"),
    hide_flac=True,
    hide_time_range=True,
    is_podcast=False,
    media_label="filmie",
    requires_cookies=True,
    cookies_hint=(
        "Wiele tweetów (szczególnie oznaczonych jako Sensitive) wymaga "
        "zalogowania. Zaloguj się na x.com w przeglądarce i wyeksportuj "
        "cookies rozszerzeniem 'Get cookies.txt LOCALLY'."
    ),
)
```

```python
# bot/platforms/spotify.py
from bot.platforms.base import PlatformConfig

CONFIG = PlatformConfig(
    name="spotify",
    display_name="Spotify",
    domains=("open.spotify.com",),
    hide_flac=True,
    hide_time_range=True,
    is_podcast=True,
    media_label="odcinku",
    requires_cookies=False,
    cookies_hint=None,
)
```

## Integration Points (5 files)

### `bot/security_policy.py`

- Replace inline `_DOMAIN_TO_PLATFORM` dict with `from bot.platforms import detect_by_domain, all_domains, get_platform`.
- `ALLOWED_DOMAINS` → `all_domains()`.
- `detect_platform(url) -> str | None` — keep signature for backward compatibility, implement as `detect_by_domain(host).name if match else None`.
- `get_media_label(platform: str | None) -> str` — look up via `get_platform(platform)`, fall back to `"filmie"` if unknown.
- `normalize_url` (Castbox redirect handling) stays untouched — it is URL logic, not registry data.

### `bot/handlers/common_ui.py`

`build_main_keyboard(platform: str, ...)`:

```python
config = get_platform(platform)
if config is None:
    raise ValueError(f"Unknown platform in session: {platform!r}")
is_podcast = config.is_podcast
hide_flac = config.hide_flac
hide_time_range = config.hide_time_range
```

Raising is appropriate here because an unknown platform name in the session
indicates a programming bug (writing to session but not registering the
platform), not a user-facing condition.

### `bot/handlers/inbound_media.py`

- The "Obsługiwane platformy:" message (the reply sent when `validate_url`
  rejects a URL; currently a hardcoded list around line 267) is generated by
  iterating `PLATFORMS` and formatting `"- {display_name} ({first_domain})"`.
  Ordering follows declaration order in `PLATFORMS`.
- Special branches (`if platform == "instagram"`, `if platform == "castbox"`,
  `if platform == "spotify"`) stay exactly as they are — Phase 1 does not touch
  them. The `platform` variable at line 357 still comes from
  `detect_platform(url)`, now backed by the registry.

### `bot/handlers/command_access.py`

Two hardcoded strings ("TikTok, Instagram i LinkedIn mogą wymagać cookies")
replaced by dynamic generation:

```python
requires_cookies_names = [
    p.display_name for p in PLATFORMS if p.requires_cookies
]
cookies_msg = f"{', '.join(requires_cookies_names)} mogą wymagać cookies.txt"
```

### `bot/handlers/download_callbacks.py`

This file has no `if platform == "..."` check today — Phase 1 **adds** a new
platform lookup (does not refactor an existing one). At the keyword-based
auth-error detection block (currently around line 589):

```python
if any(keyword in error_str for keyword in ("login", "sign in", "cookie", "authentication")):
    platform_name = session_context.get("platform")  # read from current session
    config = get_platform(platform_name) if platform_name else None
    hint = config.cookies_hint if config and config.cookies_hint else GENERIC_COOKIES_HINT
    # ... send message with `hint` substituted
```

`GENERIC_COOKIES_HINT` is the **existing fallback text extracted verbatim into
a module-level constant** — no wording change, no translation change. The new
code only inserts the platform-specific `cookies_hint` when available;
platforms without one (or unknown platform in session) see the same message
they see today.

## Data Flow

1. User sends URL → `inbound_media.handle_url`
2. `normalize_url(url)` — resolves Castbox redirects, unchanged
3. `validate_url(url)` — checks `parsed.netloc` against `all_domains()`
4. `detect_platform(url)` → returns `"x"` (or other name); stored in session as string
5. Existing branching in `inbound_media.py` dispatches podcast/Instagram/default flows
6. `build_main_keyboard("x")` — reads flags from `get_platform("x")`, renders menu without FLAC and without time-range buttons
7. User picks format → `execute_download_plan` runs yt-dlp (no platform-specific logic in the download pipeline)
8. On auth failure → error handler looks up `get_platform(session.platform).cookies_hint` and surfaces it; successful downloads follow the standard upload path

The download pipeline itself (`bot/services/download_service.py`) remains
platform-agnostic. yt-dlp's Twitter extractor handles X URLs natively; the
existing `yt-dlp>=2024.12.6` pin in `requirements.txt` is sufficient.

## Error Handling

| Scenario | Behavior |
|---|---|
| URL matches no domain | Existing "supported platforms" message, now generated from registry |
| yt-dlp auth error (login/sign-in/cookie keywords) | Per-platform `cookies_hint` if set, else `GENERIC_COOKIES_HINT` |
| X "Sensitive media" without cookies | Surfaces as auth error → handled by above case (no separate code path) |
| yt-dlp extractor broken / schema change | Propagate original yt-dlp error, no platform-specific wrapping |
| Missing `platform` in session (defensive) | Fall back to generic hint, log warning |
| `get_platform(name)` returns None for a name from session | Raise — programming bug, not user-facing |

No pre-flight cookies check for X. Consistent with all other platforms.

## Testing Strategy

### New: `tests/test_platforms.py`

- `test_all_platforms_have_unique_names`
- `test_no_overlapping_domains`
- `test_detect_by_domain_matches_known_hosts` (parametrized over every
  `(domain, platform.name)` pair, plus `www.` variants where applicable)
- `test_detect_by_domain_returns_none_for_unknown`
- `test_get_platform_returns_config_for_known_names`
- `test_all_domains_includes_www_variants_for_bare_domains`
- `test_x_platform_has_tiktok_style_menu_flags` — regression for the
  hide_flac/hide_time_range decision
- `test_cookies_hint_set_for_platforms_requiring_cookies_except_podcasts`

### Updated: `tests/test_security_policy.py`

- Extend parametrization of `detect_platform` with `x.com`, `twitter.com`,
  `mobile.twitter.com`, and their `www.` variants where applicable
- Happy-path `validate_url` for `https://x.com/i/status/{id}` and
  `https://twitter.com/{user}/status/{id}`
- Any assertions that hardcode the old `_DOMAIN_TO_PLATFORM` dict are rewritten
  to iterate the registry

### Updated: `tests/test_inbound_media_handlers.py`

- Assertions on the "supported platforms" message relaxed from exact string
  match to containment checks (must include each platform's `display_name`)

### Optional integration: `tests/test_telegram_integration.py`

One scenario: simulate user sending `https://x.com/i/status/{id}` and assert
`session.platform == "x"` and the rendered keyboard contains neither FLAC nor
time-range buttons. Skipped if it becomes fragile to session-store changes.

### Out of scope for tests

- Actual network fetches from X (non-deterministic, requires credentials)
- yt-dlp's own Twitter extractor (covered upstream)

## What Is Not Changing in Phase 1

- `bot/services/download_service.py` — platform-agnostic, no changes
- `bot/handlers/inbound_media.py` special branches for Instagram/Spotify/Castbox
- `bot/telegram_callbacks.py:144` Spotify-specific callback
- `bot/downloader_media.py` Instagram post/photo helpers
- `bot/spotify.py`, `bot/mtproto.py`, transcription pipeline
- CLI (`bot/cli.py`) — inherits registry via `validate_url`, no explicit changes
- `cookies.txt` handling, session store schema, authorization flow

## Future Phases (Out of Scope)

- **Phase 2 — Instagram:** Move `downloader_media.get_instagram_post_info`,
  `is_photo_entry`, carousel keyboard into `bot/platforms/instagram.py`
- **Phase 3 — Podcasts:** Move Spotify/Castbox iTunes resolution and
  podcast-specific callbacks into `bot/platforms/{spotify,castbox}.py`
- **Phase 4 — YouTube:** Move YouTube-specific format-sort heuristics and
  subtitles handling into `bot/platforms/youtube.py`

Each future phase gets its own spec.

## Risk Assessment

- **Regression in simple-video platforms (TikTok/Vimeo/LinkedIn):** Mitigated
  by keeping `detect_platform(url) -> str` signature stable and by the existing
  test suite in `test_security_policy.py` (extended, not replaced).
- **Session schema drift:** None — session still stores `platform: str`.
- **Import cycles:** `bot/platforms/` imports nothing from `bot/handlers/` or
  `bot/services/`. The reverse dependency is the only direction.
- **Docstring rot in platform files:** Deliberate — platform modules are data
  only. No logic, no docstrings to drift.

## Acceptance Criteria

Implementation is complete when:

1. `bot/platforms/` exists with all 8 platforms defined
2. The 5 integration-point files read from the registry. Specifically:
   `security_policy.py` has no inline `_DOMAIN_TO_PLATFORM`, `common_ui.py` has
   no `platform in ("tiktok", ...)` tuple, `command_access.py` has no hardcoded
   "TikTok, Instagram i LinkedIn" string, `inbound_media.py` generates its
   supported-platforms list from `PLATFORMS`, `download_callbacks.py` looks up
   `cookies_hint` via `get_platform`. Remaining `if platform == "..."` branches
   in `inbound_media.py` (Instagram/Castbox/Spotify special flows) and
   `telegram_callbacks.py:144` (Spotify callback) are custom logic paths, not
   data queries, and are permitted to stay in Phase 1.
3. `tests/test_platforms.py` exists and passes
4. `tests/test_security_policy.py` extended with X and passes
5. Sending `https://x.com/i/status/2047124368623534362` to the bot produces
   the TikTok-style menu (no FLAC, no time-range) and a successful download
   for public video tweets
6. README's platforms table updated with an X row
7. Full test suite passes
