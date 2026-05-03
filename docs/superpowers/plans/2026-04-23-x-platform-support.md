# X (Twitter) Platform Support — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add X (x.com, twitter.com, mobile.twitter.com) as a supported download platform while introducing a central `bot/platforms/` registry that eliminates scattered string checks for TikTok/Vimeo/LinkedIn.

**Architecture:** Data-only `PlatformConfig` dataclass per platform + central registry with name/domain lookups. Integration sites (security_policy, common_ui, inbound_media, command_access, auth_service, download_callbacks) read from the registry instead of hardcoding platform names. Instagram, Castbox, Spotify, and YouTube get registry entries but their custom flows stay untouched.

**Tech Stack:** Python 3.11+, `dataclasses`, `pytest`, yt-dlp ≥ 2024.12.6.

**Spec:** `docs/superpowers/specs/2026-04-23-x-platform-support-design.md`

**Scope note:** During plan authoring, discovered a 6th integration point (`bot/services/auth_service.py` with two hardcoded platform-list strings) in addition to the 5 named in the spec. This plan includes it as Task 8.

---

## File Structure

### Created

| Path | Responsibility |
|---|---|
| `bot/platforms/__init__.py` | Registry: `PLATFORMS`, `get_platform`, `detect_by_domain`, `all_domains`, `_expand_with_www` |
| `bot/platforms/base.py` | `PlatformConfig` dataclass (frozen, kw_only) |
| `bot/platforms/youtube.py` | YouTube CONFIG (data only) |
| `bot/platforms/vimeo.py` | Vimeo CONFIG |
| `bot/platforms/tiktok.py` | TikTok CONFIG |
| `bot/platforms/linkedin.py` | LinkedIn CONFIG |
| `bot/platforms/x.py` | X CONFIG (new platform) |
| `bot/platforms/instagram.py` | Instagram CONFIG |
| `bot/platforms/castbox.py` | Castbox CONFIG |
| `bot/platforms/spotify.py` | Spotify CONFIG |
| `tests/test_platforms.py` | Registry integrity, lookup functions, per-platform regressions |

### Modified

| Path | Change |
|---|---|
| `bot/security_policy.py` | Replace `_DOMAIN_TO_PLATFORM`/`ALLOWED_DOMAINS`/`get_media_label` with registry delegation |
| `bot/handlers/common_ui.py` | `build_main_keyboard` reads flags via `get_platform(name)` |
| `bot/handlers/inbound_media.py` | Generate "Obsługiwane platformy" list from `PLATFORMS` |
| `bot/handlers/command_access.py` | Generate `/start` platform list and `/status` cookies hint from registry |
| `bot/services/auth_service.py` | Generate two PIN-flow platform-list strings from registry |
| `bot/handlers/download_callbacks.py` | Add `cookies_hint` lookup; extract `GENERIC_COOKIES_HINT` constant |
| `tests/test_security_policy.py` | Extend `detect_platform`/`validate_url` parametrizations with X |
| `tests/test_command_access_handlers.py` | Relax assertions on the hardcoded 7-platform list so X inclusion passes |
| `README.md` | Add X row to supported-platforms table |

---

### Task 1: PlatformConfig dataclass (TDD)

**Files:**
- Create: `bot/platforms/base.py`
- Create: `bot/platforms/__init__.py` (empty module marker; real registry lands in Task 3)
- Test: `tests/test_platforms.py`

- [ ] **Step 1: Create empty `bot/platforms/__init__.py` so the package is importable**

```python
"""Platform registry package. Contents land in Task 3."""
```

- [ ] **Step 2: Write the failing test for `PlatformConfig`**

Create `tests/test_platforms.py` with the following content:

```python
"""Tests for bot/platforms/ registry and per-platform CONFIG modules."""

import dataclasses

import pytest

from bot.platforms.base import PlatformConfig


def test_platform_config_is_frozen_and_kw_only():
    config = PlatformConfig(
        name="test",
        display_name="Test",
        domains=("test.com",),
        hide_flac=False,
        hide_time_range=False,
        is_podcast=False,
        media_label="filmie",
        requires_cookies=False,
    )
    assert dataclasses.is_dataclass(config)

    with pytest.raises(dataclasses.FrozenInstanceError):
        config.name = "mutated"  # type: ignore[misc]


def test_platform_config_defaults_cookies_hint_to_none():
    config = PlatformConfig(
        name="test",
        display_name="Test",
        domains=("test.com",),
        hide_flac=False,
        hide_time_range=False,
        is_podcast=False,
        media_label="filmie",
        requires_cookies=False,
    )
    assert config.cookies_hint is None


def test_platform_config_requires_keyword_arguments():
    with pytest.raises(TypeError):
        PlatformConfig(  # type: ignore[call-arg]
            "test", "Test", ("test.com",), False, False, False, "filmie", False
        )


def test_platform_config_equality_on_same_fields():
    a = PlatformConfig(
        name="test",
        display_name="Test",
        domains=("test.com",),
        hide_flac=False,
        hide_time_range=False,
        is_podcast=False,
        media_label="filmie",
        requires_cookies=False,
    )
    b = PlatformConfig(
        name="test",
        display_name="Test",
        domains=("test.com",),
        hide_flac=False,
        hide_time_range=False,
        is_podcast=False,
        media_label="filmie",
        requires_cookies=False,
    )
    assert a == b
```

