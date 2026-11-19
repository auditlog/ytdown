"""TikTok photo metadata and bounded image downloads, without a third-party API."""

from __future__ import annotations

import logging
import os
import re
from io import BytesIO
from urllib.parse import urljoin, urlparse, urlunparse

import requests
import yt_dlp
from PIL import Image, ImageOps
from yt_dlp.extractor.tiktok import TikTokIE

from bot.config import COOKIES_FILE

_POST_PATH = re.compile(r"/@[^/]+/(?P<kind>photo|video)/(?P<id>\d+)/?$")
_PAGE_HOSTS = {"tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"}
_MEDIA_DOMAINS = (
    "tiktokcdn.com",
    "tiktokcdn-eu.com",
    "tiktokcdn-us.com",
    "tiktokcdn-us.net",
    "byteoversea.com",
    "ibytedtos.com",
    "byteimg.com",
    "ibyteimg.com",
)
_MAX_SOURCE_BYTES = 20 * 1024 * 1024
_MAX_PHOTO_BYTES = 10 * 1024 * 1024
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.tiktok.com/"}


class TikTokError(Exception):
    """An expected failure that can be explained without exposing provider details."""


def _allowed_url(url: str, *, media: bool = False) -> bool:
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        return (
            parsed.scheme == "https"
            and not parsed.username
            and not parsed.password
            and parsed.port in (None, 443)
            and (
                host in _PAGE_HOSTS
                if not media
                else any(host == domain or host.endswith("." + domain) for domain in _MEDIA_DOMAINS)
            )
        )
    except ValueError:
        return False


def _open_url(url: str, *, media: bool = False):
    """Validate every redirect before making a request, including CDN redirects."""
    for _ in range(6):
        if not _allowed_url(url, media=media):
            raise TikTokError("TikTok zwrócił nieobsługiwany adres. Wyślij link do posta ponownie.")
        response = requests.get(
            url,
            headers=_HEADERS,
            timeout=20,
            stream=True,
            allow_redirects=False,
        )
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            response.close()
            if not location:
                break
            url = urljoin(url, location)
            continue
        try:
            response.raise_for_status()
        except Exception:
            response.close()
            raise
        return response, url
    raise TikTokError("Nie udało się rozwinąć linku TikToka. Wyślij pełny adres posta.")


def resolve_tiktok_url(url: str) -> str:
    """Resolve public share links while keeping requests on TikTok domains."""
    if not _allowed_url(url):
        raise TikTokError("Nieprawidłowy link TikToka.")
    if _POST_PATH.fullmatch(urlparse(url).path):
        return url
    try:
        response, resolved = _open_url(url)
        response.close()
    except requests.RequestException as exc:
        raise TikTokError(
            "Nie udało się połączyć z TikTokiem. Spróbuj ponownie za chwilę."
        ) from exc
    if not _POST_PATH.fullmatch(urlparse(resolved).path):
        raise TikTokError("Wyślij link do konkretnego filmu lub posta ze zdjęciami na TikToku.")
    return resolved


def get_tiktok_gallery(url: str, *, cookies_file: str | None = COOKIES_FILE) -> dict | None:
    """Return ordered gallery entries, or None for a regular video URL.

    TikTok exposes imagePost on the canonical /video/ page, even for /photo/
    posts. Isolate yt-dlp's private web-data helper here so API changes have
    one adapter to update; regular video extraction remains with the downloader.
    """
    match = _POST_PATH.fullmatch(urlparse(url).path)
    if not _allowed_url(url) or not match:
        raise TikTokError("Nieprawidłowy link do posta TikToka.")
    if match["kind"] != "photo":
        return None
    post_id = match["id"]
    parsed = urlparse(url)
    page_url = urlunparse(
        ("https", "www.tiktok.com", parsed.path.replace("/photo/", "/video/"), "", "", "")
    )
    opts = {"quiet": True, "no_warnings": True, "socket_timeout": 20, "cachedir": False}
    if cookies_file and os.path.exists(cookies_file):
        opts["cookiefile"] = cookies_file
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            extractor = TikTokIE(ydl)
            extractor.initialize()
            item, status = extractor._extract_web_data_and_status(page_url, post_id)
        if status != 0 or not item or str(item.get("id")) != post_id:
            raise ValueError(f"Post unavailable (status {status})")
        images = item.get("imagePost", {}).get("images", [])
        if not images or len(images) > 100:
            raise ValueError("Missing or oversized image list")
        photos = []
        for image in images:
            urls = image.get("imageURL", {}).get("urlList", [])
            image_url = next(
                (
                    value
                    for value in urls
                    if isinstance(value, str) and _allowed_url(value, media=True)
                ),
                None,
            )
            if not image_url:
                raise ValueError("Missing supported image URL")
            photos.append({"url": image_url, "ext": "jpg"})
        return {
            "id": post_id,
            "url": url,
            "title": str(item.get("desc") or "Zdjęcia z TikToka"),
            "photos": photos,
        }
    except Exception as exc:
        logging.warning(
            "TikTok gallery metadata failed for post %s: %s", post_id, type(exc).__name__
        )
        raise TikTokError(
            "Nie udało się odczytać zdjęć z TikToka. Post może być niedostępny "
            "lub wymagać zalogowania. Spróbuj ponownie; jeśli błąd się powtarza, "
            "sprawdź aktualność cookies TikToka."
        ) from exc


def download_tiktok_photo(url: str, output_path: str) -> str | None:
    """Download one bounded image and encode JPEG for Telegram photo delivery."""
    try:
        response, _ = _open_url(url, media=True)
        try:
            if int(response.headers.get("Content-Length", "0")) > _MAX_SOURCE_BYTES:
                raise ValueError("Image exceeds download limit")
            data = bytearray()
            for chunk in response.iter_content(64 * 1024):
                data.extend(chunk)
                if len(data) > _MAX_SOURCE_BYTES:
                    raise ValueError("Image exceeds download limit")
        finally:
            response.close()
        with Image.open(BytesIO(data)) as original:
            if original.width * original.height > 40_000_000:
                raise ValueError("Image dimensions exceed decode limit")
            image = ImageOps.exif_transpose(original).convert("RGB")
        try:
            if max(image.size) > 20 * min(image.size):
                raise ValueError("Image aspect ratio exceeds Telegram limit")
            if sum(image.size) > 10000:
                scale = 9998 / sum(image.size)
                image = image.resize(tuple(max(1, int(side * scale)) for side in image.size))
            encoded = BytesIO()
            image.save(encoded, "JPEG", quality=90)
            if encoded.tell() > _MAX_PHOTO_BYTES:
                encoded = BytesIO()
                image.save(encoded, "JPEG", quality=75)
            if encoded.tell() > _MAX_PHOTO_BYTES:
                raise ValueError("Image exceeds Telegram upload limit")
            path = f"{output_path}.jpg"
            with open(path, "wb") as file_obj:
                file_obj.write(encoded.getbuffer())
            return path
        finally:
            image.close()
    except Exception as exc:
        logging.warning("TikTok photo download failed: %s", type(exc).__name__)
        return None
