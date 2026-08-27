"""Spotify user OAuth flow and token persistence tests."""

import json
import stat
from urllib.parse import parse_qs, urlparse

import pytest

from bot import spotify_oauth as oauth


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def oauth_config(monkeypatch, tmp_path):
    values = {
        "SPOTIFY_CLIENT_ID": "client-id",
        "SPOTIFY_CLIENT_SECRET": "client-secret",
        "SPOTIFY_REDIRECT_URI": "https://example.test/spotify/callback",
        "SPOTIFY_OAUTH_TOKEN_FILE": str(tmp_path / "spotify_oauth.json"),
        "SPOTIFY_OAUTH_CALLBACK_HOST": "127.0.0.1",
        "SPOTIFY_OAUTH_CALLBACK_PORT": "0",
    }
    monkeypatch.setattr(
        oauth,
        "get_runtime_value",
        lambda key, default="": values.get(key, default),
    )
    oauth._pending_states.clear()
    oauth.clear_spotify_authorization()
    yield values
    oauth._pending_states.clear()
    oauth.clear_spotify_authorization()
    oauth.stop_spotify_oauth_callback_server()


def test_authorization_url_contains_scopes_and_one_use_state(oauth_config):
    url = oauth.build_spotify_authorization_url(123)
    query = parse_qs(urlparse(url).query)

    assert query["client_id"] == ["client-id"]
    assert query["redirect_uri"] == [oauth_config["SPOTIFY_REDIRECT_URI"]]
    assert set(query["scope"][0].split()) == set(oauth.SPOTIFY_OAUTH_SCOPES)
    assert query["state"][0] in oauth._pending_states
    assert "client-secret" not in url


def test_complete_authorization_stores_mode_600_token(
    oauth_config,
    monkeypatch,
):
    url = oauth.build_spotify_authorization_url(321)
    state = parse_qs(urlparse(url).query)["state"][0]
    def post(*args, **kwargs):
        return FakeResponse(
            200,
            {
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "expires_in": 3600,
                "scope": " ".join(oauth.SPOTIFY_OAUTH_SCOPES),
            },
        )
    monkeypatch.setattr(oauth.requests, "post", post)
    monkeypatch.setattr(
        oauth.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(200, {"items": []}),
    )

    telegram_user_id = oauth.complete_spotify_authorization("one-use-code", state)

    token_path = oauth._token_file_path()
    payload = json.loads(token_path.read_text(encoding="utf-8"))
    assert telegram_user_id == 321
    assert payload["access_token"] == "access-token"
    assert payload["refresh_token"] == "refresh-token"
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
    assert state not in oauth._pending_states


def test_refresh_preserves_existing_refresh_token(oauth_config, monkeypatch):
    oauth._write_token_payload(
        {
            "access_token": "expired",
            "refresh_token": "refresh-token",
            "expires_at": 0,
            "scope": " ".join(oauth.SPOTIFY_OAUTH_SCOPES),
        }
    )
    monkeypatch.setattr(
        oauth.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(
            200,
            {"access_token": "new-access-token", "expires_in": 3600},
        ),
    )

    assert oauth.get_spotify_user_access_token() == "new-access-token"
    stored = json.loads(oauth._token_file_path().read_text(encoding="utf-8"))
    assert stored["refresh_token"] == "refresh-token"


def test_complete_authorization_rejects_unknown_state(oauth_config, monkeypatch):
    post = pytest.fail
    monkeypatch.setattr(oauth.requests, "post", post)

    with pytest.raises(oauth.SpotifyOAuthError, match="state_mismatch"):
        oauth.complete_spotify_authorization("code", "unknown-state")


def test_callback_server_binds_only_configured_local_host(oauth_config, monkeypatch):
    class FakeServer:
        def __init__(self, address, handler):
            self.server_address = (address[0], 43123)
            self.handler = handler
            self.daemon_threads = False

        def serve_forever(self):
            return None

        def shutdown(self):
            return None

        def server_close(self):
            return None

    class FakeThread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            return None

    monkeypatch.setattr(oauth, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(oauth.threading, "Thread", FakeThread)

    server = oauth.start_spotify_oauth_callback_server()

    assert server is not None
    assert server.server_address[0] == "127.0.0.1"
    assert server.server_address[1] > 0
