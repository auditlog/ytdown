"""Spotify Authorization Code flow and refresh-token persistence.

The OAuth access token is a live credential. This module never logs token,
authorization-code, or cookie values and stores the token file with mode 0600.
"""

from __future__ import annotations

import html
import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from bot.config import get_runtime_value

SPOTIFY_AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_USER_PLAYLISTS_URL = "https://api.spotify.com/v1/me/playlists"
SPOTIFY_OAUTH_SCOPES = (
    "playlist-read-private",
    "playlist-read-collaborative",
)
SPOTIFY_OAUTH_STATE_TTL_SEC = 10 * 60
SPOTIFY_TOKEN_REFRESH_MARGIN_SEC = 90

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_token_lock = threading.RLock()
_state_lock = threading.RLock()
_server_lock = threading.RLock()
_pending_states: dict[str, PendingSpotifyAuthorization] = {}
_callback_server: ThreadingHTTPServer | None = None


class SpotifyOAuthError(RuntimeError):
    """Normalized OAuth failure that does not carry credential values."""

    def __init__(self, reason: str, status_code: int | None = None):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


@dataclass(frozen=True)
class PendingSpotifyAuthorization:
    telegram_user_id: int
    created_at: float


def _oauth_config() -> tuple[str, str, str]:
    return (
        str(get_runtime_value("SPOTIFY_CLIENT_ID", "") or "").strip(),
        str(get_runtime_value("SPOTIFY_CLIENT_SECRET", "") or "").strip(),
        str(get_runtime_value("SPOTIFY_REDIRECT_URI", "") or "").strip(),
    )


def _token_file_path() -> Path:
    configured = str(get_runtime_value("SPOTIFY_OAUTH_TOKEN_FILE", "") or "").strip()
    if not configured:
        return _PROJECT_ROOT / "spotify_oauth.json"
    path = Path(configured).expanduser()
    return path if path.is_absolute() else _PROJECT_ROOT / path


def _read_token_payload() -> dict[str, Any] | None:
    path = _token_file_path()
    with _token_lock:
        try:
            with path.open(encoding="utf-8") as file_obj:
                payload = json.load(file_obj)
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError) as exc:
            logging.error("Cannot read Spotify OAuth token file %s: %s", path, exc)
            return None
    return payload if isinstance(payload, dict) else None


def _write_token_payload(payload: dict[str, Any]) -> None:
    path = _token_file_path()
    temp_path = path.with_name(f".{path.name}.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with _token_lock:
        try:
            with temp_path.open("w", encoding="utf-8") as file_obj:
                json.dump(payload, file_obj, indent=2)
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, path)
            os.chmod(path, 0o600)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def clear_spotify_authorization() -> bool:
    """Remove the stored OAuth token. Returns whether a token existed."""

    path = _token_file_path()
    with _token_lock:
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False


def has_spotify_authorization() -> bool:
    payload = _read_token_payload()
    return bool(payload and payload.get("refresh_token"))


def _purge_expired_states(now: float) -> None:
    expired = [
        state
        for state, pending in _pending_states.items()
        if now - pending.created_at > SPOTIFY_OAUTH_STATE_TTL_SEC
    ]
    for state in expired:
        _pending_states.pop(state, None)


def build_spotify_authorization_url(telegram_user_id: int) -> str:
    """Create a one-use Spotify authorization URL bound to a Telegram user."""

    client_id, client_secret, redirect_uri = _oauth_config()
    if not client_id or not client_secret:
        raise SpotifyOAuthError("missing_client_credentials")
    if not redirect_uri:
        raise SpotifyOAuthError("missing_redirect_uri")
    parsed_redirect = urlparse(redirect_uri)
    if parsed_redirect.scheme != "https" or not parsed_redirect.netloc:
        raise SpotifyOAuthError("invalid_redirect_uri")

    state = secrets.token_urlsafe(32)
    now = time.time()
    with _state_lock:
        _purge_expired_states(now)
        _pending_states[state] = PendingSpotifyAuthorization(
            telegram_user_id=int(telegram_user_id),
            created_at=now,
        )

    return f"{SPOTIFY_AUTHORIZE_URL}?{urlencode({
        'client_id': client_id,
        'response_type': 'code',
        'redirect_uri': redirect_uri,
        'state': state,
        'scope': ' '.join(SPOTIFY_OAUTH_SCOPES),
        'show_dialog': 'true',
    })}"


