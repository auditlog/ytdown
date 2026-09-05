# ytdown MCP

For the deployed Raspberry Pi instance, see [Private MCP on rpi5a](mcp-rpi5a.md).

The MCP entry point exposes the existing download/transcription services independently
of Telegram. It supports one trusted owner on Linux, macOS or WSL, Python 3.12+,
and the stable v1 MCP Python SDK. Run the server on the machine with ffmpeg and
the required YouTube JavaScript runtime. Native Windows worker management is not supported.

This implementation is ready for private clients using stdio or authenticated
Streamable HTTP. Public OAuth deployment remains a separate decision. It does
not send Telegram messages or expose Telegram users, PINs, history, or Spotify sessions.

## Private service quick start

Use one HTTP server and a stdio bridge when multiple local clients should share
the same jobs. The bridge keeps the bearer token out of client JSON and model
context. It refuses non-loopback plaintext HTTP and HTTP redirects.

```bash
uv venv --python 3.12 .venv-mcp
uv pip install --python .venv-mcp/bin/python -r requirements-mcp.txt
.venv-mcp/bin/python -m bot.mcp.private

systemctl --user link "$HOME/.config/ytdown/ytdown-mcp.service"
systemctl --user daemon-reload
systemctl --user enable --now ytdown-mcp.service
```

The generator creates an owner-only `~/.config/ytdown/mcp.env`, a user systemd
unit, and `~/.config/ytdown/mcp-stdio` (the bridge launcher). Runtime data is in
`~/.local/state/ytdown/mcp`. Keep these on a filesystem with Unix permissions,
especially when the checkout is on a Windows drive in WSL. Re-running the
generator preserves the token; reload/restart systemd to apply unit changes.

Merge the `ytdown` entry from `~/.config/ytdown/claude-mcp.json` into your local
client configuration. On WSL, `claude-mcp-windows.json` is also generated for
Windows clients; it invokes `wsl.exe` with the current distribution name. This
does not overwrite an existing Claude configuration. Restart the client after
adding the entry. The same `mcp-stdio` launcher can be used as the local MCP
command for Secure MCP Tunnel: both clients then reach this single HTTP process.

```bash
systemctl --user status ytdown-mcp.service
journalctl --user -u ytdown-mcp.service -n 50
systemctl --user restart ytdown-mcp.service
systemctl --user disable --now ytdown-mcp.service
```

The service starts with the user session. If it must run after logout, an
administrator can enable lingering using `sudo loginctl enable-linger "$USER"`.
WSL must itself be running; user systemd does not start a stopped WSL distribution.

To add private access from other devices, first start and sign in to Tailscale:

```bash
sudo systemctl enable --now tailscaled
sudo tailscale up
```