- [ ] **Step 3: Run test to verify it fails**

Run: `source venv/bin/activate && pytest tests/test_platforms.py -v`
Expected: FAIL with `ImportError: No module named 'bot.platforms.base'`

- [ ] **Step 4: Implement `PlatformConfig`**

Create `bot/platforms/base.py`:

```python
"""PlatformConfig dataclass — data contract for each supported platform."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class PlatformConfig:
    """Declarative per-platform configuration.

    Platform modules under bot/platforms/ export exactly one of these as CONFIG.
    Integration sites read fields via bot.platforms.get_platform(name).
    """

    name: str
    display_name: str
    domains: tuple[str, ...]
    hide_flac: bool
    hide_time_range: bool
    is_podcast: bool
    media_label: str
    requires_cookies: bool
    cookies_hint: str | None = None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_platforms.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add bot/platforms/__init__.py bot/platforms/base.py tests/test_platforms.py
git commit -m "Add PlatformConfig dataclass for platform registry"
```

---

### Task 2: Per-platform CONFIG modules (bulk)

**Files:**
- Create: `bot/platforms/youtube.py`
- Create: `bot/platforms/vimeo.py`
- Create: `bot/platforms/tiktok.py`
- Create: `bot/platforms/linkedin.py`
- Create: `bot/platforms/x.py`
- Create: `bot/platforms/instagram.py`
- Create: `bot/platforms/castbox.py`
- Create: `bot/platforms/spotify.py`

These are pure data files with no logic. The registry in Task 3 will import them; the tests in Task 3 verify them.

- [ ] **Step 1: Create `bot/platforms/youtube.py`**

```python
"""YouTube platform config."""

from bot.platforms.base import PlatformConfig

CONFIG = PlatformConfig(
    name="youtube",
    display_name="YouTube",
    domains=("youtube.com", "youtu.be", "m.youtube.com", "music.youtube.com"),
    hide_flac=False,
    hide_time_range=False,
    is_podcast=False,
    media_label="filmie",
    requires_cookies=False,
    cookies_hint=(
        "YouTube czasem blokuje pobieranie komunikatem "
        "\"Sign in to confirm you're not a bot\". Zaloguj się na YouTube "
        "w przeglądarce i wyeksportuj cookies rozszerzeniem "
        "\"Get cookies.txt LOCALLY\"."
    ),
)
```

- [ ] **Step 2: Create `bot/platforms/vimeo.py`**

```python
"""Vimeo platform config."""

from bot.platforms.base import PlatformConfig

CONFIG = PlatformConfig(
    name="vimeo",
    display_name="Vimeo",
    domains=("vimeo.com", "player.vimeo.com"),
    hide_flac=False,
    hide_time_range=False,
    is_podcast=False,
    media_label="filmie",
    requires_cookies=False,
    cookies_hint=None,
)
```

- [ ] **Step 3: Create `bot/platforms/tiktok.py`**

```python
"""TikTok platform config."""

from bot.platforms.base import PlatformConfig

CONFIG = PlatformConfig(
    name="tiktok",
    display_name="TikTok",
    domains=("tiktok.com", "m.tiktok.com", "vm.tiktok.com"),
    hide_flac=True,
    hide_time_range=True,
    is_podcast=False,
    media_label="filmie",
    requires_cookies=True,
    cookies_hint=(
        "TikTok często wymaga zalogowania. Zaloguj się na tiktok.com "
        "w przeglądarce i wyeksportuj cookies rozszerzeniem "
        "\"Get cookies.txt LOCALLY\"."
    ),
)
```

- [ ] **Step 4: Create `bot/platforms/linkedin.py`**

```python
"""LinkedIn platform config."""

from bot.platforms.base import PlatformConfig

CONFIG = PlatformConfig(
    name="linkedin",
    display_name="LinkedIn",
    domains=("linkedin.com",),
    hide_flac=False,
    hide_time_range=False,
    is_podcast=False,
    media_label="filmie",
    requires_cookies=True,
    cookies_hint=(
        "LinkedIn wymaga zalogowania do pobierania video z postów. "
        "Zaloguj się na linkedin.com i wyeksportuj cookies rozszerzeniem "
        "\"Get cookies.txt LOCALLY\"."
    ),
)
```

- [ ] **Step 5: Create `bot/platforms/x.py`**

```python
"""X (Twitter) platform config."""

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
        "cookies rozszerzeniem \"Get cookies.txt LOCALLY\"."
    ),
)
```

- [ ] **Step 6: Create `bot/platforms/instagram.py`**

