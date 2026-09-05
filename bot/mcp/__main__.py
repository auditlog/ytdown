"""Run with python -m bot.mcp (stdio by default)."""

import argparse
import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from bot.mcp.policy import Limits


def main():
    parser = argparse.ArgumentParser(description="Private, single-owner ytdown MCP server")
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--port", type=int, default=8092)
    parser.add_argument(
        "--data-dir", type=Path, default=Path(__file__).resolve().parents[2] / "mcp_data"
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        help="Exact proxy hostname, e.g. pi.example.ts.net",
    )
    parser.add_argument(
        "--allowed-origin", action="append", default=[], help="Exact browser origin, if needed"
    )
    parser.add_argument(
        "--use-youtube-cookies",
        action="store_true",
        help="Allow access using the existing YouTube cookie jar",
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be in 1..65535")
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    logging.basicConfig(level=logging.INFO)
    # New files are owner-only, including provider output created by child processes.
    os.umask(0o077)
    try:
        from bot.mcp.jobs import JobStore
        from bot.mcp.server import create_http_app, create_server, run_stdio
    except ModuleNotFoundError as exc:
        if exc.name == "mcp":
            parser.error("Install MCP dependencies: pip install -r requirements-mcp.txt")
        raise
    try:
        limits = Limits(
            **{
                name: int(os.environ.get(f"YTDOWN_MCP_{name.upper()}", field.default))
                for name, field in Limits.__dataclass_fields__.items()
            }
        )
        store = JobStore(args.data_dir, limits, use_cookies=args.use_youtube_cookies)
        server = create_server(
            store,
            port=args.port,
            allowed_hosts=args.allowed_host,
            allowed_origins=args.allowed_origin,
        )
        if args.transport == "stdio":
            asyncio.run(run_stdio(server, store))
        else:
            import uvicorn

            app = create_http_app(server, store, os.environ.get("YTDOWN_MCP_TOKEN", ""))
            uvicorn.run(
                app, host="127.0.0.1", port=args.port, proxy_headers=False, access_log=False
            )
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
