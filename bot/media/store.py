"""Bounded, chat-isolated JSON manifests and retained editing sources."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import threading
import time
from dataclasses import asdict
from pathlib import Path

from bot.config import DOWNLOAD_PATH
from bot.media.timeline import Segment, checked_segment

_LOCK = threading.RLock()
_ID = re.compile(r"[a-f0-9]{16}")
DEFAULT_ROOT = Path(DOWNLOAD_PATH).parent / "media_library"
MAX_SOURCE_BYTES = 512 * 1024**2
MAX_LIBRARY_BYTES = 5 * 1024**3
MIN_FREE_BYTES = 2 * 1024**3
RETENTION_SECONDS = 7 * 86400
MAX_SOURCES = 16


class MediaError(ValueError):
    """A user-facing media-workflow failure."""


class MediaStore:
    def __init__(self, root: Path | str = DEFAULT_ROOT):
        self.root = Path(root).absolute()

    def chat_dir(self, owner: int) -> Path:
        if isinstance(owner, bool) or not isinstance(owner, int):
            raise MediaError("Nieprawidłowa sesja.")
        path = self.root / str(owner)
        if self.root.is_symlink() or path.is_symlink():
            raise MediaError("Nieprawidłowy katalog materiałów.")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path

    def directory(self, owner: int, identity: str) -> Path:
        if not isinstance(identity, str) or not _ID.fullmatch(identity):
            raise MediaError("Nie znaleziono materiału.")
        path = self.chat_dir(owner) / identity
        if path.is_symlink():
            raise MediaError("Nie znaleziono materiału.")
        return path

    def ensure_space(self, extra: int = 0) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if shutil.disk_usage(self.root).free < MIN_FREE_BYTES + extra:
            raise MediaError("Za mało miejsca na serwerze. Usuń starsze materiały.")
        size = 0
        for path in self.root.rglob("*"):
            try:
                if path.is_file() and not path.is_symlink():
                    size += path.stat().st_size
            except FileNotFoundError:
                continue
        if size + extra > MAX_LIBRARY_BYTES:
            raise MediaError("Biblioteka zajmuje już 5 GB. Usuń starsze materiały.")

    def reserve(self, owner: int) -> tuple[str, Path]:
        with _LOCK:
            self.purge(owner)
            if len(self.list(owner)) >= MAX_SOURCES:
                raise MediaError("Biblioteka mieści 16 materiałów. Usuń jeden, aby dodać następny.")
            self.ensure_space()
            identity = secrets.token_hex(8)
            path = self.directory(owner, identity)
            path.mkdir(mode=0o700)
            return identity, path

    @staticmethod
    def atomic_json(path: Path, value: dict) -> None:
        temporary = path.with_name(path.name + ".tmp")
        with open(temporary, "w", encoding="utf-8") as file_obj:
            os.chmod(temporary, 0o600)
            json.dump(value, file_obj, ensure_ascii=False, allow_nan=False)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(temporary, path)

    def commit(
        self,
        owner: int,
        identity: str,
        *,
        title: str,
        url: str,
        kind: str,
        duration: float,
        segments: list[Segment],
        media_path: str | Path | None = None,
        **extra,
    ) -> dict:
        with _LOCK:
            self.ensure_space()
            directory = self.directory(owner, identity)
            directory.mkdir(exist_ok=True, mode=0o700)
            if kind not in {"audio", "video", "gallery"} or not 0 <= duration <= 7200:
                raise MediaError("Materiał przekracza limit dwóch godzin.")
            if len(segments) > 20000:
                raise MediaError("Za dużo fragmentów materiału.")
            validated = [checked_segment(asdict(item)) for item in segments]
            if any(item.end > duration + 1 for item in validated):
                raise MediaError("Znaczniki czasu wykraczają poza nagranie.")
            media_file = None
            if media_path:
                source = Path(media_path)
                if (
                    source.is_symlink()
                    or not source.is_file()
                    or source.stat().st_size > MAX_SOURCE_BYTES
                ):
                    raise MediaError("Plik jest niedostępny albo przekracza limit 512 MB.")
                extension = source.suffix.lower()
                if extension not in {
                    ".mp3",
                    ".m4a",
                    ".mp4",
                    ".mkv",
                    ".webm",
                    ".mov",
                    ".ogg",
                    ".avi",
                    ".wav",
                    ".flac",
                    ".opus",
                    ".aac",
                    ".m4v",
                }:
                    raise MediaError("Nieobsługiwany format pliku.")
                target = directory / ("source" + extension)
                if source.absolute() != target.absolute():
                    self.ensure_space(source.stat().st_size)
                    shutil.copyfile(source, target)
                os.chmod(target, 0o600)
                media_file = target.name
            result = {
                **extra,
                "version": 1,
                "id": identity,
                "owner": owner,
                "title": str(title)[:500],
                "url": str(url)[:4096],
                "kind": kind,
                "duration": duration,
                "created_at": time.time(),
                "media_file": media_file,
                "segments": [asdict(item) for item in validated],
            }
            # Reserved fields cannot be overridden through auxiliary metadata.
            result.update(id=identity, owner=owner, media_file=media_file)
            self.atomic_json(directory / "source.json", result)
            return result

    def get(self, owner: int, identity: str) -> dict:
        with _LOCK:
            directory = self.directory(owner, identity)
            path = directory / "source.json"
            try:
                if path.is_symlink() or path.stat().st_size > 16 * 1024**2:
                    raise ValueError("Invalid source manifest")
                data = json.loads(path.read_text(encoding="utf-8"))
                if (
                    data["owner"] != owner
                    or data["id"] != identity
                    or time.time() - data["created_at"] > RETENTION_SECONDS
                ):
                    raise ValueError("Expired source")
                data["segments"] = [asdict(checked_segment(item)) for item in data["segments"]]
                return data
            except (OSError, ValueError, KeyError, TypeError):
                raise MediaError(
                    "Materiał wygasł lub został usunięty. Zapisz go ponownie."
                ) from None

    def media_path(self, owner: int, identity: str) -> Path:
        data = self.get(owner, identity)
        name = data.get("media_file")
        if not isinstance(name, str) or Path(name).name != name:
            raise MediaError("Brak zachowanego nagrania. Zapisz materiał ponownie.")
        path = self.directory(owner, identity) / name
        if path.is_symlink() or not path.is_file():
            raise MediaError("Plik nagrania wygasł. Zapisz materiał ponownie.")
        return path

    def list(self, owner: int) -> list[dict]:
        with _LOCK:
            result = []
            for directory in self.chat_dir(owner).iterdir():
                if (
                    _ID.fullmatch(directory.name)
                    and directory.is_dir()
                    and not directory.is_symlink()
                ):
                    try:
                        result.append(self.get(owner, directory.name))
                    except MediaError:
                        pass
            return sorted(result, key=lambda item: item["created_at"], reverse=True)

    def delete(self, owner: int, identity: str) -> None:
        with _LOCK:
            shutil.rmtree(self.directory(owner, identity), ignore_errors=True)

    def purge(self, owner: int) -> None:
        with _LOCK:
            for directory in self.chat_dir(owner).iterdir():
                if (
                    directory.name.startswith(("render_", "frames_", "radar_"))
                    and directory.is_dir()
                    and not directory.is_symlink()
                    and time.time() - directory.stat().st_mtime > 86400
                ):
                    shutil.rmtree(directory, ignore_errors=True)
                    continue
                if (
                    _ID.fullmatch(directory.name)
                    and directory.is_dir()
                    and not directory.is_symlink()
                ):
                    try:
                        stamp = json.loads((directory / "source.json").read_text())["created_at"]
                    except (OSError, ValueError, KeyError, TypeError):
                        stamp = directory.stat().st_mtime
                    try:
                        expired = time.time() - float(stamp) > RETENTION_SECONDS
                    except (ValueError, TypeError, OverflowError):
                        expired = time.time() - directory.stat().st_mtime > RETENTION_SECONDS
                    if expired:
                        shutil.rmtree(directory, ignore_errors=True)


media_store = MediaStore()