```python
"""Instagram platform config."""

from bot.platforms.base import PlatformConfig

CONFIG = PlatformConfig(
    name="instagram",
    display_name="Instagram",
    domains=("instagram.com",),
    hide_flac=False,
    hide_time_range=False,
    is_podcast=False,
    media_label="filmie",
    requires_cookies=True,
    cookies_hint=(
        "Instagram blokuje większość postów bez aktywnej sesji. "
        "Zaloguj się na instagram.com i wyeksportuj cookies rozszerzeniem "
        "\"Get cookies.txt LOCALLY\". Do zdjęć/karuzel potrzebny jest "
        "dodatkowo pakiet instaloader."
    ),
)
```

- [ ] **Step 7: Create `bot/platforms/castbox.py`**

```python
"""Castbox platform config."""

from bot.platforms.base import PlatformConfig

CONFIG = PlatformConfig(
    name="castbox",
    display_name="Castbox",
    domains=("castbox.fm",),
    hide_flac=True,
    hide_time_range=True,
    is_podcast=True,
    media_label="odcinku",
    requires_cookies=False,
    cookies_hint=None,
)
```

- [ ] **Step 8: Create `bot/platforms/spotify.py`**

```python
"""Spotify platform config."""

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

- [ ] **Step 9: Verify all modules import cleanly**

Run:
```bash
python -c "
from bot.platforms import youtube, vimeo, tiktok, linkedin, x, instagram, castbox, spotify
for m in (youtube, vimeo, tiktok, linkedin, x, instagram, castbox, spotify):
    print(f'{m.__name__}: {m.CONFIG.name}')
"
```
Expected: eight lines listing each module and its platform name.

- [ ] **Step 10: Commit**

```bash
git add bot/platforms/
git commit -m "Add per-platform CONFIG modules"
```

---

### Task 3: Registry (`bot/platforms/__init__.py`) with tests

**Files:**
- Modify: `bot/platforms/__init__.py`
- Modify: `tests/test_platforms.py`

- [ ] **Step 1: Append registry tests to `tests/test_platforms.py`**

Add to the bottom of the existing `tests/test_platforms.py`:

```python
# Registry tests start here


import bot.platforms as platforms_pkg


def test_platforms_registry_is_non_empty_tuple():
    assert isinstance(platforms_pkg.PLATFORMS, tuple)
    assert len(platforms_pkg.PLATFORMS) == 8


def test_all_platforms_have_unique_names():
    names = [p.name for p in platforms_pkg.PLATFORMS]
    assert len(names) == len(set(names))


def test_no_overlapping_domains_across_platforms():
    seen: dict[str, str] = {}
    for p in platforms_pkg.PLATFORMS:
        for domain in p.domains:
            assert domain not in seen, (
                f"Domain {domain!r} used by both "
                f"{seen.get(domain)!r} and {p.name!r}"
            )
            seen[domain] = p.name


@pytest.mark.parametrize(
    "host, expected_name",
    [
        ("youtube.com", "youtube"),
        ("youtu.be", "youtube"),
        ("m.youtube.com", "youtube"),
        ("music.youtube.com", "youtube"),
        ("www.youtube.com", "youtube"),
        ("vimeo.com", "vimeo"),
        ("www.vimeo.com", "vimeo"),
        ("player.vimeo.com", "vimeo"),
        ("tiktok.com", "tiktok"),
        ("www.tiktok.com", "tiktok"),
        ("m.tiktok.com", "tiktok"),
        ("vm.tiktok.com", "tiktok"),
        ("linkedin.com", "linkedin"),
        ("www.linkedin.com", "linkedin"),
        ("x.com", "x"),
        ("www.x.com", "x"),
        ("twitter.com", "x"),
        ("www.twitter.com", "x"),
        ("mobile.twitter.com", "x"),
        ("instagram.com", "instagram"),
        ("www.instagram.com", "instagram"),
        ("castbox.fm", "castbox"),
        ("www.castbox.fm", "castbox"),
        ("open.spotify.com", "spotify"),
    ],
)
def test_detect_by_domain_matches_known_hosts(host, expected_name):
    config = platforms_pkg.detect_by_domain(host)
    assert config is not None
    assert config.name == expected_name


def test_detect_by_domain_returns_none_for_unknown_host():
    assert platforms_pkg.detect_by_domain("example.com") is None
    assert platforms_pkg.detect_by_domain("") is None


def test_detect_by_domain_is_case_insensitive():
    assert platforms_pkg.detect_by_domain("X.COM").name == "x"
    assert platforms_pkg.detect_by_domain("WwW.YouTube.Com").name == "youtube"


def test_get_platform_returns_config_for_known_names():
    for p in platforms_pkg.PLATFORMS:
        assert platforms_pkg.get_platform(p.name) is p


def test_get_platform_returns_none_for_unknown():
    assert platforms_pkg.get_platform("facebook") is None
    assert platforms_pkg.get_platform("") is None


def test_all_domains_includes_www_variants_for_bare_domains():
    domains = platforms_pkg.all_domains()
    assert "x.com" in domains
    assert "www.x.com" in domains
    assert "mobile.twitter.com" in domains
    # Subdomain entries should NOT be www-expanded
    assert "www.mobile.twitter.com" not in domains
    assert "www.m.youtube.com" not in domains


