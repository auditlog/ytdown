"""Tests for the resilient local stdio-to-HTTPS MCP bridge (stdlib only)."""

import io
import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from bot.mcp import local_bridge

TOKEN = "t" * 40


def _lines(output: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in output.getvalue().splitlines() if line.strip()]


def _run(bridge: local_bridge.Bridge, *messages: dict) -> None:
    stdin = io.StringIO("".join(json.dumps(message) + "\n" for message in messages))
    bridge.run(stdin)


def _request(request_id, method="tools/list", params=None) -> dict:
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def test_forwards_requests_over_http_and_writes_the_reply():
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen["authorization"] = self.headers["Authorization"]
            seen["accept"] = self.headers["Accept"]
            reply = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": {"tools": []}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    output = io.StringIO()
    try:
        bridge = local_bridge.Bridge(f"http://127.0.0.1:{server.server_port}/mcp", TOKEN, output=output)
        _run(bridge, _request(7))
    finally:
        server.shutdown()

    assert _lines(output) == [{"jsonrpc": "2.0", "id": 7, "result": {"tools": []}}]
    assert seen["authorization"] == f"Bearer {TOKEN}"
    assert "application/json" in seen["accept"]


class FakeTransport:
    """Scripted post(): each entry is a (status, body) reply or an exception.

    Bridges using it run with workers=1 so replies are consumed in order.
    """

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, url, payload, headers, timeout):
        self.calls.append({"payload": json.loads(payload), "headers": dict(headers)})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _ok(request_id, result=None):
    return 200, json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result or {}}).encode()


def test_network_failure_answers_that_request_and_the_bridge_keeps_working():
    # After the laptop sleeps, the first call fails; the bridge must survive it
    # instead of exiting, which Claude Desktop would show as "server unavailable".
    down = urllib.error.URLError("Name or service not known")
    transport = FakeTransport(down, down, _ok(2))
    output = io.StringIO()
    bridge = local_bridge.Bridge("https://example.ts.net/mcp", TOKEN, output=output, post=transport, retry_delay=0, workers=1)

    _run(bridge, _request(1), _request(2))

    replies = {reply["id"]: reply for reply in _lines(output)}
    assert replies[1]["error"]["code"] == local_bridge.NETWORK_ERROR_CODE
    assert "ytdown" in replies[1]["error"]["message"]
    assert replies[2]["result"] == {}
    assert len(transport.calls) == 3  # one retry for the failed request


def test_a_single_retry_recovers_from_a_brief_network_drop():
    transport = FakeTransport(urllib.error.URLError("timed out"), _ok(5, {"ok": True}))
    output = io.StringIO()
    bridge = local_bridge.Bridge("https://example.ts.net/mcp", TOKEN, output=output, post=transport, retry_delay=0, workers=1)

    _run(bridge, _request(5))

    assert _lines(output) == [{"jsonrpc": "2.0", "id": 5, "result": {"ok": True}}]


def test_notifications_get_no_reply_even_when_the_network_fails():
    transport = FakeTransport((202, b""), OSError("network unreachable"), OSError("network unreachable"), _ok(3))
    output = io.StringIO()
    bridge = local_bridge.Bridge("https://example.ts.net/mcp", TOKEN, output=output, post=transport, retry_delay=0, workers=1)

    _run(
        bridge,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}},
        _request(3),
    )

    assert [reply["id"] for reply in _lines(output)] == [3]


def test_a_rejected_token_is_reported_as_such():
    transport = FakeTransport((401, b'{"error": "unauthorized"}'))
    output = io.StringIO()
    bridge = local_bridge.Bridge("https://example.ts.net/mcp", TOKEN, output=output, post=transport, retry_delay=0, workers=1)

    _run(bridge, _request(9))

    error = _lines(output)[0]["error"]
    assert "token" in error["message"].lower()
    assert len(transport.calls) == 1  # authentication errors are not retried


def test_negotiated_protocol_version_is_sent_after_initialize():
    transport = FakeTransport(_ok(0, {"protocolVersion": "2025-06-18", "capabilities": {}}), _ok(1))
    bridge = local_bridge.Bridge(
        "https://example.ts.net/mcp", TOKEN, output=io.StringIO(), post=transport, retry_delay=0, workers=1,
    )

    _run(bridge, _request(0, "initialize", {"protocolVersion": "2025-06-18"}), _request(1))

    assert transport.calls[1]["headers"]["MCP-Protocol-Version"] == "2025-06-18"


def test_reads_the_token_from_a_dotenv_file(tmp_path):
    token_file = tmp_path / "mcp.env"
    token_file.write_text(f'# ytdown MCP\nYTDOWN_MCP_TOKEN="{TOKEN}"\n', encoding="utf-8")

    assert local_bridge.read_token(token_file) == TOKEN


@pytest.mark.parametrize("url", ["http://rpi5a.tail3e20a0.ts.net:9443/mcp", "https://user:pw@host/mcp"])
def test_refuses_plain_http_off_loopback_and_credentials_in_the_url(url):
    with pytest.raises(ValueError):
        local_bridge.Bridge(url, TOKEN, output=io.StringIO())


def test_refuses_a_missing_or_short_token():
    with pytest.raises(ValueError):
        local_bridge.Bridge("https://example.ts.net/mcp", "short", output=io.StringIO())
