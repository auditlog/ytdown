"""Unit tests for bot.downloader_media module."""

from __future__ import annotations

import os
import tempfile
import shutil

import pytest

from bot.downloader_media import (
    is_photo_entry,
    download_photo,
    download_thumbnail,
    _load_instagram_cookies,
    get_instagram_post_info,
)


# ---------------------------------------------------------------------------
# is_photo_entry
# ---------------------------------------------------------------------------

def test_is_photo_entry_empty_dict_returns_false():
    assert is_photo_entry({}) is False


def test_is_photo_entry_none_returns_false():
    assert is_photo_entry(None) is False


def test_is_photo_entry_is_video_true_returns_false():
    assert is_photo_entry({"is_video": True}) is False


def test_is_photo_entry_is_video_false_returns_true():
    assert is_photo_entry({"is_video": False}) is True


def test_is_photo_entry_ext_jpg_returns_true():
    assert is_photo_entry({"ext": "jpg"}) is True


def test_is_photo_entry_ext_jpeg_returns_true():
    assert is_photo_entry({"ext": "jpeg"}) is True


def test_is_photo_entry_ext_png_returns_true():
    assert is_photo_entry({"ext": "png"}) is True


def test_is_photo_entry_ext_webp_returns_true():
    assert is_photo_entry({"ext": "webp"}) is True


def test_is_photo_entry_ext_mp4_returns_false():
    assert is_photo_entry({"ext": "mp4"}) is False


def test_is_photo_entry_url_with_jpg_extension_returns_true():
    info = {"url": "https://example.com/photo.jpg"}
    assert is_photo_entry(info) is True


def test_is_photo_entry_url_with_png_extension_returns_true():
    info = {"url": "https://example.com/photo.png"}
    assert is_photo_entry(info) is True


def test_is_photo_entry_url_with_mp4_extension_returns_false():
    info = {"url": "https://example.com/video.mp4"}
    assert is_photo_entry(info) is False


def test_is_photo_entry_video_with_formats_key_returns_false():
    # Has formats → treated as video even without duration
    info = {"url": "https://example.com/video.mp4", "formats": [{"ext": "mp4"}]}
    assert is_photo_entry(info) is False


def test_is_photo_entry_video_with_duration_returns_false():
    info = {"url": "https://example.com/file", "duration": 120}
    assert is_photo_entry(info) is False


# ---------------------------------------------------------------------------
# download_photo
# ---------------------------------------------------------------------------

class _FakeResponse:
    """Minimal requests.Response stub."""

    def __init__(self, content: bytes, content_type: str = "image/jpeg", status_code: int = 200):
        self.content = content
        self.headers = {"content-type": content_type}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


def test_download_photo_success_writes_jpg_file(monkeypatch, tmp_path):
    fake_resp = _FakeResponse(b"FAKEJPG", content_type="image/jpeg")
    monkeypatch.setattr("requests.get", lambda *a, **kw: fake_resp)

    output_path = str(tmp_path / "photo")
    result = download_photo("https://example.com/img.jpg", output_path)

    assert result == output_path + ".jpg"
    assert os.path.exists(result)


def test_download_photo_success_detects_png_content_type(monkeypatch, tmp_path):
    fake_resp = _FakeResponse(b"FAKEPNG", content_type="image/png")
    monkeypatch.setattr("requests.get", lambda *a, **kw: fake_resp)

    output_path = str(tmp_path / "photo")
    result = download_photo("https://example.com/img.png", output_path)

    assert result is not None
    assert result.endswith(".png")


def test_download_photo_success_detects_webp_content_type(monkeypatch, tmp_path):
    fake_resp = _FakeResponse(b"FAKEWEBP", content_type="image/webp")
    monkeypatch.setattr("requests.get", lambda *a, **kw: fake_resp)

    output_path = str(tmp_path / "photo")
    result = download_photo("https://example.com/img.webp", output_path)

    assert result is not None
    assert result.endswith(".webp")


def test_download_photo_success_writes_correct_bytes(monkeypatch, tmp_path):
    payload = b"\x89PNG\r\n"
    fake_resp = _FakeResponse(payload, content_type="image/jpeg")
    monkeypatch.setattr("requests.get", lambda *a, **kw: fake_resp)

    output_path = str(tmp_path / "photo")
    result = download_photo("https://example.com/img.jpg", output_path)

    with open(result, "rb") as f:
        assert f.read() == payload


def test_download_photo_http_error_returns_none(monkeypatch, tmp_path):
    fake_resp = _FakeResponse(b"", status_code=404)
    monkeypatch.setattr("requests.get", lambda *a, **kw: fake_resp)

    output_path = str(tmp_path / "photo")
    result = download_photo("https://example.com/missing.jpg", output_path)

    assert result is None


