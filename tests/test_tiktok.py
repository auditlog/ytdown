"""TikTok photo extraction, redirect boundaries and real image decoding."""

from io import BytesIO
from unittest.mock import Mock

import pytest
import requests
from PIL import Image

from bot import tiktok

POST_ID = "7706467733575060769"
PHOTO_URL = f"https://www.tiktok.com/@example/photo/{POST_ID}"
VIDEO_URL = PHOTO_URL.replace("/photo/", "/video/")
IMAGE_URL = "https://p19-common-sign.tiktokcdn-eu.com/image.webp?signature=example"


def _response(*, status=200, location=None, data=b"", headers=None):
    response = Mock(status_code=status)
    response.headers = headers or {}
    if location:
        response.headers["Location"] = location
    response.iter_content.return_value = [data]
    response.raise_for_status.return_value = None
    return response


def test_short_link_redirects_to_photo_without_reading_response_body(monkeypatch):
    redirect = _response(status=302, location=PHOTO_URL)
    final = _response()
    get = Mock(side_effect=[redirect, final])
    monkeypatch.setattr(tiktok.requests, "get", get)
    assert tiktok.resolve_tiktok_url("https://vm.tiktok.com/example/") == PHOTO_URL
    assert get.call_count == 2
    assert all(call.kwargs["allow_redirects"] is False for call in get.call_args_list)
    redirect.close.assert_called_once()
    final.close.assert_called_once()
    final.iter_content.assert_not_called()


@pytest.mark.parametrize(
    "location",
    [
        "http://www.tiktok.com/@example/photo/1",
        "https://tiktok.com.evil.test/photo/1",
        "https://127.0.0.1/private",
        "https://www.tiktok.com:8443/@example/photo/1",
        "https://user:password@www.tiktok.com/@example/photo/1",
    ],
)
def test_rejects_unsafe_redirect_before_request(monkeypatch, location):
    get = Mock(return_value=_response(status=302, location=location))
    monkeypatch.setattr(tiktok.requests, "get", get)
    with pytest.raises(tiktok.TikTokError):
        tiktok.resolve_tiktok_url("https://vt.tiktok.com/example/")
    get.assert_called_once()


def test_limits_redirect_loop(monkeypatch):
    get = Mock(return_value=_response(status=302, location="/loop"))
    monkeypatch.setattr(tiktok.requests, "get", get)
    with pytest.raises(tiktok.TikTokError):
        tiktok.resolve_tiktok_url("https://vm.tiktok.com/example/")
    assert get.call_count == 6


def test_network_error_has_user_safe_explanation(monkeypatch):
    monkeypatch.setattr(tiktok.requests, "get", Mock(side_effect=requests.Timeout))
    with pytest.raises(tiktok.TikTokError, match="połączyć"):
        tiktok.resolve_tiktok_url("https://vm.tiktok.com/example/")


def _install_extractor(monkeypatch, item, status=0):
    downloader = Mock()
    downloader.__enter__ = Mock(return_value=downloader)
    downloader.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(tiktok.yt_dlp, "YoutubeDL", Mock(return_value=downloader))
    extractor = Mock()
    extractor._extract_web_data_and_status.return_value = (item, status)
    monkeypatch.setattr(tiktok, "TikTokIE", Mock(return_value=extractor))
    return extractor


def _item(count=2):
    return {
        "id": POST_ID,
        "desc": "Photo post",
        "imagePost": {
            "images": [{"imageURL": {"urlList": [f"{IMAGE_URL}&slide={i}"]}} for i in range(count)]
        },
    }


def test_reads_photo_metadata_from_video_page_in_slide_order(monkeypatch):
    extractor = _install_extractor(monkeypatch, _item())
    result = tiktok.get_tiktok_gallery(PHOTO_URL, cookies_file=None)
    assert [entry["url"] for entry in result["photos"]] == [
        f"{IMAGE_URL}&slide={i}" for i in range(2)
    ]
    assert result["url"] == PHOTO_URL
    extractor._extract_web_data_and_status.assert_called_once_with(VIDEO_URL, POST_ID)
    assert "cookiefile" not in tiktok.yt_dlp.YoutubeDL.call_args.args[0]


def test_uses_configured_cookie_file_without_reading_it_in_adapter(monkeypatch, tmp_path):
    _install_extractor(monkeypatch, _item())
    cookies = tmp_path / "cookies.txt"
    cookies.touch()
    tiktok.get_tiktok_gallery(PHOTO_URL, cookies_file=str(cookies))
    assert tiktok.yt_dlp.YoutubeDL.call_args.args[0]["cookiefile"] == str(cookies)


@pytest.mark.parametrize(
    "item,status",
    [
        (None, 10204),
        (_item(), 10216),
        ({"id": "wrong", "imagePost": _item()["imagePost"]}, 0),
        (_item(0), 0),
        (_item(101), 0),
        (
            {
                "id": POST_ID,
                "imagePost": {
                    "images": [{"imageURL": {"urlList": ["https://127.0.0.1/internal"]}}]
                },
            },
            0,
        ),
    ],
)
def test_unavailable_or_malformed_gallery_has_no_partial_result(monkeypatch, item, status):
    _install_extractor(monkeypatch, item, status)
    with pytest.raises(tiktok.TikTokError, match="odczytać zdjęć"):
        tiktok.get_tiktok_gallery(PHOTO_URL, cookies_file=None)


def test_regular_video_needs_no_extra_requests(monkeypatch):
    get = Mock()
    monkeypatch.setattr(tiktok.requests, "get", get)
    assert tiktok.resolve_tiktok_url(VIDEO_URL) == VIDEO_URL
    assert tiktok.get_tiktok_gallery(VIDEO_URL, cookies_file=None) is None
    get.assert_not_called()


def _image_bytes():
    data = BytesIO()
    Image.new("RGB", (80, 120), "green").save(data, "WEBP")
    return data.getvalue()


def test_download_converts_webp_to_telegram_jpeg(monkeypatch, tmp_path):
    response = _response(data=_image_bytes())
    monkeypatch.setattr(tiktok.requests, "get", Mock(return_value=response))
    result = tiktok.download_tiktok_photo(IMAGE_URL, str(tmp_path / "slide"))
    with Image.open(result) as image:
        assert image.format == "JPEG"
        assert image.size == (80, 120)
    response.close.assert_called_once()


@pytest.mark.parametrize("declared", [True, False])
def test_download_limit_works_with_and_without_content_length(monkeypatch, tmp_path, declared):
    monkeypatch.setattr(tiktok, "_MAX_SOURCE_BYTES", 5)
    response = _response(
        data=b"too much data", headers={"Content-Length": "100"} if declared else {}
    )
    monkeypatch.setattr(tiktok.requests, "get", Mock(return_value=response))
    assert tiktok.download_tiktok_photo(IMAGE_URL, str(tmp_path / "slide")) is None
    assert list(tmp_path.iterdir()) == []
    response.close.assert_called_once()


def test_non_image_response_is_not_written(monkeypatch, tmp_path):
    monkeypatch.setattr(
        tiktok.requests, "get", Mock(return_value=_response(data=b"<html>login</html>"))
    )
    assert tiktok.download_tiktok_photo(IMAGE_URL, str(tmp_path / "slide")) is None
    assert list(tmp_path.iterdir()) == []
