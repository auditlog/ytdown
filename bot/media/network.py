"""Bounded HTTPS downloads with DNS-pinned public addresses on every redirect."""

from __future__ import annotations

import http.client
import ipaddress
import shutil
import socket
import ssl
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bot.media.store import MediaError


def public_address(host: str) -> str:
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise MediaError("Adres musi wskazywać publiczny serwer HTTPS.")
    return addresses[0][4][0]


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(host, timeout=20)
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def fetch_public(url: str, *, max_bytes=2 * 1024**2, destination=None, cancellation=None):
    """Return bytes (small documents) or write a bounded media stream to disk."""
    deadline = time.monotonic() + 300
    destination = Path(destination) if destination is not None else None
    for _ in range(6):
        connection = None
        try:
            if cancellation is not None and cancellation.event.is_set():
                raise MediaError("Operacja została zatrzymana.")
            parsed = urlparse(url)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.port not in (None, 443)
            ):
                raise MediaError("Wymagany jest publiczny adres HTTPS bez danych logowania.")
            connection = _PinnedHTTPS(parsed.hostname, public_address(parsed.hostname))
            target = parsed.path or "/"
            if parsed.query:
                target += "?" + parsed.query
            connection.request(
                "GET",
                target,
                headers={"User-Agent": "ytdown/1.0 media-reader", "Accept-Encoding": "identity"},
            )
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise MediaError("Nieprawidłowe przekierowanie źródła.")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise MediaError(f"Serwer źródła zwrócił błąd HTTP {response.status}.")
            if response.getheader("Content-Encoding", "identity") != "identity":
                raise MediaError("Serwer zwrócił nieobsługiwany format kompresji.")
            if int(response.getheader("Content-Length", "0")) > max_bytes:
                raise MediaError("Źródło przekracza dozwolony rozmiar.")
            total, chunks = 0, []
            output = open(destination, "wb") if destination else None
            try:
                while True:
                    if time.monotonic() > deadline:
                        raise MediaError("Przekroczono czas pobierania źródła.")
                    if cancellation is not None and cancellation.event.is_set():
                        raise MediaError("Operacja została zatrzymana.")
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise MediaError("Źródło przekracza dozwolony rozmiar.")
                    if output:
                        if shutil.disk_usage(destination.parent).free < 2 * 1024**3 + len(chunk):
                            raise MediaError("Za mało miejsca na pobranie materiału.")
                        output.write(chunk)
                    else:
                        chunks.append(chunk)
            finally:
                if output:
                    output.close()
            return b"".join(chunks), url
        except MediaError:
            raise
        except (OSError, ValueError, http.client.HTTPException, ssl.SSLError):
            raise MediaError("Nie udało się odczytać publicznego źródła HTTPS.") from None
        finally:
            if connection:
                connection.close()
    raise MediaError("Zbyt wiele przekierowań źródła.")