def test_download_photo_network_exception_returns_none(monkeypatch, tmp_path):
    def _raise(*a, **kw):
        raise ConnectionError("network down")

    monkeypatch.setattr("requests.get", _raise)

    output_path = str(tmp_path / "photo")
    result = download_photo("https://example.com/img.jpg", output_path)

    assert result is None


# ---------------------------------------------------------------------------
# download_thumbnail
# ---------------------------------------------------------------------------

def _make_fake_pil(saved_files: list):
    """Return (fake_PIL_module, fake_Image_object) for monkeypatching sys.modules.

    The source does:
        from PIL import Image          # → PIL.Image attribute
        img = Image.open(buf)          # → must have .open()
        img.thumbnail(...)
        img.mode                       # → "RGB" so convert is skipped
        img.save(path, fmt, quality=)
        Image.LANCZOS                  # → constant
    """
    import types

    fake_pil = types.ModuleType("PIL")

    class _FakeImg:
        mode = "RGB"

        def thumbnail(self, size, resample=None):
            pass

        def convert(self, mode):
            return self

        def save(self, path, fmt, quality=None):
            with open(path, "wb") as f:
                f.write(b"FAKE_JPEG")
            saved_files.append(path)

    # This object is what `Image` resolves to after `from PIL import Image`
    class _FakeImageClass:
        LANCZOS = 1

        @staticmethod
        def open(buf):
            return _FakeImg()

    fake_pil.Image = _FakeImageClass

    # Also register as PIL.Image in sys.modules (some import styles need this)
    fake_image_submod = types.ModuleType("PIL.Image")
    fake_image_submod.open = _FakeImageClass.open
    fake_image_submod.LANCZOS = 1

    return fake_pil, fake_image_submod


def test_download_thumbnail_no_thumbnail_url_returns_none():
    info = {"title": "Test", "formats": []}
    result = download_thumbnail(info, "/tmp")
    assert result is None


def test_download_thumbnail_empty_thumbnails_list_no_fallback_returns_none():
    info = {"title": "Test", "thumbnails": []}
    result = download_thumbnail(info, "/tmp")
    assert result is None


def test_download_thumbnail_with_thumbnail_key_downloads_and_saves(monkeypatch, tmp_path):
    import sys

    saved_files: list = []
    fake_pil, fake_image_mod = _make_fake_pil(saved_files)

    # Inject fake PIL into sys.modules so `from PIL import Image` works
    monkeypatch.setitem(sys.modules, "PIL", fake_pil)
    monkeypatch.setitem(sys.modules, "PIL.Image", fake_image_mod)

    fake_resp = _FakeResponse(b"FAKEJPEG", content_type="image/jpeg")
    monkeypatch.setattr("requests.get", lambda *a, **kw: fake_resp)

    info = {"title": "My Video", "thumbnail": "https://example.com/thumb.jpg"}
    result = download_thumbnail(info, str(tmp_path))

    assert result is not None
    assert os.path.exists(result)
    assert result.endswith("_thumb_full.jpg")


def test_download_thumbnail_uses_last_thumbnails_entry(monkeypatch, tmp_path):
    import sys

    saved_files: list = []
    fake_pil, fake_image_mod = _make_fake_pil(saved_files)
    monkeypatch.setitem(sys.modules, "PIL", fake_pil)
    monkeypatch.setitem(sys.modules, "PIL.Image", fake_image_mod)

    captured_urls: list = []

    def _fake_get(url, **kw):
        captured_urls.append(url)
        return _FakeResponse(b"FAKEJPEG", content_type="image/jpeg")

    monkeypatch.setattr("requests.get", _fake_get)

    info = {
        "title": "Vid",
        "thumbnails": [
            {"url": "https://example.com/small.jpg"},
            {"url": "https://example.com/large.jpg"},
        ],
    }
    download_thumbnail(info, str(tmp_path))

    assert captured_urls[0] == "https://example.com/large.jpg"


def test_download_thumbnail_embed_mode_saves_with_embed_suffix(monkeypatch, tmp_path):
    import sys

    saved_files: list = []
    fake_pil, fake_image_mod = _make_fake_pil(saved_files)
    monkeypatch.setitem(sys.modules, "PIL", fake_pil)
    monkeypatch.setitem(sys.modules, "PIL.Image", fake_image_mod)

    monkeypatch.setattr("requests.get", lambda *a, **kw: _FakeResponse(b"FAKE"))

    info = {"title": "Clip", "thumbnail": "https://example.com/t.jpg"}
    result = download_thumbnail(info, str(tmp_path), embed=True)

    assert result is not None
    assert result.endswith("_thumb_embed.jpg")


