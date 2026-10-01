"""Verify the private deployment and stdio-to-HTTP client bridge."""

import asyncio
import json
import socket
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from bot.mcp.bridge import validate_endpoint
from bot.mcp.jobs import JobStore
from bot.mcp.private import prepare
from bot.mcp.server import create_http_app, create_server


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.2:8092/mcp",
        "https://user:password@example.com/mcp",
        "https://example.com/mcp?token=secret",
        "file:///tmp/mcp",
        "https://example.com/mcp#secret",
    ],
)
def test_bridge_refuses_credential_leaks(url):
    with pytest.raises(ValueError):
        validate_endpoint(url)


def test_prepare_keeps_credentials_private_and_preserves_token(tmp_path):
    project = tmp_path / "project with spaces"
    interpreter = project / ".venv-mcp" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    config = tmp_path / "config"
    state = tmp_path / "state"
    result = prepare(project, config, state, tailnet_host="pi.example.ts.net", tailnet_port=8443)
    token = (config / "mcp.env").read_text()
    assert "YTDOWN_MCP_TOKEN=" in token
    assert (config / "mcp.env").stat().st_mode & 0o777 == 0o600
    assert config.stat().st_mode & 0o777 == 0o700
    assert state.stat().st_mode & 0o777 == 0o700
    assert token.strip().split("=", 1)[1] not in json.dumps(result)
    assert "--allowed-host" in Path(result["unit"]).read_text()
    assert "pi.example.ts.net:8443" in Path(result["unit"]).read_text()
    assert result["tailnet_endpoint"] == "https://pi.example.ts.net:8443/mcp"
    assert token.strip().split("=", 1)[1] not in Path(result["client_config"]).read_text()
    prepare(project, config, state)
    assert (config / "mcp.env").read_text() == token


@pytest.mark.asyncio
async def test_real_bridge_handshake_and_clean_disconnect(tmp_path):
    import uvicorn
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    token = "test-token-" + "a" * 40
    token_path = tmp_path / "mcp.env"
    token_path.write_text(f"YTDOWN_MCP_TOKEN={token}\n")
    store = JobStore(tmp_path / "jobs")
    app = create_http_app(create_server(store), store, token)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if serving.done():
                    await serving
                await asyncio.sleep(0.01)
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "bot.mcp.bridge",
                "--url",
                f"http://127.0.0.1:{port}/mcp",
                "--token-file",
                str(token_path),
            ],
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        for _ in range(2):
            async with (
                asyncio.timeout(15),
                stdio_client(params) as (reader, writer),
                ClientSession(reader, writer) as session,
            ):
                assert (await session.initialize()).serverInfo.name == "ytdown"
                assert len((await session.list_tools()).tools) == 5
                assert not (await session.call_tool("list_jobs", {})).isError
        assert store._lock_file is not None
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, timeout=5)
        sock.close()