def test_x_platform_has_tiktok_style_menu_flags():
    x_config = platforms_pkg.get_platform("x")
    assert x_config is not None
    assert x_config.hide_flac is True
    assert x_config.hide_time_range is True
    assert x_config.is_podcast is False


def test_cookies_hint_set_for_platforms_requiring_cookies_except_podcasts():
    for p in platforms_pkg.PLATFORMS:
        if p.is_podcast:
            assert p.cookies_hint is None, (
                f"Podcast platform {p.name!r} should not have cookies_hint"
            )
            continue
        if p.requires_cookies:
            assert p.cookies_hint is not None, (
                f"Platform {p.name!r} marked requires_cookies but has no hint"
            )
```

- [ ] **Step 2: Run registry tests to verify they fail**

Run: `pytest tests/test_platforms.py -v`
Expected: all registry tests FAIL (module `bot.platforms` has no `PLATFORMS` / `get_platform` / etc.)

- [ ] **Step 3: Implement the registry**

Replace content of `bot/platforms/__init__.py` with:

```python
"""Platform registry.

Single source of truth for supported platforms. Integration sites import
``PLATFORMS`` for iteration, ``get_platform`` for name-based lookup, and
``detect_by_domain`` / ``all_domains`` for URL-based lookup.

Platform modules under this package export a single ``CONFIG`` of type
:class:`PlatformConfig`. To add a platform:

1. Create ``bot/platforms/<name>.py`` exporting ``CONFIG``.
2. Add the module to the imports and the ``PLATFORMS`` tuple below.
"""

from __future__ import annotations

from bot.platforms import (
    castbox,
    instagram,
    linkedin,
    spotify,
    tiktok,
    vimeo,
    x,
    youtube,
)
from bot.platforms.base import PlatformConfig

__all__ = [
    "PlatformConfig",
    "PLATFORMS",
    "get_platform",
    "detect_by_domain",
    "all_domains",
]

PLATFORMS: tuple[PlatformConfig, ...] = (
    youtube.CONFIG,
    vimeo.CONFIG,
    tiktok.CONFIG,
    linkedin.CONFIG,
    x.CONFIG,
    instagram.CONFIG,
    castbox.CONFIG,
    spotify.CONFIG,
)


def _expand_with_www(domains: tuple[str, ...]) -> tuple[str, ...]:
    """Return domains plus www.-prefixed variants for non-subdomain entries.

    Mirrors the behavior previously inlined in bot/security_policy.py: a
    domain with exactly one dot (e.g. ``x.com``) also matches ``www.x.com``.
    Subdomain entries like ``mobile.twitter.com`` are not www-expanded.
    """

    expanded: list[str] = []
    for domain in domains:
        expanded.append(domain)
        if domain.count(".") == 1:
            expanded.append(f"www.{domain}")
    return tuple(expanded)


_BY_NAME: dict[str, PlatformConfig] = {p.name: p for p in PLATFORMS}
_BY_DOMAIN: dict[str, PlatformConfig] = {
    domain: p
    for p in PLATFORMS
    for domain in _expand_with_www(p.domains)
}
_ALL_DOMAINS: frozenset[str] = frozenset(_BY_DOMAIN.keys())


def get_platform(name: str | None) -> PlatformConfig | None:
    """Return the PlatformConfig matching ``name`` or None."""

    if not name:
        return None
    return _BY_NAME.get(name)


def detect_by_domain(host: str | None) -> PlatformConfig | None:
    """Return the PlatformConfig matching ``host`` (case-insensitive) or None."""

    if not host:
        return None
    return _BY_DOMAIN.get(host.lower())


def all_domains() -> frozenset[str]:
    """Return the full set of supported domains (including www. variants)."""

    return _ALL_DOMAINS
```

- [ ] **Step 4: Run all platform tests**

Run: `pytest tests/test_platforms.py -v`
Expected: all tests pass (data class tests from Task 1 + registry tests from Task 3)

- [ ] **Step 5: Commit**

```bash
git add bot/platforms/__init__.py tests/test_platforms.py
git commit -m "Add platform registry with name and domain lookups"
```

---

### Task 4: Refactor `bot/security_policy.py` to use the registry

**Files:**
- Modify: `bot/security_policy.py`
- Test: `tests/test_security_policy.py` (should still pass, not modified in this task)

- [ ] **Step 1: Run the existing security_policy tests (baseline)**

Run: `pytest tests/test_security_policy.py -v`
Expected: all tests pass (this is the pre-refactor baseline)

- [ ] **Step 2: Replace the domain map and helpers with registry delegation**

In `bot/security_policy.py`, replace the block from line 12 to line 31 (the
`_DOMAIN_TO_PLATFORM` dict and `ALLOWED_DOMAINS` sorted set) with:

```python
from bot.platforms import all_domains, detect_by_domain, get_platform

# Kept as a module attribute for backward compatibility with external code
# (tests, downstream imports). Prefer bot.platforms.all_domains() in new code.
ALLOWED_DOMAINS = sorted(all_domains())
```

- [ ] **Step 3: Refactor `get_media_label` (lines 77-82)**

Replace the existing function body with:

```python
def get_media_label(platform: str | None) -> str:
    """Return Polish locative noun for media type."""

    config = get_platform(platform)
    if config is None:
        return "filmie"
    return config.media_label