def test_download_thumbnail_request_failure_returns_none(monkeypatch, tmp_path):
    def _raise(*a, **kw):
        raise ConnectionError("timeout")

    monkeypatch.setattr("requests.get", _raise)

    info = {"title": "Vid", "thumbnail": "https://example.com/t.jpg"}
    result = download_thumbnail(info, str(tmp_path))

    assert result is None


# ---------------------------------------------------------------------------
# _load_instagram_cookies
# ---------------------------------------------------------------------------

def test_load_instagram_cookies_no_file_returns_empty_dict():
    result = _load_instagram_cookies(cookies_file=None)
    assert result == {}


def test_load_instagram_cookies_nonexistent_path_returns_empty_dict(tmp_path):
    missing = str(tmp_path / "no_such_cookies.txt")
    result = _load_instagram_cookies(cookies_file=missing)
    assert result == {}


def test_load_instagram_cookies_parses_instagram_entries(tmp_path):
    cookies_file = tmp_path / "cookies.txt"
    # Netscape cookie format: domain flag path secure expiry name value
    line = ".instagram.com\tTRUE\t/\tTRUE\t9999999999\tsessionid\tabc123\n"
    cookies_file.write_text("# Netscape HTTP Cookie File\n" + line)

    result = _load_instagram_cookies(cookies_file=str(cookies_file))

    assert result.get("sessionid") == "abc123"


def test_load_instagram_cookies_ignores_non_instagram_entries(tmp_path):
    cookies_file = tmp_path / "cookies.txt"
    line = ".youtube.com\tTRUE\t/\tTRUE\t9999999999\tSID\txyz\n"
    cookies_file.write_text(line)

    result = _load_instagram_cookies(cookies_file=str(cookies_file))

    assert result == {}


def test_load_instagram_cookies_ignores_comment_lines(tmp_path):
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text("# This is a comment\n")

    result = _load_instagram_cookies(cookies_file=str(cookies_file))

    assert result == {}


def test_load_instagram_cookies_skips_malformed_lines(tmp_path):
    cookies_file = tmp_path / "cookies.txt"
    # Only 3 tab-separated fields — too short to be valid
    cookies_file.write_text(".instagram.com\tTRUE\tshort\n")

    result = _load_instagram_cookies(cookies_file=str(cookies_file))

    assert result == {}


# ---------------------------------------------------------------------------
# get_instagram_post_info
# ---------------------------------------------------------------------------

def test_get_instagram_post_info_invalid_url_returns_none():
    # URL has no /p/ or /reel/ segment
    result = get_instagram_post_info("https://instagram.com/explore/")
    assert result is None


def test_get_instagram_post_info_ytdlp_success(monkeypatch, tmp_path):
    """When instaloader is unavailable, falls back to yt-dlp which returns info."""
    fake_info = {"title": "Test post", "ext": "jpg", "url": "https://example.com/img.jpg"}

    # Force instaloader import to fail so the yt-dlp path is taken
    import sys
    monkeypatch.setitem(sys.modules, "instaloader", None)

    class _FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def extract_info(self, url, download=False):
            return fake_info

    monkeypatch.setattr("yt_dlp.YoutubeDL", _FakeYDL)

    result = get_instagram_post_info(
        "https://www.instagram.com/p/ABC123/",
        cookies_file=None,
    )

    assert result == fake_info


def test_get_instagram_post_info_ytdlp_raises_returns_none(monkeypatch):
    """When yt-dlp raises an exception, the function returns None."""
    import sys
    monkeypatch.setitem(sys.modules, "instaloader", None)

    class _FailYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def extract_info(self, url, download=False):
            raise RuntimeError("yt-dlp failed")

    monkeypatch.setattr("yt_dlp.YoutubeDL", _FailYDL)

    result = get_instagram_post_info(
        "https://www.instagram.com/p/ABC123/",
        cookies_file=None,
    )

    assert result is None


def test_get_instagram_post_info_reel_url_is_accepted(monkeypatch):
    """Reel URLs with /reel/ segment are accepted (shortcode extracted)."""
    import sys
    monkeypatch.setitem(sys.modules, "instaloader", None)

    called_with = []

    class _FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def extract_info(self, url, download=False):
            called_with.append(url)
            return {"title": "reel", "ext": "mp4", "duration": 10}

    monkeypatch.setattr("yt_dlp.YoutubeDL", _FakeYDL)

    result = get_instagram_post_info(
        "https://www.instagram.com/reel/XYZ789/",
        cookies_file=None,
    )

    # The function should have attempted yt-dlp (called_with is non-empty)
    assert len(called_with) == 1
