"""Tests for the TOK FM yt-dlp plugin (yt_dlp_plugins/extractor/tokfm.py).

TOK FM's HTTP endpoints are replaced by FakeTokFM, which also records every
request, so the tests can prove that a premium episode without a valid session
never reaches the /gets endpoint (the anti-goal in
docs/superpowers/specs/2026-11-18-tokfm-podcast-design.md).
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yt_dlp
from yt_dlp.utils import ExtractorError

from yt_dlp_plugins.extractor.tokfm import (
    NON_EPISODE_ERROR,
    SESSION_ERROR,
    TokFMIE,
    TokFMNonEpisodeIE,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
EPISODE_URL = "https://audycje.tokfm.pl/podcast/200878,Antrophic-Nvidia-i-Microsoft"
AUDIO_URL = "https://storage.tuba.fm/podcast/tok/abc/00/00/20/08/78.mp3"
ACCESS_URL = "https://audycje.tokfm.pl/premium/blokada?podcast=200878"
TITLE = "Dłuższa rozmowa - Antrophic, Nvidia i Microsoft"


def _player_tag(episode_id, title, *, free=None, premium=None):
    attrs = [
        f'data-id="{episode_id}"',
        f'data-podcast-title="{title}"',
        "data-leader=\"'Jakub Janiszewski'\"",
        "data-guests=\"'Gość Pierwszy', 'Gość Drugi'\"",
    ]
    if free is not None:
        attrs.append(f'data-podcast_free="{free}"')
    if premium is not None:
        attrs.append(f'data-only_for_premium="{premium}"')
    return f'<button class="tok-js__play" {" ".join(attrs)}>'


def _page(*tags):
    return (
        '<html><head><meta property="og:image" content="https://static.radioagora.pl/p.jpg">'
        "</head><body>" + "".join(tags) + "</body></html>"
    )


class FakeTokFM:
    """Stand-in for TOK FM's HTTP endpoints that records what the extractor asked for."""

    def __init__(self, page, *, access_status=204, gets=None, content_length="20287696"):
        self.page = page
        self.access_status = access_status
        self.gets = {"url": AUDIO_URL, "tok": "tokfm", "start_time": 0} if gets is None else gets
        self.content_length = content_length
        self.calls = []

    def install(self, ie, monkeypatch):
        monkeypatch.setattr(ie, "_download_webpage", self._download_webpage)
        monkeypatch.setattr(ie, "_request_webpage", self._request_webpage)
        monkeypatch.setattr(ie, "_download_json", self._download_json)
        return self

    def kinds(self):
        return [call[0] for call in self.calls]

    def _download_webpage(self, url_or_request, video_id, *args, **kwargs):
        self.calls.append(("page", url_or_request))
        return self.page

    def _request_webpage(self, url_or_request, video_id, *args, **kwargs):
        if getattr(url_or_request, "method", None) == "HEAD":
            self.calls.append(("head", url_or_request.url))
            if self.content_length is None:
                return False
            return SimpleNamespace(status=200, headers={"Content-Length": self.content_length})
        self.calls.append(("access", url_or_request))
        return SimpleNamespace(status=self.access_status, headers={})

    def _download_json(self, url_or_request, video_id, *args, **kwargs):
        self.calls.append(("gets", url_or_request, kwargs.get("data"), kwargs.get("headers")))
        return self.gets


@pytest.fixture
def ydl():
    return yt_dlp.YoutubeDL({"quiet": True})


def _extract(ydl, monkeypatch, fake, url=EPISODE_URL):
    ie = TokFMIE(ydl)
    fake.install(ie, monkeypatch)
    return ie.extract(url)


@pytest.mark.parametrize(
    "url",
    [
        EPISODE_URL,
        "https://audycje.tokfm.pl/podcast/200878",
        "http://audycje.tokfm.pl/podcast/200878,Slug?utm_source=x",
    ],
)
def test_episode_links_match_tokfm_ie(url):
    assert TokFMIE.suitable(url)
    assert not TokFMNonEpisodeIE.suitable(url)
    assert TokFMIE.get_temp_id(url) == "200878"


@pytest.mark.parametrize(
    "url",
    [
        "https://audycje.tokfm.pl/audycja/687,Dluzsza-rozmowa",
        "https://audycje.tokfm.pl/player/v2/200878",
        "https://audycje.tokfm.pl/",
        "https://audycje.tokfm.pl",
    ],
)
def test_non_episode_links_match_only_non_episode_ie(url):
    assert TokFMNonEpisodeIE.suitable(url)
    assert not TokFMIE.suitable(url)


