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