Then regenerate the unit with this host's Tailscale DNS name, restart MCP, and
enable Serve (subject to the host's Tailscale operator permissions):

```bash
.venv-mcp/bin/python -m bot.mcp.private --tailnet-host pi.example.ts.net
systemctl --user daemon-reload
systemctl --user restart ytdown-mcp.service
sudo tailscale serve --bg http://127.0.0.1:8092
```

The remote MCP URL is `https://pi.example.ts.net/mcp`, still requiring the token.
Tailscale Serve stays inside the tailnet; restrict tailnet grants to your devices.
For ChatGPT, create the Secure MCP Tunnel in the intended OpenAI organization/
workspace and configure `~/.config/ytdown/mcp-stdio` as its stdio command. This
requires the account-side tunnel identity and runtime credential described below.

## Install and run a standalone stdio server

From the repository root:

```bash
python -m pip install -r requirements-mcp.txt
# Or:
poetry install -E mcp

python -m bot.mcp
# Or:
poetry run ytdown-mcp
```

The default transport is stdio: stdout is reserved for MCP, logs go to stderr.
Use an absolute interpreter path in client configurations. The server loads
the repository's `.env` without overriding environment variables. Media workers
reuse the shared configuration loader: environment > `.env` > `api_key.md` >
defaults. The legacy loader can emit Telegram/PIN validation warnings and check
config-file permissions, but MCP does not require a Telegram token/PIN and does
not start polling. The MCP transport uses its own access controls.

Required credentials depend on the operation:

| Operation | Output | Credentials |
| --- | --- | --- |
| `info` | Title, duration, canonical URL | None |
| `audio` | MP3 at 192 kbps | None |
| `video` | Video at the highest available source resolution by default | None |
| `transcribe` | Markdown transcript | `GROQ_API_KEY` |
| `summarize` | Transcript and Markdown summary | `GROQ_API_KEY`, `CLAUDE_API_KEY` |

Video accepts `quality`: `best` (default), `4320p`, `2160p`, `1440p`, `1080p`,
`720p`, `480p`, or `360p`. `best` has no resolution ceiling: it selects the
highest source resolution, including 4K, 8K, or higher. Resolution takes priority
over H.264 compatibility; the output may use VP9 or AV1 without re-encoding.
The named presets prefer formats up to the given height, with the existing
fallback to an available source format when none match (for example, portrait
videos). These presets are preferences, not strict resolution limits.
Storage and time limits still apply: the defaults are 10 GiB per media file,
22 GiB temporary storage per job (including source streams and archive copies),
and two hours per job. Video/audio outputs larger than 1000 MiB are automatically
packed without re-encoding into 7z volumes of at most 1000 MiB each.
The result lists all parts and `rozpakowanie.txt`; download every part into the
same directory and extract the `.7z.001` part with 7-Zip. The server requires the
system `7z` executable. Completed archives replace the original media to save space.
Missing 7z, insufficient disk space, or exceeding the media limit produces a safe
job error instead of advertising an incomplete output.
The requested video quality is retained in the job record.

Transcription skips
Claude correction. Choose `transcribe` and let the calling assistant summarize
the result to avoid an extra Anthropic API call. Provider subscriptions in the
ChatGPT/Claude UI do not supply these server-side API credentials.

YouTube cookies are disabled by default. `--use-youtube-cookies` explicitly
enables the existing repository cookie jar for this server. Any client with
access then acts with that YouTube session's permissions.

## Tools and output

1. `start_job(url, operation, language?, summary_type?, quality?)` returns `job_id` immediately.
2. `get_job(job_id)` reports `queued`, `running`, `completed`, `failed`, or `cancelled`.
   Poll every few seconds. On completion it lists artifact names and byte sizes.
3. `read_artifact(job_id, name, offset?, limit?)` reads a completed output in pages.
   Text offsets count Unicode characters; binary offsets count bytes and the
   content is base64. Follow `next_offset` until `eof` is true.
4. `list_jobs(limit?)` retrieves recent jobs; `cancel_job(job_id)` stops a job and
   removes partial outputs. Completed files remain available until expiration.

`summary_type` is 1 (short), 2 (detailed), 3 (bullets), or 4 (tasks by person).
`language` is optional, for example `pl` or `en`. Input is restricted to a single
HTTPS YouTube video, including short links and Shorts. Playlists, live streams,
arbitrary URLs, custom extractor options, local input paths and other platforms
are outside this first MCP interface.

Example tool arguments:

```json
{"url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ","operation":"transcribe","language":"pl"}
```

Large media should be downloaded outside the model context. In HTTP mode,
`GET /artifacts/{job_id}/{name}` streams a completed, advertised file with the
same bearer authentication as `/mcp`. It supports the SDK dependency's HTTP
file response behavior, including range requests. Supply the token from a
client-side secret store; never put it in a prompt or a URL. With stdio, the
owner can retrieve `mcp_data/<job_id>/<name>` on the server, for example over SSH.
These files are not automatically attached to ChatGPT or Claude conversations.

## Access decision

| Variant | Reachability | Authentication | Status |
| --- | --- | --- | --- |
| Local stdio / SSH over Tailscale | Client or SSH process reaches the host | Local OS account / SSH + tailnet grants | Implemented |
| Private HTTP via Tailscale Serve | Clients inside the tailnet | Tailscale grants + random bearer token | Implemented |
| ChatGPT Secure MCP Tunnel | Outbound tunnel from the private network | OpenAI organization/workspace tunnel permissions | Server compatible via stdio; account setup required |
| Common remote URL for ChatGPT and Claude | Public HTTPS gateway to private backend | MCP-compatible OAuth, allowlisted owner, MFA | Proposed; not implemented/deployed |

**Recommended starting point:** keep ytdown private. Use local/SSH stdio for
Claude Desktop and other local MCP clients, and Secure MCP Tunnel for ChatGPT
if the account has the required access. Tailscale Serve shares a service only
inside the tailnet; Tailscale Funnel exposes it to the internet. Do not treat
Funnel as private access control. See [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve).

Tailscale controls who can reach the service; it does not constrain authorized
model actions, API spend, or reading another caller's results. This server's
single-owner boundary means all allowed clients share all MCP jobs and files.
Use separate data directories, processes and credentials for separate owners.

### Claude Desktop through local stdio or Tailscale SSH

Install the entry point with Poetry or `pip install -e '.[mcp]'`. For a server
on the same Linux/macOS machine, add a local MCP configuration:

```json
{
  "mcpServers": {
    "ytdown": {
      "command": "/absolute/path/ytdown/.venv/bin/ytdown-mcp",
      "args": []
    }
  }
}
```

For a Raspberry Pi/VPS reachable by SSH over Tailscale:

```json
{
  "mcpServers": {
    "ytdown": {
      "command": "ssh",
      "args": ["-T", "-o", "BatchMode=yes", "ytdown@pi.example.ts.net", "/opt/ytdown/.venv/bin/ytdown-mcp"]
    }
  }
}
```

Configure SSH trust and restrict the SSH identity to the server command before
using it. Do not start two servers against the same data directory; a process
lock rejects the second one. Local stdio is distinct from a remote connector
added through Claude's Connectors UI: those connections originate in Anthropic's
cloud and require public reachability even from Claude Desktop.
[Claude network requirements](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp).

### Private HTTP through Tailscale

Generate a bearer token locally with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
Store it as `YTDOWN_MCP_TOKEN` in the service environment or an owner-only `.env`.
The server requires at least 32 non-whitespace ASCII characters, but length
validation cannot ensure randomness. Rotate it by replacing it and restarting.

```bash
python -m bot.mcp --transport streamable-http \
  --allowed-host pi.example.ts.net

# On the same host, after deciding to enable tailnet access:
tailscale serve --bg http://127.0.0.1:8092
```

Connect a client that supports an Authorization header to
`https://pi.example.ts.net/mcp`. Limit Tailscale grants to your own identity and
devices. `--allowed-host` adds an exact proxy Host value (include a port if the
proxy sends one). Browser clients may need an exact `--allowed-origin`; wildcard
origins are not enabled by default. `/artifacts` is bearer-protected too.

The bearer middleware is intentionally for private clients, not OAuth discovery.
Do not select "No Authentication" in a cloud connector and expect this HTTP
endpoint to work. Keep the backend on loopback.

### ChatGPT through Secure MCP Tunnel

OpenAI documents an outbound-only tunnel that can forward to stdio or private
HTTP. It requires a Platform `tunnel_id`, a runtime API key, tunnel permissions,
and association with the intended ChatGPT workspace. Availability depends on
account/workspace policy. See the current [Secure MCP Tunnel setup](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels).

After installing the official `tunnel-client` and creating the tunnel, configure
its stdio command as `/opt/ytdown/.venv/bin/ytdown-mcp`. Run it on the server
or another host that can reach the server. In ChatGPT developer mode, create
an MCP connection and choose **Tunnel**, then select the associated tunnel.
[Connect an MCP server to ChatGPT](https://developers.openai.com/apps-sdk/deploy/connect-chatgpt/).

The tunnel client should be the sole stdio launcher for its data directory.
For simultaneous local Claude access, use a separate `--data-dir` (separate
job history), or configure both clients to reach a single private HTTP process.
The HTTP tunnel configuration must forward the bearer credential privately.

### Proposed common remote endpoint: HTTPS + OAuth

If one connection URL must work in both cloud UIs, the proposed deployment is:

```text
ChatGPT / Claude → public HTTPS MCP gateway → private ytdown backend
                            ↓
                 OAuth identity provider (owner + MFA)
```

Use a maintained MCP-compatible OAuth gateway/authorization server. The gateway
must enforce authorization before forwarding to the private bearer-protected
backend, with no direct public backend route. Restrict login to the owner's
identity; do not grant every successfully authenticated account access.

The proposed policy is authorization-code flow with PKCE, exact redirect URIs,
MCP protected-resource metadata and authorization-server discovery, short-lived
audience-bound tokens (e.g. 10 minutes), and revocable refresh credentials.
Validate issuer, signature, audience, expiration and permissions on every call.
Suggested permissions: `ytdown:read`, `ytdown:download`, `ytdown:transcribe`,
`ytdown:summarize`; enforce them in the gateway/tool dispatch, not just in tool
descriptions. These permissions are a proposal, not implemented in this server.
[MCP authorization specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization).

The identity provider, gateway, domain and deployment host remain to be chosen.
A generic web login page alone is insufficient: connector OAuth discovery,
token exchange, refresh and denied-user behavior need end-to-end validation in
both products. Public deployment should also apply gateway request throttling,
a service OS account/container with CPU/memory/storage limits, and an outbound
network policy blocking private/metadata networks while permitting required
media and API services. No gateway or public listener is deployed by this change.

## Enforced limits and operational boundaries

Environment overrides are operator settings, never tool arguments:

| Variable | Default |
| --- | --- |
| `YTDOWN_MCP_MAX_ACTIVE` | 2 jobs |
| `YTDOWN_MCP_JOBS_PER_HOUR` | 10 starts, including metadata/failed/cancelled jobs |
| `YTDOWN_MCP_MAX_DURATION_SECONDS` | 7200; unknown durations rejected for processing |
| `YTDOWN_MCP_MAX_MEDIA_BYTES` | 10737418240 (10 GiB per media file) |
| `YTDOWN_MCP_MAX_JOB_BYTES` | 23622320128 (22 GiB including intermediate output) |
| `YTDOWN_MCP_ARCHIVE_VOLUME_MB` | 1000 (MiB per 7z part; also the auto-packing threshold) |
| `YTDOWN_MCP_MIN_FREE_BYTES` | 2147483648 (2 GiB) |
| `YTDOWN_MCP_TIMEOUT_SECONDS` | 7200 |
| `YTDOWN_MCP_RETENTION_SECONDS` | 86400 after completion; minimum effective retention 1 hour |

Each job runs in a separate process group. Cancellation, timeout and disk
pressure kill the group, including ffmpeg; a worker watchdog exits if its parent
dies. Disk use is sampled every 0.5 seconds and may overshoot between samples;
this is not an OS disk quota. The admission count limits work, not a monetary
budget. Set provider spending limits separately if an exact budget is needed.

Results and metadata persist in `mcp_data/`, separately from Telegram downloads.
Cleanup runs every minute and at startup, excludes active jobs and unrelated
directories, and keeps completed results across restarts. Interrupted jobs are
marked failed and their partial files removed, not silently retried or billed again. Use one server process per
data directory. Keep a custom `--data-dir` private and outside source control.

Input URLs are rebuilt from validated YouTube IDs, dropping all extra query
parameters. Extractors and ffmpeg still make outbound requests, so this is not
a complete network sandbox. Cookie files, credentials, arbitrary paths and
internal job metadata are never exposed as artifacts. Tool annotations identify
mutations; client confirmations are useful, but authorization comes from the
transport and server policy. Treat transcript content as untrusted source data.

## Validation

```bash
python -m pytest tests/test_mcp.py tests/test_mcp_private.py tests/test_mcp_archives.py \
  tests/test_download_budget.py tests/test_download_service.py tests/test_archive.py \
  tests/test_callback_common.py tests/test_callback_download_handlers.py \
  tests/test_callback_transcription_handlers.py tests/test_archive_service.py
```

Tests cover URL restrictions, artifact traversal/symlinks, pagination, exclusive
ownership, restart/retention/rate behavior, actual subprocess cancellation and
timeout, cookie isolation, HTTP authentication and Host/Origin checks, tool
schemas, and a real stdio handshake. They also verify source-resolution selection,
download size and free-space checks, Telegram archive handoff, and multipart
archive extraction with unchanged content (requires the system `7z` executable).
Download and provider operations are mocked to avoid real media transfers or
paid API calls.