```

- [ ] **Step 4: Refactor `detect_platform` (lines 138-146)**

Replace the existing function body with:

```python
def detect_platform(url) -> str | None:
    """Detect source platform name from URL."""

    domain = _normalize_domain(url)
    if domain is None:
        return None

    config = detect_by_domain(domain)
    if config is None and domain.startswith("www."):
        config = detect_by_domain(domain[4:])
    return config.name if config else None
```

- [ ] **Step 5: Run security_policy tests**

Run: `pytest tests/test_security_policy.py -v`
Expected: all tests still pass

- [ ] **Step 6: Run the full test suite to catch regressions elsewhere**

Run: `pytest -x`
Expected: all tests pass (X is not yet integrated into UI sites; we are only testing the registry plumbing)

- [ ] **Step 7: Commit**

```bash
git add bot/security_policy.py
git commit -m "Delegate platform detection to bot/platforms registry"
```

---

### Task 5: Refactor `bot/handlers/common_ui.py`

**Files:**
- Modify: `bot/handlers/common_ui.py`

- [ ] **Step 1: Add a test for the refactor behavior**

Append to `tests/test_platforms.py`:

```python
def test_build_main_keyboard_reads_flags_from_registry():
    from bot.handlers.common_ui import build_main_keyboard

    # TikTok → hide FLAC and time-range
    tiktok_keyboard = build_main_keyboard("tiktok")
    flat_callbacks = [
        btn.callback_data
        for row in tiktok_keyboard
        for btn in row
    ]
    assert "dl_audio_flac" not in flat_callbacks
    assert "time_range" not in flat_callbacks

    # YouTube → FLAC and time-range present
    yt_keyboard = build_main_keyboard("youtube")
    flat_callbacks = [
        btn.callback_data
        for row in yt_keyboard
        for btn in row
    ]
    assert "dl_audio_flac" in flat_callbacks
    assert "time_range" in flat_callbacks

    # X → same shape as TikTok
    x_keyboard = build_main_keyboard("x")
    flat_callbacks = [
        btn.callback_data
        for row in x_keyboard
        for btn in row
    ]
    assert "dl_audio_flac" not in flat_callbacks
    assert "time_range" not in flat_callbacks


def test_build_main_keyboard_raises_on_unknown_platform():
    from bot.handlers.common_ui import build_main_keyboard

    with pytest.raises(ValueError, match="Unknown platform"):
        build_main_keyboard("facebook")
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `pytest tests/test_platforms.py -v -k "build_main_keyboard"`
Expected: `test_build_main_keyboard_raises_on_unknown_platform` FAILS (current
code returns a keyboard for any string); the TikTok/YouTube checks likely pass
by coincidence but will confirm the refactor preserves behavior.

- [ ] **Step 3: Refactor `build_main_keyboard`**

In `bot/handlers/common_ui.py`, replace lines 18-23 with:

```python
def build_main_keyboard(platform: str, large_file: bool = False) -> list:
    """Build the main format selection keyboard for a detected platform."""

    from bot.platforms import get_platform

    config = get_platform(platform)
    if config is None:
        raise ValueError(f"Unknown platform in session: {platform!r}")
    is_podcast = config.is_podcast
    hide_flac = config.hide_flac
    hide_time_range = config.hide_time_range
```

(The rest of the function body — `if is_podcast:` onwards — stays unchanged.)

- [ ] **Step 4: Run the new tests**

Run: `pytest tests/test_platforms.py -v -k "build_main_keyboard"`
Expected: both tests pass

- [ ] **Step 5: Run the full test suite**

Run: `pytest -x`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add bot/handlers/common_ui.py tests/test_platforms.py
git commit -m "Read keyboard flags from platform registry"
```

---

### Task 6: Refactor `bot/handlers/inbound_media.py` supported-platforms message

**Files:**
- Modify: `bot/handlers/inbound_media.py`

- [ ] **Step 1: Replace the hardcoded list with a registry-driven formatter**

In `bot/handlers/inbound_media.py`, replace lines 264-275 (the `if not validate_url(...)` block with its `reply_text(...)` containing the hardcoded list) with:

```python
    if not validate_url(message_text):
        from bot.platforms import PLATFORMS

        platform_lines = "\n".join(
            f"- {p.display_name} ({p.domains[0]})" for p in PLATFORMS
        )
        await update.message.reply_text(
            "Nieprawidłowy URL!\n\n"
            "Obsługiwane platformy:\n"
            f"{platform_lines}"
        )
        return
```

- [ ] **Step 2: Verify smoke test**

Run:
```bash
python -c "
from bot.platforms import PLATFORMS
for p in PLATFORMS:
    print(f'- {p.display_name} ({p.domains[0]})')