def _validated_token_payload(
    response_payload: dict[str, Any],
    *,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    access_token = response_payload.get("access_token")
    refresh_token = response_payload.get("refresh_token") or (
        previous.get("refresh_token") if previous else None
    )
    if not isinstance(access_token, str) or not access_token:
        raise SpotifyOAuthError("invalid_token_response")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise SpotifyOAuthError("missing_refresh_token")

    try:
        expires_in = max(60, int(response_payload.get("expires_in") or 3600))
    except (TypeError, ValueError) as exc:
        raise SpotifyOAuthError("invalid_token_response") from exc

    scope = response_payload.get("scope")
    if not isinstance(scope, str) or not scope:
        scope = str(previous.get("scope") or "") if previous else ""

    payload = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": time.time() + expires_in,
        "scope": scope,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    if previous and previous.get("authorized_at"):
        payload["authorized_at"] = previous["authorized_at"]
    else:
        payload["authorized_at"] = payload["updated_at"]
    return payload


def _parse_token_response(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise SpotifyOAuthError("invalid_token_response", response.status_code) from exc
    if response.status_code != 200:
        reason = payload.get("error") if isinstance(payload, dict) else None
        normalized = str(reason) if reason else "token_exchange_failed"
        raise SpotifyOAuthError(normalized, response.status_code)
    if not isinstance(payload, dict):
        raise SpotifyOAuthError("invalid_token_response", response.status_code)
    return payload


def _validate_playlist_access(access_token: str) -> None:
    try:
        response = requests.get(
            SPOTIFY_USER_PLAYLISTS_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            params={"limit": 1},
            timeout=20,
        )
    except requests.RequestException as exc:
        raise SpotifyOAuthError("network_error") from exc
    if response.status_code != 200:
        raise SpotifyOAuthError("playlist_access_denied", response.status_code)


def complete_spotify_authorization(code: str, state: str) -> int:
    """Validate callback state, exchange the code, and persist the token."""

    if not code or not state:
        raise SpotifyOAuthError("invalid_callback")
    now = time.time()
    with _state_lock:
        _purge_expired_states(now)
        pending = _pending_states.pop(state, None)
    if pending is None:
        raise SpotifyOAuthError("state_mismatch")

    client_id, client_secret, redirect_uri = _oauth_config()
    if not client_id or not client_secret or not redirect_uri:
        raise SpotifyOAuthError("oauth_not_configured")
    try:
        response = requests.post(
            SPOTIFY_TOKEN_URL,
            auth=(client_id, client_secret),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        raise SpotifyOAuthError("network_error") from exc

    token_payload = _validated_token_payload(_parse_token_response(response))
    granted_scopes = set(token_payload.get("scope", "").split())
    if not set(SPOTIFY_OAUTH_SCOPES).issubset(granted_scopes):
        raise SpotifyOAuthError("missing_scope")
    _validate_playlist_access(token_payload["access_token"])
    _write_token_payload(token_payload)
    logging.info(
        "Spotify OAuth authorization stored for Telegram user %s",
        pending.telegram_user_id,
    )
    return pending.telegram_user_id


def get_spotify_user_access_token() -> str | None:
    """Return a valid user OAuth token, refreshing it when necessary."""

    payload = _read_token_payload()
    if not payload or not payload.get("refresh_token"):
        return None
    access_token = payload.get("access_token")
    try:
        expires_at = float(payload.get("expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0
    if (
        isinstance(access_token, str)
        and access_token
        and expires_at > time.time() + SPOTIFY_TOKEN_REFRESH_MARGIN_SEC
    ):
        return access_token

    client_id, client_secret, _redirect_uri = _oauth_config()
    if not client_id or not client_secret:
        raise SpotifyOAuthError("missing_client_credentials")
    try:
        response = requests.post(
            SPOTIFY_TOKEN_URL,
            auth=(client_id, client_secret),
            data={
                "grant_type": "refresh_token",
                "refresh_token": payload["refresh_token"],
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        raise SpotifyOAuthError("network_error") from exc

    try:
        refreshed = _validated_token_payload(
            _parse_token_response(response),
            previous=payload,
        )
    except SpotifyOAuthError as exc:
        if exc.reason == "invalid_grant":
            clear_spotify_authorization()
            raise SpotifyOAuthError("reauthorization_required", exc.status_code) from exc
        raise
    _write_token_payload(refreshed)
    logging.info("Spotify OAuth access token refreshed")
    return str(refreshed["access_token"])


def _oauth_result_html(success: bool, detail: str = "") -> bytes:
    title = "Spotify połączone" if success else "Nie udało się połączyć Spotify"
    color = "#1db954" if success else "#d93025"
    message = (
        "Autoryzacja zakończona. Wróć do Telegrama i wyślij link do playlisty ponownie."
        if success
        else detail or "Uruchom /spotify_login w Telegramie i spróbuj ponownie."
    )
    document = f"""<!doctype html>
<html lang="pl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{html.escape(title)}</title></head>
<body style="font-family:system-ui;max-width:44rem;margin:4rem auto;padding:0 1rem">
<h1 style="color:{color}">{html.escape(title)}</h1><p>{html.escape(message)}</p>
</body></html>"""
    return document.encode("utf-8")


class _SpotifyOAuthCallbackHandler(BaseHTTPRequestHandler):
    """Minimal callback endpoint; query strings are deliberately never logged."""

    server_version = "SpotifyOAuthCallback/1.0"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)
        configured_path = getattr(self.server, "spotify_callback_path", "/spotify/callback")
        if parsed.path not in {"/", configured_path, f"{configured_path}/"}:
            self._respond(404, _oauth_result_html(False, "Nie znaleziono tej strony."))
            return

        query = parse_qs(parsed.query)
        if query.get("error"):
            self._respond(400, _oauth_result_html(False, "Nie udzielono zgody Spotify."))
            return
        code = (query.get("code") or [""])[0]
        state = (query.get("state") or [""])[0]
        try:
            complete_spotify_authorization(code, state)
        except SpotifyOAuthError as exc:
            logging.warning(
                "Spotify OAuth callback failed: reason=%s status=%s",
                exc.reason,
                exc.status_code,
            )
            self._respond(400, _oauth_result_html(False))
            return
        except Exception:
            logging.exception("Unexpected Spotify OAuth callback failure")
            self._respond(500, _oauth_result_html(False))
            return
        self._respond(200, _oauth_result_html(True))


def start_spotify_oauth_callback_server() -> ThreadingHTTPServer | None:
    """Start the local callback listener when OAuth is configured."""

    global _callback_server
    client_id, client_secret, redirect_uri = _oauth_config()
    if not client_id or not client_secret or not redirect_uri:
        logging.info("Spotify OAuth callback server disabled: incomplete configuration")
        return None

    parsed_redirect = urlparse(redirect_uri)
    if parsed_redirect.scheme != "https" or not parsed_redirect.netloc:
        logging.error("Spotify OAuth callback server disabled: invalid redirect URI")
        return None
    host = str(get_runtime_value("SPOTIFY_OAUTH_CALLBACK_HOST", "127.0.0.1"))
    try:
        port = int(get_runtime_value("SPOTIFY_OAUTH_CALLBACK_PORT", "8091"))
    except (TypeError, ValueError):
        logging.error("Spotify OAuth callback server disabled: invalid callback port")
        return None

    with _server_lock:
        if _callback_server is not None:
            return _callback_server
        try:
            server = ThreadingHTTPServer((host, port), _SpotifyOAuthCallbackHandler)
        except OSError as exc:
            logging.error("Cannot start Spotify OAuth callback server: %s", exc)
            return None
        server.daemon_threads = True
        server.spotify_callback_path = parsed_redirect.path or "/spotify/callback"  # type: ignore[attr-defined]
        thread = threading.Thread(
            target=server.serve_forever,
            name="spotify-oauth-callback",
            daemon=True,
        )
        thread.start()
        _callback_server = server
    logging.info("Spotify OAuth callback server listening on %s:%s", host, port)
    return server


def stop_spotify_oauth_callback_server() -> None:
    """Stop the callback listener; primarily useful for tests."""

    global _callback_server
    with _server_lock:
        server = _callback_server
        _callback_server = None
    if server is not None:
        server.shutdown()
        server.server_close()
