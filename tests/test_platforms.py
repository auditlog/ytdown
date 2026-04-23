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