"
```
Expected output includes `- X (x.com)` among 8 lines.

- [ ] **Step 3: Run relevant test files**

Run: `pytest tests/test_inbound_media_handlers.py -v`
Expected: all pass (the existing test only checks for "Nieprawidłowy URL"
substring, which is preserved).

- [ ] **Step 4: Commit**

```bash
git add bot/handlers/inbound_media.py
git commit -m "Generate supported-platforms message from registry"
```

---

### Task 7: Refactor `bot/handlers/command_access.py`

**Files:**
- Modify: `bot/handlers/command_access.py`

- [ ] **Step 1: Inspect the current `/help` message**

Run: `grep -n "Obsługiwane platformy\|TikTok, Instagram" bot/handlers/command_access.py`
Expected: matches around lines 220 (the `/help` platform list) and 229 (the
"TikTok, Instagram i LinkedIn" cookies hint) and 277 (the `/status` cookies
hint).

- [ ] **Step 2: Add the helper functions at module top (after imports)**

Find the import block at the top of `bot/handlers/command_access.py` and add immediately after it:

```python
from bot.platforms import PLATFORMS


def _format_supported_platforms_block() -> str:
    """Return a newline-joined bullet list of platforms for help messages."""

    return "\n".join(f"- {p.display_name} ({p.domains[0]})" for p in PLATFORMS)


def _format_cookies_required_names() -> str:
    """Return a comma-separated list of platforms that typically need cookies.txt."""

    names = [p.display_name for p in PLATFORMS if p.requires_cookies]
    return ", ".join(names)
```

- [ ] **Step 3: Replace the `/help` platform list and cookies hint**

In `bot/handlers/command_access.py`, replace the block from line 220 to 230
(the hardcoded platform bullets + "TikTok, Instagram i LinkedIn" hint) with:

```python
        "🌐 *Obsługiwane platformy:*\n"
        f"{_format_supported_platforms_block()}\n\n"
        "🔒 *Platformy wymagające logowania:*\n"
        f"{_format_cookies_required_names()} mogą wymagać pliku cookies.txt\n"
        "do pobierania treści z ograniczonym dostępem.\n\n"
        "Komendy administracyjne:\n"
        "- /status - sprawdź przestrzeń dyskową\n"
        "- /cleanup - usuń stare pliki (>24h)",
```

- [ ] **Step 4: Replace the `/status` cookies hint**

In the same file, replace line 277:

```python
        status_msg += f"\n**cookies.txt:** ❌ brak ({_format_cookies_required_names()} mogą wymagać)\n"
```

- [ ] **Step 5: Smoke test**

Run:
```bash
python -c "
from bot.handlers.command_access import _format_supported_platforms_block, _format_cookies_required_names
print(_format_supported_platforms_block())
print('---')
print(_format_cookies_required_names())
"
```
Expected: 8-line bullet list including X; cookies list includes YouTube,
TikTok, LinkedIn, X, Instagram.

- [ ] **Step 6: Run command_access tests**

Run: `pytest tests/test_command_access_handlers.py -v`
Expected: all tests pass (or fail only on hardcoded 7-platform list
assertions, which Task 11 will address).

- [ ] **Step 7: Commit**

```bash
git add bot/handlers/command_access.py
git commit -m "Generate /help and /status platform lists from registry"
```

---

### Task 8: Refactor `bot/services/auth_service.py`

**Files:**
- Modify: `bot/services/auth_service.py`

- [ ] **Step 1: Locate the two hardcoded strings**

Run: `grep -n "YouTube, Vimeo, TikTok" bot/services/auth_service.py`
Expected: matches around lines 103 and 181.

- [ ] **Step 2: Add a helper near the top of `auth_service.py` (after existing imports)**

```python
from bot.platforms import PLATFORMS


def _platforms_inline_list() -> str:
    """Return comma-separated display names for inline sentences."""

    return ", ".join(p.display_name for p in PLATFORMS)
```

- [ ] **Step 3: Replace both hardcoded strings**

Line 103:
```python
                f"Jesteś już zalogowany. Wyślij link ({_platforms_inline_list()}) "
```

Line 181:
```python
                f"Wyślij link ({_platforms_inline_list()}) "