def test_premium_episode_with_session_returns_single_mp3_format(ydl, monkeypatch):
    fake = FakeTokFM(_page(_player_tag("200878", TITLE, free="false", premium="true")))

    info = _extract(ydl, monkeypatch, fake)

    assert info["id"] == "200878"
    assert info["title"] == TITLE
    assert info["creators"] == ["Jakub Janiszewski"]
    assert info["cast"] == ["Gość Pierwszy", "Gość Drugi"]
    assert info["thumbnail"] == "https://static.radioagora.pl/p.jpg"
    assert info["formats"] == [
        {
            "format_id": "mp3",
            "url": AUDIO_URL,
            "ext": "mp3",
            "acodec": "mp3",
            "vcodec": "none",
            "abr": 64,
            "filesize": 20287696,
        }
    ]
    # 20287696 bytes of 64 kb/s CBR MP3 = 2535.96 s.
    assert info["duration"] == 2536
    assert ("access", ACCESS_URL) in fake.calls
    gets_call = next(call for call in fake.calls if call[0] == "gets")
    _, gets_url, gets_data, gets_headers = gets_call
    assert gets_url == "https://audycje.tokfm.pl/gets"
    assert json.loads(gets_data) == {"pid": 200878, "st": "tokfm"}
    assert gets_headers["X-Tuba"] == "tokfm"


def test_premium_episode_without_session_fails_before_gets(ydl, monkeypatch):
    fake = FakeTokFM(
        _page(_player_tag("200878", TITLE, free="false", premium="true")), access_status=200
    )

    with pytest.raises(ExtractorError) as excinfo:
        _extract(ydl, monkeypatch, fake)

    assert SESSION_ERROR in str(excinfo.value)
    assert "gets" not in fake.kinds()


def test_session_error_mentions_cookies_for_download_hint():
    # bot/handlers/download_callbacks.py shows the platform cookies hint only when
    # the error text contains "cookie".
    assert "cookie" in SESSION_ERROR.lower()


def test_free_episode_skips_access_check(ydl, monkeypatch):
    fake = FakeTokFM(_page(_player_tag("200878", TITLE, free="true", premium="false")))

    info = _extract(ydl, monkeypatch, fake)

    assert "access" not in fake.kinds()
    assert "gets" in fake.kinds()
    assert info["formats"][0]["url"] == AUDIO_URL


def test_episode_without_premium_flags_is_treated_as_premium(ydl, monkeypatch):
    fake = FakeTokFM(_page(_player_tag("200878", TITLE)), access_status=200)

    with pytest.raises(ExtractorError) as excinfo:
        _extract(ydl, monkeypatch, fake)

    assert SESSION_ERROR in str(excinfo.value)
    assert "gets" not in fake.kinds()


def test_player_tag_is_matched_by_episode_id(ydl, monkeypatch):
    fake = FakeTokFM(
        _page(
            _player_tag("199999", "Inny odcinek", free="true", premium="false"),
            _player_tag(
                "200878", "Rozmowa &quot;o IT&quot; &amp; chmura", free="false", premium="true"
            ),
        )
    )

    info = _extract(ydl, monkeypatch, fake)

    assert info["title"] == 'Rozmowa "o IT" & chmura'
    assert ("access", ACCESS_URL) in fake.calls


def test_missing_content_length_leaves_size_and_duration_unknown(ydl, monkeypatch):
    from bot.security_policy import estimate_file_size

    fake = FakeTokFM(
        _page(_player_tag("200878", TITLE, free="false", premium="true")), content_length=None
    )

    info = _extract(ydl, monkeypatch, fake)

    assert info["duration"] is None
    assert info["formats"][0].get("filesize") is None
    # Without size or duration the bot must not invent a multi-GB estimate.
    assert estimate_file_size(info) is None


def test_gets_response_without_url_raises(ydl, monkeypatch):
    fake = FakeTokFM(
        _page(_player_tag("200878", TITLE, free="false", premium="true")), gets={"tok": "tokfm"}
    )

    with pytest.raises(ExtractorError):
        _extract(ydl, monkeypatch, fake)


def test_page_without_player_tag_raises(ydl, monkeypatch):
    fake = FakeTokFM(_page())

    with pytest.raises(ExtractorError):
        _extract(ydl, monkeypatch, fake)

    assert "access" not in fake.kinds()
    assert "gets" not in fake.kinds()


def test_non_episode_link_raises_expected_error(ydl):
    ie = TokFMNonEpisodeIE(ydl)

    with pytest.raises(ExtractorError) as excinfo:
        ie.extract("https://audycje.tokfm.pl/audycja/687,Dluzsza-rozmowa")

    assert NON_EPISODE_ERROR in str(excinfo.value)
    assert excinfo.value.expected is True


def test_yt_dlp_discovers_plugin_from_repo_root():
    # Production runs main.py from the repo root, so the root is on sys.path and
    # yt-dlp's plugin finder must pick up yt_dlp_plugins/extractor/tokfm.py.
    code = (
        "import yt_dlp; yt_dlp.YoutubeDL({'quiet': True}); "
        "from yt_dlp.globals import plugin_ies; print(sorted(plugin_ies.value))"
    )
    env = {key: value for key, value in os.environ.items() if key != "YTDLP_NO_PLUGINS"}
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    )
    assert "TokFMIE" in result.stdout
    assert "TokFMNonEpisodeIE" in result.stdout
    assert "Error while importing module" not in result.stderr
