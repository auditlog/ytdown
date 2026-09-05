"""Generate a private local deployment without modifying client or system settings."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shlex
from pathlib import Path

from dotenv import dotenv_values


def unit_quote(value: str | Path) -> str:
    value = str(value)
    if any(char in value for char in "\n\r\0"):
        raise ValueError("Configuration paths must not contain control characters.")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def prepare(
    project: Path,
    config_dir: Path,
    state_dir: Path,
    *,
    port=8092,
    tailnet_host=None,
    tailnet_port=443,
):
    """Keep the bearer credential on a private filesystem outside the checkout."""
    project = project.resolve()
    config_dir = config_dir.resolve()
    state_dir = state_dir.resolve()
    python = project / ".venv-mcp" / "bin" / "python"
    if not python.is_file():
        raise ValueError("Create .venv-mcp and install requirements-mcp.txt first.")
    if not 1 <= port <= 65535:
        raise ValueError("Port must be in 1..65535.")
    if not 1 <= tailnet_port <= 65535:
        raise ValueError("Tailscale port must be in 1..65535.")
    if tailnet_host and not re.fullmatch(
        r"[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.ts\.net", tailnet_host
    ):
        raise ValueError("Expected a Tailscale DNS hostname ending in .ts.net.")
    for directory in (config_dir, state_dir):
        unit_quote(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        if directory.stat().st_mode & 0o077:
            raise ValueError(
                "Use a filesystem supporting private Unix permissions for config and state."
            )
    token_file = config_dir / "mcp.env"
    if token_file.is_symlink():
        raise ValueError("The token file must not be a symlink.")
    if not token_file.exists():
        with token_file.open("x", encoding="utf-8") as stream:
            stream.write(f"YTDOWN_MCP_TOKEN={secrets.token_urlsafe(32)}\n")
    token_file.chmod(0o600)
    token = dotenv_values(token_file).get("YTDOWN_MCP_TOKEN") or ""
    if len(token) < 32 or not token.isascii() or any(char.isspace() for char in token):
        raise ValueError("Existing token is invalid; replace it explicitly.")
    if token_file.stat().st_mode & 0o077:
        raise ValueError("Token file permissions must be private.")

    server_args = [
        str(python),
        "-m",
        "bot.mcp",
        "--transport",
        "streamable-http",
        "--port",
        str(port),
        "--data-dir",
        str(state_dir),
    ]
    if tailnet_host:
        host_header = tailnet_host if tailnet_port == 443 else f"{tailnet_host}:{tailnet_port}"
        server_args += ["--allowed-host", host_header]
    # The worker uses ffmpeg and the operator's installed JS runtime through PATH.
    service = f"""[Unit]
Description=Private ytdown MCP server
After=network.target

[Service]
Type=simple
WorkingDirectory={str(project).replace("%", "%%")}
ExecStart={" ".join(unit_quote(arg) for arg in server_args)}
EnvironmentFile={str(token_file).replace("%", "%%")}
Environment={unit_quote("PATH=" + os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"))}
UMask=0077
Restart=on-failure
RestartSec=5
TimeoutStopSec=15
KillMode=control-group
NoNewPrivileges=true
LimitNOFILE=4096

[Install]
WantedBy=default.target
"""
    unit_path = config_dir / "ytdown-mcp.service"
    unit_path.write_text(service, encoding="utf-8")
    unit_path.chmod(0o600)
    bridge_script = config_dir / "mcp-stdio"
    bridge_script.write_text(
        "#!/bin/sh\nset -eu\n"
        + f"cd {shlex.quote(str(project))}\n"
        + "exec "
        + shlex.join(
            [
                str(python),
                "-m",
                "bot.mcp.bridge",
                "--url",
                f"http://127.0.0.1:{port}/mcp",
                "--token-file",
                str(token_file),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    bridge_script.chmod(0o700)
    client = {"mcpServers": {"ytdown": {"command": str(bridge_script), "args": []}}}
    client_path = config_dir / "claude-mcp.json"
    client_path.write_text(json.dumps(client, indent=2) + "\n", encoding="utf-8")
    client_path.chmod(0o600)
    windows_client_path = None
    if distribution := os.environ.get("WSL_DISTRO_NAME"):
        windows_client = {
            "mcpServers": {
                "ytdown": {
                    "command": "wsl.exe",
                    "args": ["--distribution", distribution, "--exec", str(bridge_script)],
                }
            }
        }
        windows_client_path = config_dir / "claude-mcp-windows.json"
        windows_client_path.write_text(
            json.dumps(windows_client, indent=2) + "\n", encoding="utf-8"
        )
        windows_client_path.chmod(0o600)
    return {
        "unit": str(unit_path),
        "bridge": str(bridge_script),
        "client_config": str(client_path),
        "windows_client_config": str(windows_client_path) if windows_client_path else None,
        "endpoint": f"http://127.0.0.1:{port}/mcp",
        "tailnet_endpoint": (f"https://{host_header}/mcp" if tailnet_host else None),
        "state_dir": str(state_dir),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path.home() / ".config" / "ytdown")
    parser.add_argument(
        "--state-dir", type=Path, default=Path.home() / ".local" / "state" / "ytdown" / "mcp"
    )
    parser.add_argument("--port", type=int, default=8092)
    parser.add_argument("--tailnet-host")
    parser.add_argument("--tailnet-port", type=int, default=443)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        profile = prepare(
            Path(__file__).resolve().parents[2],
            args.config_dir,
            args.state_dir,
            port=args.port,
            tailnet_host=args.tailnet_host,
            tailnet_port=args.tailnet_port,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(profile, indent=2))


if __name__ == "__main__":
    main()