```

- [ ] **Step 4: Smoke test**

Run:
```bash
python -c "from bot.services.auth_service import _platforms_inline_list; print(_platforms_inline_list())"
```
Expected: `YouTube, Vimeo, TikTok, LinkedIn, X, Instagram, Castbox, Spotify`

- [ ] **Step 5: Run auth_service tests**

Run: `pytest tests/test_auth_service.py -v`
Expected: all tests pass (or fail only on hardcoded 7-platform list
assertions, which Task 11 will address).

- [ ] **Step 6: Commit**

```bash
git add bot/services/auth_service.py
git commit -m "Generate auth-flow platform lists from registry"
```

---

### Task 9: Per-platform `cookies_hint` in `bot/handlers/download_callbacks.py`

**Files:**
- Modify: `bot/handlers/download_callbacks.py`

- [ ] **Step 1: Locate the auth-error block**

Run: `grep -n "Ta platforma wymaga zalogowania\|login.*sign in.*cookie" bot/handlers/download_callbacks.py`
Expected: matches around lines 589-597.

- [ ] **Step 2: Extract the existing message into a module-level constant**

At the top of `bot/handlers/download_callbacks.py`, ensure these imports are
present (add if missing):

```python
from bot.platforms import get_platform
from bot.session_context import get_session_context_value as _get_session_context_value
```

(The file already imports `_get_session_context_value` — verify with
`grep -n "_get_session_context_value" bot/handlers/download_callbacks.py`.
Only add the `get_platform` import.)

Then add a module-level constant near the top of the file:

```python
GENERIC_COOKIES_HINT = (
    "Ta platforma wymaga zalogowania.\n\n"
    "Aby pobrać treści z ograniczonym dostępem:\n"
    "1. Zaloguj się na platformę w przeglądarce\n"
    "2. Wyeksportuj cookies (rozszerzenie 'Get cookies.txt LOCALLY')\n"
    "3. Umieść plik cookies.txt w katalogu bota\n"
    "4. Spróbuj ponownie"
)
```

- [ ] **Step 3: Replace the keyword-match block (lines 588-597)**

Replace:
```python
        error_str = str(exc).lower()
        if any(keyword in error_str for keyword in ("login", "sign in", "cookie", "authentication")):
            await update_status(
                "Ta platforma wymaga zalogowania.\n\n"
                "Aby pobrać treści z ograniczonym dostępem:\n"
                "1. Zaloguj się na platformę w przeglądarce\n"
                "2. Wyeksportuj cookies (rozszerzenie 'Get cookies.txt LOCALLY')\n"
                "3. Umieść plik cookies.txt w katalogu bota\n"
                "4. Spróbuj ponownie"
            )
        else:
            await update_status("Wystąpił błąd podczas pobierania. Spróbuj ponownie.")
```

With:
```python
        error_str = str(exc).lower()
        if any(keyword in error_str for keyword in ("login", "sign in", "cookie", "authentication")):
            platform_name = _get_session_context_value(
                context, chat_id, "platform", legacy_key="platform"
            )
            config = get_platform(platform_name)
            hint = (
                config.cookies_hint
                if config and config.cookies_hint
                else GENERIC_COOKIES_HINT
            )
            await update_status(hint)
        else:
            await update_status("Wystąpił błąd podczas pobierania. Spróbuj ponownie.")
```

(The `chat_id` variable is already in scope from the surrounding download
handler — the same handler earlier calls `record_download_for(context, chat_id, ...)`.)

- [ ] **Step 4: Add tests for the new behavior**

Append to `tests/test_platforms.py`:

```python
def test_download_callbacks_has_generic_cookies_hint_constant():
    """The generic fallback hint must stay available for unknown platforms."""

    from bot.handlers.download_callbacks import GENERIC_COOKIES_HINT

    assert isinstance(GENERIC_COOKIES_HINT, str)
    assert "cookies.txt" in GENERIC_COOKIES_HINT


def test_x_platform_cookies_hint_mentions_x():
    """Regression: X's per-platform hint should reference X-specific guidance."""

    config = platforms_pkg.get_platform("x")
    assert config is not None
    assert config.cookies_hint is not None
    lowered = config.cookies_hint.lower()
    assert "x.com" in lowered or "sensitive" in lowered


def test_get_platform_fallback_behavior_for_auth_error_path():
    """get_platform(None) returns None so the caller falls back to GENERIC."""

    assert platforms_pkg.get_platform(None) is None
    assert platforms_pkg.get_platform("unknown-platform") is None
```

- [ ] **Step 5: Run the new tests**

Run: `pytest tests/test_platforms.py -v -k "cookies_hint or generic_cookies or fallback"`
Expected: 3 passed

- [ ] **Step 6: Run the full download_callbacks test file**

Run: `pytest tests/test_callback_download_handlers.py -v`
Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add bot/handlers/download_callbacks.py tests/test_platforms.py
git commit -m "Surface per-platform cookies hint on auth errors"
```

---

### Task 10: Extend `tests/test_security_policy.py` for X

**Files:**
- Modify: `tests/test_security_policy.py`

- [ ] **Step 1: Add X cases to existing tests**

In `tests/test_security_policy.py`, extend the existing tests:

Replace `test_validate_url_accepts_supported_https_domains` with:

```python
def test_validate_url_accepts_supported_https_domains():
    assert validate_url("https://www.youtube.com/watch?v=abc") is True
    assert validate_url("https://open.spotify.com/episode/abc") is True
    assert validate_url("https://castbox.fm/episode/test") is True
    assert validate_url("https://x.com/i/status/123456789") is True
    assert validate_url("https://twitter.com/user/status/123456789") is True
    assert validate_url("https://mobile.twitter.com/user/status/123") is True
```

Replace `test_detect_platform_maps_supported_domains` with:

```python
def test_detect_platform_maps_supported_domains():
    assert detect_platform("https://youtu.be/abc") == "youtube"
    assert detect_platform("https://www.instagram.com/reel/abc") == "instagram"
    assert detect_platform("https://open.spotify.com/episode/abc") == "spotify"
    assert detect_platform("https://x.com/i/status/123") == "x"
    assert detect_platform("https://www.x.com/i/status/123") == "x"
    assert detect_platform("https://twitter.com/u/status/123") == "x"
    assert detect_platform("https://mobile.twitter.com/u/status/123") == "x"
```

