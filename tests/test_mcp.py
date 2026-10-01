"""MCP boundary, persistence, transport, and disposable-worker regression tests."""

import asyncio
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock

import pytest

from bot.mcp.jobs import JobStore
from bot.mcp.policy import VIDEO_QUALITIES, Limits, UserError, youtube_url

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "url",
    [
        "http://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ",
        "https://youtube.com@127.0.0.1/watch?v=dQw4w9WgXcQ",
        "https://127.0.0.1/watch?v=dQw4w9WgXcQ",
        "https://youtube.com:8443/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/redirect?q=https://127.0.0.1",
        "https://youtube.com/playlist?list=123",
        "https://youtu.be/../../etc/passwd",
        "https://youtube.com/watch?v=dQw4w9WgXcQ&v=aaaaaaaaaaa",
        "https://youtube.com\n/watch?v=dQw4w9WgXcQ",
        "file:///etc/passwd",
    ],
)
def test_reject_unsafe_urls(url):
    with pytest.raises(ValueError):
        youtube_url(url)


@pytest.mark.parametrize(
    "url",
    [
        URL + "&list=ignore&t=30",
        "https://youtu.be/dQw4w9WgXcQ?si=tracking",
        "https://m.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
    ],
)
def test_urls_are_canonicalized(url):
    assert youtube_url(url) == URL


def completed_record(store, job_id="a" * 32, name="transcript.md"):
    directory = store.root / job_id
    directory.mkdir(exist_ok=True)
    (directory / name).write_text("Zażółć gęślą jaźń", encoding="utf-8")
    record = {
        "job_id": job_id,
        "operation": "transcribe",
        "status": "completed",
        "created_at": time.time(),
        "finished_at": time.time(),
        "artifacts": [{"name": name, "size_bytes": 26}],
    }
    store.records[job_id] = record
    store._save(record)
    return record


