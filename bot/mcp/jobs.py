"""Bounded subprocess jobs and isolated JSON persistence for one MCP owner."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import re
import secrets
import shutil
import signal
import sys
import time
from dataclasses import asdict
from pathlib import Path

from bot.mcp.policy import VIDEO_QUALITIES, Limits, UserError, youtube_url

JOB_ID = re.compile(r"[a-f0-9]{32}")
OPERATIONS = {"info", "audio", "video", "transcribe", "summarize"}


class JobStore:
    """One process owns a directory; no Telegram history or user state is shared."""

    def __init__(self, root: Path, limits: Limits | None = None, *, use_cookies=False):
        self.root = root.resolve()
        self.limits = limits or Limits()
        self.use_cookies = use_cookies
        self.tasks: dict[str, asyncio.Task] = {}
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.records: dict[str, dict] = {}
        self._lock_file = None
        self._maintenance = None

    async def open(self):
        if os.name != "posix":
            raise RuntimeError("MCP workers require Linux/macOS/WSL process groups.")
        import fcntl

        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock_file = (self.root / ".lock").open("a")
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock_file.close()
            self._lock_file = None
            raise RuntimeError("Another MCP server already owns this data directory.") from None
        for path in self.root.iterdir():
            if not JOB_ID.fullmatch(path.name) or path.is_symlink() or not path.is_dir():
                continue
            try:
                record = json.loads((path / "job.json").read_text(encoding="utf-8"))
                if record["job_id"] != path.name:
                    continue
                if record["status"] in {"queued", "running"}:
                    record.update(
                        status="failed",
                        error="Serwer został zrestartowany.",
                        finished_at=time.time(),
                        artifacts=[],
                    )
                    self._clear_outputs(path)
                self.records[path.name] = record
                self._save(record)
            except (OSError, ValueError, KeyError):
                logging.warning("Skipping unreadable MCP job %s", path.name)
        self.cleanup()
        self._maintenance = asyncio.create_task(self._maintain())

    async def close(self):
        if self._maintenance:
            self._maintenance.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._maintenance
        await asyncio.sleep(0)
        for task in list(self.tasks.values()):
            if not task.cancelling():
                task.cancel()
        await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)
        if self._lock_file:
            self._lock_file.close()
            self._lock_file = None

    def _save(self, record):
        path = self.root / record["job_id"]
        temporary = path / "job.tmp"
        temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path / "job.json")

    def get(self, job_id: str) -> dict:
        if not JOB_ID.fullmatch(job_id) or job_id not in self.records:
            raise ValueError("Nie znaleziono zadania.")
        # Do not hand callers mutable internal state.
        return json.loads(json.dumps(self.records[job_id]))

    def list(self, limit: int = 20) -> list[dict]:
        return [
            self.get(r["job_id"])
            for r in sorted(self.records.values(), key=lambda r: r["created_at"], reverse=True)[
                :limit
            ]
        ]

    def start(
        self, url: str, operation: str, language: str | None = None, summary_type: int = 1,
        quality: str = "best",
    ) -> dict:
        if self._lock_file is None:
            raise RuntimeError("MCP job store is not open.")
        url = youtube_url(url)
        if operation not in OPERATIONS or summary_type not in {1, 2, 3, 4}:
            raise ValueError("Nieobsługiwany rodzaj zadania.")
        if quality not in VIDEO_QUALITIES:
            raise ValueError("Nieobsługiwana jakość wideo.")
        if language is not None and not re.fullmatch(r"[a-z]{2,3}", language):
            raise ValueError("Podaj kod języka, np. pl lub en.")
        now = time.time()
        if len(self.tasks) >= self.limits.max_active:
            raise ValueError("Osiągnięto limit równoległych zadań. Spróbuj później.")
        if (
            sum(r["created_at"] > now - 3600 for r in self.records.values())
            >= self.limits.jobs_per_hour
        ):
            raise ValueError("Osiągnięto godzinowy limit zadań.")
        if shutil.disk_usage(self.root).free < self.limits.min_free_bytes:
            raise ValueError("Za mało wolnego miejsca na dysku.")
        job_id = secrets.token_hex(16)
        (self.root / job_id).mkdir(mode=0o700)
        record = {
            "job_id": job_id,
            "operation": operation,
            "url": url,
            "status": "queued",
            "created_at": now,
            "artifacts": [],
        }
        if operation == "video":
            record["quality"] = quality
        self.records[job_id] = record
        self._save(record)
        request = dict(
            url=url,
            operation=operation,
            language=language,
            summary_type=summary_type,
            quality=quality,
            use_cookies=self.use_cookies,
            **asdict(self.limits),
        )
        self.tasks[job_id] = asyncio.create_task(self._run(record, request))
        return self.get(job_id)

    @staticmethod
    async def _stop_process(process):
        # Kill the entire group, including ffmpeg and JS children, even if its leader exited.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()

    @staticmethod
    def _clear_outputs(directory):
        for path in directory.iterdir():
            if path.name in {"job.json", "job.tmp"}:
                continue
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)

    def _check_disk(self, directory):
        used = 0
        for path in directory.rglob("*"):
            # The worker may rename or remove intermediate chunks while we scan.
            with contextlib.suppress(FileNotFoundError):
                if path.is_file() and not path.is_symlink():
                    used += path.stat().st_size
        if used > self.limits.max_job_bytes:
            raise UserError("Przekroczono limit miejsca dla zadania.")
        if shutil.disk_usage(self.root).free < self.limits.min_free_bytes:
            raise UserError("Zadanie zatrzymano z powodu braku miejsca na dysku.")

    async def _run(self, record, request):
        directory = self.root / record["job_id"]
        process = None
        try:
            record["status"] = "running"
            self._save(record)
            worker_env = {
                key: value
                for key, value in os.environ.items()
                if key
                in {
                    "PATH",
                    "HOME",
                    "LANG",
                    "LC_ALL",
                    "TMPDIR",
                    "XDG_CACHE_HOME",
                    "PYTHONDONTWRITEBYTECODE",
                    "SSL_CERT_FILE",
                    "SSL_CERT_DIR",
                    "GROQ_API_KEY",
                    "CLAUDE_API_KEY",
                }
            }
            # Shield process creation so cancellation cannot orphan a just-spawned worker.
            spawning = asyncio.create_task(
                asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "bot.mcp.worker",
                    str(directory),
                    str(os.getpid()),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    cwd=str(Path(__file__).resolve().parents[2]),
                    env=worker_env,
                    start_new_session=True,
                )
            )
            try:
                process = await asyncio.shield(spawning)
            except asyncio.CancelledError:
                process = await spawning
                raise
            self.processes[record["job_id"]] = process
            process.stdin.write(json.dumps(request).encode())
            await process.stdin.drain()
            process.stdin.close()
            async with asyncio.timeout(self.limits.timeout_seconds):
                while process.returncode is None:
                    self._check_disk(directory)
                    await asyncio.sleep(0.5)
                await process.wait()
            self._check_disk(directory)
            if process.returncode != 0:
                raise RuntimeError("worker_exit")
            output = json.loads((directory / "result.json").read_text(encoding="utf-8"))
            if "error" in output:
                record.update(status="failed", error=output["error"])
            else:
                result = output["result"]
                artifacts = []
                for name in result.pop("artifacts"):
                    path = directory / name
                    if Path(name).name != name or path.is_symlink() or not path.is_file():
                        raise RuntimeError("invalid_artifact")
                    artifacts.append({"name": name, "size_bytes": path.stat().st_size})
                record.update(status="completed", result=result, artifacts=artifacts)
        except asyncio.CancelledError:
            record.update(status="cancelled", error="Zadanie anulowano.")
        except TimeoutError:
            record.update(status="failed", error="Przekroczono limit czasu zadania.")
        except UserError as exc:
            record.update(status="failed", error=str(exc))
        except Exception as exc:
            logging.error("MCP job %s failed: %s", record["job_id"], type(exc).__name__)
            record.update(status="failed", error="Zadanie nie powiodło się. Sprawdź logi serwera.")
        finally:
            if process is not None:
                await self._stop_process(process)
            self.processes.pop(record["job_id"], None)
            if record["status"] != "completed":
                self._clear_outputs(directory)
            else:
                (directory / "result.json").unlink(missing_ok=True)
            record["finished_at"] = time.time()
            self._save(record)
            self.tasks.pop(record["job_id"], None)
            logging.info(
                "MCP job %s: %s (%s)", record["job_id"], record["status"], record["operation"]
            )

    async def cancel(self, job_id):
        self.get(job_id)
        task = self.tasks.get(job_id)
        if task:
            # Give the coroutine a chance to enter its cleanup-protected region.
            await asyncio.sleep(0)
            if not task.cancelling():
                task.cancel()
            await task
        return self.get(job_id)

    def artifact_path(self, job_id, name):
        record = self.get(job_id)
        if record["status"] != "completed" or name not in {a["name"] for a in record["artifacts"]}:
            raise ValueError("Nie znaleziono pliku wynikowego.")
        directory = self.root / job_id
        path = directory / name
        if (
            Path(name).name != name
            or directory.is_symlink()
            or path.is_symlink()
            or path.resolve().parent != directory
        ):
            raise ValueError("Nie znaleziono pliku wynikowego.")
        if not path.is_file():
            raise ValueError("Plik wynikowy wygasł lub został usunięty.")
        return path

    def read(self, job_id, name, offset=0, limit=16000):
        if offset < 0 or not 1 <= limit <= 64000:
            raise ValueError("Niepoprawny zakres odczytu.")
        path = self.artifact_path(job_id, name)
        if path.suffix in {".md", ".txt"}:
            # Text offsets are Unicode characters, binary offsets are bytes.
            with path.open(encoding="utf-8") as stream:
                remaining = offset
                while remaining:
                    skipped = stream.read(min(remaining, 64000))
                    if not skipped:
                        break
                    remaining -= len(skipped)
                content = stream.read(limit)
                eof = not stream.read(1)
            encoding = "utf-8"
            count = len(content)
        else:
            with path.open("rb") as stream:
                stream.seek(offset)
                chunk = stream.read(limit)
                eof = not stream.read(1)
            content = base64.b64encode(chunk).decode("ascii")
            encoding = "base64"
            count = len(chunk)
        return {"content": content, "encoding": encoding, "next_offset": offset + count, "eof": eof}

    def cleanup(self):
        # Keep at least one hour of records so retention cannot reset rate limiting.
        cutoff = time.time() - max(3600, self.limits.retention_seconds)
        for job_id, record in list(self.records.items()):
            if (
                job_id not in self.tasks
                and record.get("finished_at", record["created_at"]) < cutoff
            ):
                path = self.root / job_id
                if not path.is_symlink():
                    shutil.rmtree(path)
                del self.records[job_id]

    async def _maintain(self):
        while True:
            await asyncio.sleep(60)
            self.cleanup()
