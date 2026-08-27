"""
Unit tests for the cookie export filter.

Browser extensions export the whole profile, which drags unrelated session
tokens (banking, gov, shopping) into a file that lives next to the code. The
filter keeps only what yt-dlp needs and flags exports that are missing the
cookies required for an authenticated YouTube session.
"""

from scripts.filter_cookies import (
    REQUIRED_COOKIES,
    filter_cookie_lines,
    missing_required_cookies,
)


def _line(domain, name, value="v", expiry="1900000000", secure="TRUE"):
    return f"{domain}\tTRUE\t/\t{secure}\t{expiry}\t{name}\t{value}"


def test_keeps_youtube_cookies():
    kept = filter_cookie_lines([_line(".youtube.com", "SID")])

    assert len(kept) == 1
    assert "SID" in kept[0]


def test_drops_unrelated_domains():
    lines = [_line(".youtube.com", "SID"), _line("etoll.gov.pl", "SESSION")]

    kept = filter_cookie_lines(lines)

    assert len(kept) == 1
    assert "etoll" not in kept[0]


def test_keeps_google_domains_for_shared_auth():
    kept = filter_cookie_lines([_line(".google.com", "SAPISID")])

    assert len(kept) == 1


def test_removes_duplicate_entries():
    duplicate = _line(".youtube.com", "SID")

    kept = filter_cookie_lines([duplicate, duplicate])

    assert kept == [duplicate]


def test_normalises_space_separated_input_to_tabs():
    spaced = ".youtube.com TRUE / TRUE 1900000000 SID abc"

    kept = filter_cookie_lines([spaced])

    assert kept == [".youtube.com\tTRUE\t/\tTRUE\t1900000000\tSID\tabc"]


def test_skips_comments_and_blank_lines():
    lines = ["# Netscape HTTP Cookie File", "", "   ", _line(".youtube.com", "SID")]

    assert len(filter_cookie_lines(lines)) == 1


def test_skips_malformed_lines():
    assert filter_cookie_lines([".youtube.com\tTRUE\tonly-three"]) == []


def test_reports_missing_login_cookies():
    missing = missing_required_cookies([_line(".youtube.com", "SID")])

    assert "SOCS" in missing
    assert "LOGIN_INFO" in missing
    assert "SID" not in missing


def test_reports_nothing_when_export_is_complete():
    lines = [_line(".youtube.com", name) for name in REQUIRED_COOKIES]

    assert missing_required_cookies(lines) == []
