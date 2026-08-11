#!/usr/bin/env python3
"""Reduce a browser cookie export to just what yt-dlp needs.

Cookie extensions export the entire browser profile. Dropping that file next to
the code puts live session tokens for unrelated sites (banking, government,
shopping) one `git add -A` away from a commit, so this filter keeps only the
Google/YouTube entries and reports whether the export can actually authenticate.

Usage:
    python scripts/filter_cookies.py <export.txt> [-o cookies.txt]

Exits non-zero when the export lacks cookies required for a logged-in session,
so a bad export fails loudly instead of silently producing age-gate errors.
See also: bot/config.py (COOKIES_FILE) for where the bot reads the result.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Iterable

# google.com is kept alongside youtube.com because the login cookies are issued
# on both domains and YouTube's API auth relies on the shared SAPISID.
DEFAULT_DOMAINS: tuple[str, ...] = ("youtube.com", "google.com")

# Netscape cookie format: domain, include_subdomains, path, secure, expiry, name, value
_FIELD_COUNT = 7

# Without every one of these YouTube treats the session as anonymous. SOCS is the
# GDPR consent cookie: omit it and YouTube serves the consent wall instead of the
# real page, which looks identical to being logged out.
REQUIRED_COOKIES: tuple[str, ...] = (
    "SID",
    "HSID",
    "SSID",
    "APISID",
    "SAPISID",
    "LOGIN_INFO",
    "SOCS",
)


def _parse(line: str) -> list[str] | None:
    """Split one cookie line into its 7 fields, or None if it is not a cookie."""

    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    fields = stripped.split()
    return fields if len(fields) == _FIELD_COUNT else None


def _domain_allowed(domain: str, domains: Iterable[str]) -> bool:
    host = domain.lstrip(".").lower()
    return any(host == d or host.endswith(f".{d}") for d in domains)


def filter_cookie_lines(
    lines: Iterable[str], domains: tuple[str, ...] = DEFAULT_DOMAINS
) -> list[str]:
    """Return deduplicated, tab-normalised cookie lines for the allowed domains."""

    kept: list[str] = []
    seen: set[str] = set()
    for line in lines:
        fields = _parse(line)
        if fields is None or not _domain_allowed(fields[0], domains):
            continue
        normalised = "\t".join(fields)
        if normalised not in seen:
            seen.add(normalised)
            kept.append(normalised)
    return kept


def missing_required_cookies(lines: Iterable[str]) -> list[str]:
    """Return the required cookie names absent from the export, in canonical order."""

    present = {fields[5] for line in lines if (fields := _parse(line)) is not None}
    return [name for name in REQUIRED_COOKIES if name not in present]


def _dropped_domains(lines: Iterable[str], domains: tuple[str, ...]) -> set[str]:
    return {
        fields[0]
        for line in lines
        if (fields := _parse(line)) is not None and not _domain_allowed(fields[0], domains)
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("source", help="cookie export produced by a browser extension")
    parser.add_argument(
        "-o", "--output", default="cookies.txt", help="destination file (default: cookies.txt)"
    )
    args = parser.parse_args(argv)

    with open(args.source, encoding="utf-8") as handle:
        raw = handle.readlines()

    kept = filter_cookie_lines(raw)
    dropped = _dropped_domains(raw, DEFAULT_DOMAINS)
    missing = missing_required_cookies(kept)

    if not kept:
        print(f"BŁĄD: {args.source} nie zawiera żadnych ciasteczek Google/YouTube.")
        return 1

    # Never silently destroy a working cookie file - it may be the only copy.
    try:
        shutil.copyfile(args.output, f"{args.output}.bak")
        print(f"Kopia zapasowa: {args.output}.bak")
    except FileNotFoundError:
        pass

    with open(args.output, "w", encoding="utf-8") as handle:
        handle.write("# Netscape HTTP Cookie File\n")
        handle.write("\n".join(kept) + "\n")

    print(f"Zapisano {len(kept)} ciasteczek do {args.output}")
    if dropped:
        print(f"Odrzucono {len(dropped)} obcych domen (m.in. {', '.join(sorted(dropped)[:3])})")

    if missing:
        print(f"\nUWAGA: brakuje ciasteczek wymaganych do zalogowanej sesji: {', '.join(missing)}")
        print("Eksportuj ponownie w oknie prywatnym i zamknij je BEZ wylogowania.")
        return 1

    print("Komplet ciasteczek zalogowanej sesji obecny.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
