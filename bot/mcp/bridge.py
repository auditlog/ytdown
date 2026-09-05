"""Connect a local stdio MCP client to the shared authenticated HTTP server."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from urllib.parse import urlsplit

import anyio
import httpx
from dotenv import dotenv_values
from mcp.client.streamable_http import streamable_http_client
from mcp.server.stdio import stdio_server


def validate_endpoint(url: str) -> str:
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


async def bridge(url: str, token: str):
    validate_endpoint(url)
    if len(token) < 32 or not token.isascii() or any(char.isspace() for char in token):
        raise ValueError("Missing or invalid YTDOWN_MCP_TOKEN in the token file.")
    async with (
        anyio.create_task_group() as group,
        httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(30, read=300),
            follow_redirects=False,
            trust_env=False,
        ) as client,
        stdio_server() as (local_read, local_write),
        streamable_http_client(url, http_client=client, terminate_on_close=False) as (
            remote_read,
            remote_write,
            _get_session,
        ),
    ):

        async def forward(reader, writer):
            try:
                async for message in reader:
                    if isinstance(message, Exception):
                        raise RuntimeError(
                            "MCP transport failed; check endpoint and credentials."
                        ) from None
                    await writer.send(message)
            finally:
                group.cancel_scope.cancel()

        group.start_soon(forward, local_read, remote_write)
        group.start_soon(forward, remote_read, local_write)
        await anyio.sleep_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8092/mcp")
    parser.add_argument(
        "--token-file",
        type=Path,
        required=True,
        help="Private dotenv file containing YTDOWN_MCP_TOKEN",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    try:
        token = dotenv_values(args.token_file).get("YTDOWN_MCP_TOKEN") or ""
        anyio.run(bridge, args.url, token)
    except (OSError, ValueError, RuntimeError, ExceptionGroup):
        parser.exit(1, "Cannot connect to ytdown MCP. Check the server, URL and token file.\n")


if __name__ == "__main__":
    main()