- [ ] **Step 2: Run the tests**

Run: `pytest tests/test_security_policy.py -v`
Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add tests/test_security_policy.py
git commit -m "Cover X domains in security_policy tests"
```

---

### Task 11: Update hardcoded 7-platform assertions in existing tests

**Files:**
- Modify: `tests/test_command_access_handlers.py`
- Modify: `tests/test_auth_service.py` (if it asserts platform list strings)

- [ ] **Step 1: Find hardcoded 7-platform strings in tests**

Run: `grep -rn "YouTube, Vimeo, TikTok, Instagram, LinkedIn, Castbox, Spotify" tests/`
Expected: matches in `tests/test_command_access_handlers.py` around lines 43
and 86; possibly `tests/test_auth_service.py`.

- [ ] **Step 2: Update each assertion**

For every match, change the assertion pattern from exact substring equality
to a looser check that tolerates additional platforms. Example — if the
test currently has:

```python
assert "Wyślij link (YouTube, Vimeo, TikTok, Instagram, LinkedIn, Castbox, Spotify) aby pobrać" in message
```

Change to:

```python
assert "Wyślij link (" in message
assert "YouTube" in message
assert "X" in message
assert ") aby pobrać" in message
```

Apply the same transformation to every match found in Step 1.

- [ ] **Step 3: Run the affected tests**

Run:
```bash
pytest tests/test_command_access_handlers.py tests/test_auth_service.py -v
```
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add tests/test_command_access_handlers.py tests/test_auth_service.py
git commit -m "Relax test assertions for registry-driven platform lists"
```

---

### Task 12: Update README supported-platforms table

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Locate the supported-platforms table**

Run: `grep -n "Obsługiwane platformy\|| Platforma" README.md | head -5`
Expected: matches around line 22.

- [ ] **Step 2: Add an X row**

In the `### Obsługiwane platformy` table (around line 22), add a new row for
X. Order-wise, place it after LinkedIn so the table matches `PLATFORMS` tuple
order from the registry:

```markdown
| X (Twitter) | x.com, twitter.com, mobile.twitter.com | Video z tweetów. Treści oznaczone jako Sensitive wymagają cookies.txt |
```

Also update the introductory paragraph on line 3 if it enumerates supported
platforms by name, adding "X" alongside "YouTube, Vimeo, TikTok, Instagram,
LinkedIn".

- [ ] **Step 3: Visual diff check**

Run: `git diff README.md`
Expected: only the platform table (and possibly the intro sentence) changed.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "Document X platform support in README"
```

---

### Task 13: Full test suite + smoke test

**Files:** none modified

- [ ] **Step 1: Run the complete test suite**

Run: `source venv/bin/activate && pytest`
Expected: every test passes, no skips attributable to this work.

- [ ] **Step 2: Import-time smoke test**

Run:
```bash
python -c "
from bot.platforms import PLATFORMS, get_platform, detect_by_domain, all_domains
assert len(PLATFORMS) == 8
assert get_platform('x').name == 'x'
assert detect_by_domain('x.com').name == 'x'
assert detect_by_domain('www.twitter.com').name == 'x'
assert 'x.com' in all_domains()
print('Registry smoke test OK')
"
```
Expected: `Registry smoke test OK`

- [ ] **Step 3: Manual CLI smoke test against X**

Run (requires network):
```bash
source venv/bin/activate
python main.py --cli --url "https://x.com/i/status/2047124368623534362" --list-formats
```
Expected: either a list of formats from yt-dlp (public video), or a clean
error message (if tweet requires auth). **Not expected:** Python traceback
from registry lookup.

- [ ] **Step 4: (Optional) Manual bot smoke test**

Start the bot (`python main.py`) and in Telegram send:
```
https://x.com/i/status/2047124368623534362
```
Expected: menu appears with Video/Audio buttons, NO FLAC, NO time-range
button; selecting Video → MP4 download; on auth error, the message mentions
"Sensitive" and "x.com".

- [ ] **Step 5: Final commit if any cleanup was needed**

```bash
git status
# If clean, nothing to do.
# If there are fixups, commit them with a descriptive message.
```

---

## Rollback Notes

If the refactor breaks an existing platform flow in production, rollback is
safe at any task boundary — commits are sequential and each task leaves the
codebase in a working state. The risk points are Tasks 4 (security_policy) and
5 (common_ui) which change the dispatch path; Tasks 6-8 only change UI text;
Task 9 only affects error-handling text. If needed, revert the specific task's
commit rather than the whole series.

## Out-of-Scope Reminders

Do not, during this implementation:

- Move Instagram photo/carousel handling into `bot/platforms/instagram.py` (that's Phase 2).
- Move Spotify/Castbox iTunes resolution (Phase 3).
- Add time-range support for X (explicit design decision — mirror TikTok).
- Pre-flight cookies check for X links (explicit design decision — lazy check).
- Change session schema — platform stays as string name in session.
