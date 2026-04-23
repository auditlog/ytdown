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
