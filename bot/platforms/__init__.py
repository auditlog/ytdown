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
