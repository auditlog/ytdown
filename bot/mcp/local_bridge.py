"""Resilient stdio-to-HTTPS bridge for the ytdown MCP server (stdlib only).

Claude Desktop starts this process once per session and keeps its stdio open.
The server runs FastMCP with ``stateless_http=True`` and ``json_response=True``
(bot/mcp/server.py), so every JSON-RPC message can travel as an independent
HTTPS POST. A network failure (laptop sleep, Wi-Fi change, a DNS hiccup in WSL)
therefore only fails the request in flight: the bridge answers it with a
JSON-RPC error and keeps running. The previous path, a long-lived SSH session,
died on every such failure, and Claude Desktop does not restart a server that
exited, so it kept showing the server as unavailable until the app restarted.

Standard library only, so it runs with the system python3 in WSL without a
virtualenv and starts fast. See also: bot/mcp/bridge.py (SDK bridge used on the
Pi behind SSH), docs/mcp-rpi5a.md.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Iterable, TextIO
from urllib.parse import urlsplit

TOKEN_KEY = "YTDOWN_MCP_TOKEN"
NETWORK_ERROR_CODE = -32000
AUTH_ERROR_CODE = -32001
HTTP_ERROR_CODE = -32002
PARSE_ERROR_CODE = -32700
# Long tool calls are answered within one request; keep the old bridge's 300 s.
REQUEST_TIMEOUT_SEC = 300
# One quick retry absorbs the first failed call right after the laptop wakes.
RETRY_DELAY_SEC = 2.0
# Claude Desktop may send e.g. a ping while a long tool call is running.
MAX_PARALLEL_REQUESTS = 4

NETWORK_ERROR_TEXT = (
    "ytdown MCP: brak połączenia z serwerem na Raspberry Pi (sieć lub Tailscale). "
    "Spróbuj ponownie za chwilę."
)

PostFn = Callable[[str, bytes, dict, float], "tuple[int, bytes]"]


def validate_endpoint(url: str) -> str:
    """Accept HTTPS, or plain HTTP only on loopback; never credentials in the URL."""

    parts = urlsplit(url)
    if (
        not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or parts.scheme not in {"http", "https"}
        or (parts.scheme == "http" and parts.hostname not in {"127.0.0.1", "localhost", "::1"})
    ):
        raise ValueError("Use HTTPS, or HTTP on loopback; credentials must be in the token file.")
    return url


def read_token(path: Path) -> str:
    """Read YTDOWN_MCP_TOKEN from a dotenv-style file (comments and quotes allowed)."""

    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip().removeprefix("export ").strip() == TOKEN_KEY:
            return value.strip().strip("'\"")
    return ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None  # surface 3xx as HTTPError instead of re-sending the token


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def _urllib_post(url: str, payload: bytes, headers: dict, timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class Bridge:
    """Forward newline-delimited JSON-RPC from stdin to the HTTP server."""

    def __init__(
        self,
        url: str,
        token: str,
        *,
        output: TextIO | None = None,
        post: PostFn | None = None,
        retry_delay: float = RETRY_DELAY_SEC,
        workers: int = MAX_PARALLEL_REQUESTS,
        log: Callable[[str], None] | None = None,
    ):
        self._url = validate_endpoint(url)
        if len(token) < 32 or not token.isascii() or any(char.isspace() for char in token):
            raise ValueError(f"Missing or invalid {TOKEN_KEY} in the token file.")
        self._token = token
        self._output = output if output is not None else sys.stdout
        self._post = post or _urllib_post
        self._retry_delay = retry_delay
        self._workers = workers
        self._log = log or (lambda text: print(f"ytdown-mcp-bridge: {text}", file=sys.stderr, flush=True))
        self._write_lock = threading.Lock()
        self._protocol_version: str | None = None

    def run(self, stdin: Iterable[str]) -> None:
        """Process messages until stdin closes, then wait for in-flight requests."""

        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            for line in stdin:
                if line.strip():
                    pool.submit(self._handle_safely, line)

    def _handle_safely(self, line: str) -> None:
        try:
            self.handle(line)
        except Exception as exc:  # a bug here must never take the bridge down
            self._log(f"unexpected error: {exc!r}")

    def handle(self, line: str) -> None:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            self._write_error(None, PARSE_ERROR_CODE, "Parse error")
            return
        is_request = isinstance(message, dict) and "method" in message and "id" in message
        request_id = message.get("id") if is_request else None

        try:
            status, body = self._send(line.strip().encode("utf-8"))
        except OSError as exc:  # URLError, timeouts and resets are all OSError
            self._log(f"network error, request answered with an error: {exc}")
            if is_request:
                self._write_error(request_id, NETWORK_ERROR_CODE, NETWORK_ERROR_TEXT)
            return

        if status in (401, 403):
            self._log(f"server rejected the token (HTTP {status})")
            if is_request:
                self._write_error(
                    request_id, AUTH_ERROR_CODE,
                    f"ytdown MCP: serwer odrzucił token (HTTP {status}). Sprawdź plik tokenu.",
                )
            return
        if status >= 300:
            self._log(f"server answered HTTP {status}")
            if is_request:
                self._write_error(request_id, HTTP_ERROR_CODE, f"ytdown MCP: serwer zwrócił błąd HTTP {status}.")
            return

        for reply in self._parse_body(body):
            if is_request and message.get("method") == "initialize" and isinstance(reply, dict):
                version = (reply.get("result") or {}).get("protocolVersion")
                if isinstance(version, str):
                    self._protocol_version = version
            self._write(reply)

    def _send(self, payload: bytes) -> tuple[int, bytes]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._protocol_version:
            headers["MCP-Protocol-Version"] = self._protocol_version
        try:
            return self._post(self._url, payload, headers, REQUEST_TIMEOUT_SEC)
        except OSError as exc:
            self._log(f"request failed ({exc}), retrying once")
            time.sleep(self._retry_delay)
            return self._post(self._url, payload, headers, REQUEST_TIMEOUT_SEC)

    @staticmethod
    def _parse_body(body: bytes) -> list:
        text = body.decode("utf-8").strip()
        if not text:
            return []
        if text[0] in "{[":
            parsed = json.loads(text)
            return parsed if isinstance(parsed, list) else [parsed]
        # The server is configured for JSON replies; accept SSE framing anyway.
        return [
            json.loads(line[len("data:"):].strip())
            for line in text.splitlines()
            if line.startswith("data:") and line[len("data:"):].strip()
        ]

    def _write(self, message) -> None:
        # ASCII-only JSON keeps the pipe independent of the console encoding.
        with self._write_lock:
            self._output.write(json.dumps(message) + "\n")
            self._output.flush()

    def _write_error(self, request_id, code: int, text: str) -> None:
        self._write({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": text}})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True, help="https://<host>:9443/mcp")
    parser.add_argument("--token-file", type=Path, required=True, help=f"dotenv file with {TOKEN_KEY}")
    args = parser.parse_args(argv)
    try:
        bridge = Bridge(args.url, read_token(args.token_file))
    except (OSError, ValueError) as exc:
        print(f"ytdown-mcp-bridge: cannot start: {exc}", file=sys.stderr)
        return 1
    bridge.run(io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
