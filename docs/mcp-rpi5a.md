# Private MCP on rpi5a

The MCP server runs on `rpi5a` next to the Telegram bot. Clients use SSH or private
Tailscale HTTPS to reach it. A Windows/WSL machine only runs the SSH client; it
does not need a local ytdown server or its own copy of the API keys.

## Deployment layout

| Component | Location on rpi5a |
| --- | --- |
| Existing Telegram bot | `/home/pi/ytdown`, system unit `ytdown.service` |
| MCP code and separate virtual environment | `/home/pi/ytdown-mcp` |
| MCP system service | `/etc/systemd/system/ytdown-mcp.service`, runs as `pi` |
| MCP bearer token | `/home/pi/.config/ytdown/mcp.env`, mode `600` |
| SSH/stdio bridge launcher | `/home/pi/.config/ytdown/mcp-stdio` |
| MCP jobs and artifacts | `/home/pi/.local/state/ytdown/mcp` |
| Local HTTP backend | `http://127.0.0.1:8092/mcp` |
| Private HTTPS endpoint | `https://rpi5a.tail3e20a0.ts.net:9443/mcp` |

The MCP tree contains a snapshot of the bot's shared Python services, with the
MCP adapter added. `.env` and `api_key.md` are local symlinks to the existing bot
configuration. YouTube cookies remain disabled. Updating shared bot code does
not automatically update this snapshot: validate and deploy compatible MCP
changes separately. The bot's source tree and virtual environment are unchanged
by the MCP deployment.

The system service starts at boot and runs independently of SSH sessions. It
allows one active MCP job, limits CPU use to two cores and memory to 2 GiB, and
keeps the filesystem read-only except for MCP state and a private temporary
directory. API/provider credentials are read on the Raspberry Pi.

## Video quality

MCP `start_job` accepts `quality="best"` (default) for the highest available source
resolution, or `4320p`, `2160p`, `1440p`, `1080p`, `720p`, `480p`, and `360p`.
Named presets prefer formats up to that height, with a source-format fallback
when none match. `best` has no 4K/8K ceiling. Resolution is selected before codec
compatibility, so higher resolutions may use VP9 or AV1.

MCP allows up to 10 GiB per media file and 22 GiB temporary storage per job, with
a two-hour timeout and a 2 GiB free-space reserve. Files over 1000 MiB are packed
automatically into 7z parts of at most 1000 MiB. Download every advertised part
to one folder and extract `.7z.001`; `rozpakowanie.txt` includes instructions.
The `7z` binary is installed on rpi5a. Completed archives replace their source
media; interrupted jobs are cleaned up, including after a service restart.

In Telegram, select **Video — maksymalna rozdzielczość źródła**. This option is
also available in the large-file menu, alongside 4K, 1440p and lower presets.
With 7z installed, ordinary video/audio downloads also allow files up to 10 GiB.
Files that exceed the active upload transport threshold offer **Wyślij jako 7z**;
the current MTProto setup uses 1900 MiB parts (49 MiB without MTProto).
Transcription retains its previous 1000 MiB input limit. Actual byte and free-space
checks apply even when the source cannot estimate the file size. Separate temporary
download folders are removed after success or failure, except when handed to the
pending archive flow. Archive workspaces and pending files retain the existing
one-hour cleanup policy.

After upgrading MCP, restart the Claude Desktop client to refresh the tool schema.

## Network boundary

Port 443 on this host already uses Tailscale Funnel for existing status and
Spotify callback endpoints, and the panel occupies ports 8443 and 8444. MCP uses
a separate Serve listener on port 9443.
The `AllowFunnel` entry for `rpi5a.tail3e20a0.ts.net:9443` must remain absent or
false. Do not add MCP routes to the existing public listener.

```bash
ssh rpi5a 'tailscale serve status --json'
ssh rpi5a 'sudo systemctl status ytdown-mcp.service'
```

Direct HTTP clients must supply the MCP bearer token over HTTPS. The SSH bridge
reads it on the server, so client JSON contains no token. SSH access and tailnet
grants control which devices can reach the service; all permitted MCP clients
belong to the same owner and share job history.

## Claude Desktop and other local MCP clients

For a Linux/macOS client with the existing `rpi5a` SSH configuration:

```json
{
  "mcpServers": {
    "ytdown": {
      "command": "ssh",
      "args": ["-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "rpi5a", "/home/pi/.config/ytdown/mcp-stdio"]
    }
  }
}
```

For Claude Desktop on Windows, using the configured SSH identity in Debian WSL:

```json
{
  "mcpServers": {
    "ytdown": {
      "command": "wsl.exe",
      "args": ["--distribution", "Debian", "--exec", "ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "rpi5a", "/home/pi/.config/ytdown/mcp-stdio"]
    }
  }
}
```

Merge this entry with existing client settings, then restart the client. SSH
host trust and key access must already work. This is a local MCP connection
whose tools execute remotely on the Raspberry Pi; it is distinct from the
Claude cloud Connectors UI.

## ChatGPT

Use the same `/home/pi/.config/ytdown/mcp-stdio` command as the MCP target for an
OpenAI Secure MCP Tunnel running on `rpi5a`. This requires the account's tunnel
identity, runtime credential and ChatGPT workspace association. The server and
bridge are prepared; creating the OpenAI-side tunnel is an account setup step.
See [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels).

## Operations

```bash
ssh rpi5a 'sudo systemctl restart ytdown-mcp.service'
ssh rpi5a 'sudo journalctl -u ytdown-mcp.service -n 50 --no-pager'
```

To stop MCP and remove only its private listener:

```bash
ssh rpi5a 'sudo systemctl disable --now ytdown-mcp.service'
ssh rpi5a 'sudo tailscale serve --https=9443 off'
```

Avoid `tailscale serve reset`: it would also remove unrelated routes on this
host. The previous local WSL MCP service has been disabled; use the remote
client configuration above.