@pytest.mark.asyncio
async def test_artifact_pagination_and_path_isolation(tmp_path):
    store = JobStore(tmp_path)
    await store.open()
    try:
        record = completed_record(store)
        job_id = record["job_id"]
        page = store.read(job_id, "transcript.md", limit=3)
        assert page["content"] == "Zaż" and page["next_offset"] == 3
        assert store.read(job_id, "transcript.md", offset=3)["content"] == "ółć gęślą jaźń"
        assert store.read(job_id, "transcript.md", offset=999)["eof"]
        for job, name in [("../", "secret"), (job_id, "../job.json"), (job_id, "job.json")]:
            with pytest.raises(ValueError):
                store.read(job, name)
        artifact = tmp_path / job_id / "transcript.md"
        artifact.unlink()
        artifact.symlink_to(tmp_path / job_id / "job.json")
        with pytest.raises(ValueError):
            store.read(job_id, "transcript.md")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_restart_cleanup_rate_limit_and_exclusive_owner(tmp_path):
    store = JobStore(tmp_path, Limits(jobs_per_hour=1))
    await store.open()
    competing = JobStore(tmp_path)
    with pytest.raises(RuntimeError, match="already owns"):
        await competing.open()
    record = completed_record(store)
    record["status"] = "running"
    store._save(record)
    await store.close()
    await store.open()
    try:
        assert store.get(record["job_id"])["status"] == "failed"
        with pytest.raises(ValueError, match="godzinowy"):
            store.start(URL, "audio")
        old = completed_record(store, "b" * 32)
        old.update(created_at=0, finished_at=0)
        store.cleanup()
        assert not (tmp_path / old["job_id"]).exists()
        assert (tmp_path / record["job_id"]).exists()
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_subprocess_cancel_timeout_and_disk_limit(tmp_path, monkeypatch):
    # Real disposable child processes, with network/provider operations replaced.
    original_spawn = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        return await original_spawn(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    store = JobStore(tmp_path, Limits(max_active=1, min_free_bytes=1, timeout_seconds=1))
    await store.open()
    try:
        first = store.start(URL, "audio")
        with pytest.raises(ValueError, match="równoległych"):
            store.start(URL, "audio")
        cancelled = await store.cancel(first["job_id"])
        assert cancelled["status"] == "cancelled"
        second = store.start(URL, "video")
        await asyncio.wait_for(store.tasks[second["job_id"]], timeout=5)
        assert "czasu" in store.get(second["job_id"])["error"]
        assert not store.processes and not store.tasks
        store.limits = Limits(max_job_bytes=1)
        with pytest.raises(UserError, match="limit miejsca"):
            store._check_disk(tmp_path)
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("quality", ["best", "2160p", "4320p"])
async def test_completed_worker_is_persisted_and_files_can_be_read(tmp_path, monkeypatch, quality):
    original_spawn = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        directory = args[3]
        script = (
            "import json,sys; from pathlib import Path; p=Path(sys.argv[1]); request=json.load(sys.stdin); "
            "(p/'media.mp3').write_bytes(b'fake media'); "
            "(p/'result.json').write_text(json.dumps({'result': {'title':'Video', "
            "'quality':request['quality'], 'artifacts':['media.mp3']}}))"
        )
        return await original_spawn(sys.executable, "-c", script, directory, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    store = JobStore(tmp_path, Limits(min_free_bytes=1))
    await store.open()
    try:
        with pytest.raises(ValueError, match="jakość"):
            store.start(URL, "video", quality="bestvideo+bestaudio")
        record = store.start(URL, "video", quality=quality)
        await asyncio.wait_for(store.tasks[record["job_id"]], timeout=5)
        assert store.get(record["job_id"])["status"] == "completed"
        assert store.get(record["job_id"])["result"]["quality"] == quality
        assert store.read(record["job_id"], "media.mp3")["encoding"] == "base64"
    finally:
        await store.close()
    await store.open()
    try:
        assert store.get(record["job_id"])["result"]["title"] == "Video"
        assert store.get(record["job_id"])["quality"] == quality
    finally:
        await store.close()


@pytest.mark.parametrize(
    "operation, quality",
    [("audio", "best"), ("video", "best"), ("video", "2160p"), ("video", "4320p")],
)
def test_worker_reuses_download_service_without_cookies_or_title_paths(
    tmp_path, monkeypatch, operation, quality,
):
    import yt_dlp

    from bot.mcp.worker import execute
    from bot.services import download_service

    fake_ydl = Mock()
    fake_ydl.__enter__ = Mock(return_value=fake_ydl)
    fake_ydl.__exit__ = Mock(return_value=False)
    fake_ydl.extract_info.return_value = {"title": "../../secret", "duration": 15}
    options = []

    def ydl(opts):
        options.append(opts)
        return fake_ydl

    def download(plan):
        assert plan.output_path == str(tmp_path / "media")
        assert "cookiefile" not in plan.ydl_opts
        assert plan.ydl_opts["noplaylist"] is True
        assert plan.format_choice == (quality if operation == "video" else "mp3")
        path = tmp_path / ("media.mp4" if operation == "video" else "media.mp3")
        path.write_bytes(b"audio")
        return download_service.DownloadResult(str(path), 0.1)

    monkeypatch.setattr(yt_dlp, "YoutubeDL", ydl)
    monkeypatch.setattr(download_service, "execute_download_plan", download)
    request = dict(url=URL, operation=operation, quality=quality, use_cookies=False, **asdict(Limits()))
    result = execute(request, tmp_path)
    assert result["artifacts"] == ["media.mp4" if operation == "video" else "media.mp3"]
    assert "cookiefile" not in options[0]
    fake_ydl.extract_info.return_value["duration"] = 100000
    with pytest.raises(UserError, match="długości"):
        execute(request, tmp_path)


def test_http_auth_schema_artifacts_and_origin(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    from starlette.testclient import TestClient

    from bot.mcp.server import create_http_app, create_server

    store = JobStore(tmp_path, Limits(min_free_bytes=1))
    server = create_server(store)
    token = "t" * 48
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}
    app = create_http_app(server, store, token)
    original_spawn = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        return await original_spawn(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with TestClient(app, base_url="http://localhost") as client:
        for method in ["GET", "POST", "DELETE"]:
            assert client.request(method, "/mcp").status_code == 401
        assert client.get("/artifacts/a/b").status_code == 401
        assert (
            client.get(
                "/mcp",
                headers=[
                    ("Authorization", f"Bearer {token}"),
                    ("Authorization", f"Bearer {token}"),
                ],
            ).status_code
            == 401
        )
        assert (
            client.post("/mcp", headers={**headers, "Authorization": "Bearer wrong"}).status_code
            == 401
        )
        assert client.post("/mcp", headers=headers, content="x" * 65537).status_code == 413
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        assert (
            client.post("/mcp", headers={**headers, "Host": "evil.test"}, json=payload).status_code
            == 421
        )
        assert (
            client.post(
                "/mcp", headers={**headers, "Origin": "https://evil.test"}, json=payload
            ).status_code
            == 403
        )
        response = client.post("/mcp", headers=headers, json=payload)
        assert response.status_code == 200
        tools = {t["name"]: t for t in response.json()["result"]["tools"]}
        assert set(tools) == {"start_job", "get_job", "list_jobs", "cancel_job", "read_artifact"}
        assert tools["start_job"]["annotations"]["readOnlyHint"] is False
        quality_schema = tools["start_job"]["inputSchema"]["properties"]["quality"]
        assert quality_schema["enum"] == list(VIDEO_QUALITIES)
        assert quality_schema["default"] == "best"
        assert tools["read_artifact"]["annotations"]["readOnlyHint"] is True
        record = completed_record(store)
        # A second stateless request must preserve the application-owned job store.
        payload.update(
            method="tools/call",
            params={"name": "get_job", "arguments": {"job_id": record["job_id"]}},
        )
        response = client.post("/mcp", headers=headers, json=payload)
        assert response.json()["result"]["isError"] is False
        assert record["job_id"] in response.text
        url = f"/artifacts/{record['job_id']}/transcript.md"
        assert client.get(url, headers=headers).text == "Zażółć gęślą jaźń"
        assert client.get(url, headers=headers).headers["cache-control"] == "no-store"
        assert (
            client.get(f"/artifacts/{record['job_id']}/job.json", headers=headers).status_code
            == 404
        )
        payload["params"] = {"name": "start_job", "arguments": {"url": URL, "operation": "shell"}}
        assert client.post("/mcp", headers=headers, json=payload).json()["result"]["isError"]
        payload["params"] = {
            "name": "start_job",
            "arguments": {"url": URL, "operation": "video", "quality": "bestvideo+bestaudio"},
        }
        assert client.post("/mcp", headers=headers, json=payload).json()["result"]["isError"]
        payload["params"] = {"name": "start_job", "arguments": {"url": URL, "operation": "audio"}}
        started = client.post("/mcp", headers=headers, json=payload).json()["result"]
        assert not started["isError"], started
        job_id = json.loads(started["content"][0]["text"])["job_id"]
        payload["params"] = {"name": "get_job", "arguments": {"job_id": job_id}}
        running = client.post("/mcp", headers=headers, json=payload).json()["result"]
        assert json.loads(running["content"][0]["text"])["status"] in {"queued", "running"}
        payload["params"] = {"name": "cancel_job", "arguments": {"job_id": job_id}}
        cancelled = client.post("/mcp", headers=headers, json=payload).json()["result"]
        assert json.loads(cancelled["content"][0]["text"])["status"] == "cancelled"


@pytest.mark.parametrize("operation", ["transcribe", "summarize"])
def test_worker_transcript_and_summary_outputs(tmp_path, monkeypatch, operation):
    import yt_dlp

    from bot import transcription_pipeline, transcription_providers
    from bot.mcp.worker import execute
    from bot.services import download_service

    ydl = Mock()
    ydl.__enter__ = Mock(return_value=ydl)
    ydl.__exit__ = Mock(return_value=False)
    ydl.extract_info.return_value = {"title": "Video", "duration": 30}
    monkeypatch.setattr(yt_dlp, "YoutubeDL", lambda options: ydl)
    monkeypatch.setenv("GROQ_API_KEY", "test-groq")
    monkeypatch.setenv("CLAUDE_API_KEY", "test-claude")

    def download(plan):
        path = tmp_path / "media.mp3"
        path.write_bytes(b"audio")
        return download_service.DownloadResult(str(path), 0.1)

    def transcribe(source, output, **kwargs):
        assert kwargs["get_api_key_fn"]() == "test-groq"
        assert kwargs["get_claude_api_key_fn"]() == ""
        assert kwargs["language"] == "pl"
        path = tmp_path / "media_transcript.md"
        path.write_text("Transcript", encoding="utf-8")
        (tmp_path / "media_part1_transcript.txt").write_text("chunk")
        return str(path)

    monkeypatch.setattr(download_service, "execute_download_plan", download)
    monkeypatch.setattr(transcription_pipeline, "transcribe_mp3_file", transcribe)
    summary = Mock(return_value="Summary")
    monkeypatch.setattr(transcription_providers, "generate_summary", summary)
    (tmp_path / "job.json").write_text("{}")
    request = dict(
        url=URL,
        operation=operation,
        language="pl",
        summary_type=2,
        use_cookies=False,
        **asdict(Limits()),
    )
    result = execute(request, tmp_path)
    assert (tmp_path / "job.json").exists()
    assert not (tmp_path / "media.mp3").exists()
    assert not (tmp_path / "media_part1_transcript.txt").exists()
    assert "media_transcript.md" in result["artifacts"]
    if operation == "summarize":
        assert "summary.md" in result["artifacts"]
        summary.assert_called_once_with("Transcript", 2, api_key="test-claude")
    else:
        summary.assert_not_called()


def test_package_import_does_not_bootstrap_telegram():
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, bot.mcp.__main__; "
            "assert 'bot.config' not in sys.modules; "
            "assert 'bot.telegram_callbacks' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("token", ["", "short", "x" * 31, "a b" * 20, "ż" * 40])
def test_http_rejects_invalid_token_configuration(token):
    pytest.importorskip("mcp")
    from bot.mcp.server import PrivateHTTP

    with pytest.raises(ValueError, match="YTDOWN_MCP_TOKEN"):
        PrivateHTTP(None, token)


@pytest.mark.asyncio
async def test_real_stdio_handshake_and_validation(tmp_path):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "bot.mcp", "--data-dir", str(tmp_path)],
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
        result = await session.initialize()
        assert result.serverInfo.name == "ytdown"
        assert len((await session.list_tools()).tools) == 5
        result = await session.call_tool(
            "start_job", {"url": "https://127.0.0.1/", "operation": "audio"}
        )
        assert result.isError
        assert not list(tmp_path.glob("*/job.json"))
