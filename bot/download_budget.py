"""Byte and free-space checks for bounded media downloads."""

from __future__ import annotations

import shutil
from pathlib import Path


class DownloadLimitError(ValueError):
    """A user-safe download resource error."""


class DownloadBudget:
    def __init__(self, directory: str, max_file_bytes: int, min_free_bytes: int):
        self.directory = Path(directory)
        self.max_file_bytes = max_file_bytes
        self.min_free_bytes = min_free_bytes
        self.stream_bytes: dict[str, int] = {}
        if max_file_bytes <= 0 or min_free_bytes < 0:
            raise ValueError("Invalid download budget")

    def check_space(self, extra_bytes: int = 0):
        if shutil.disk_usage(self.directory).free < self.min_free_bytes + extra_bytes:
            raise DownloadLimitError("Za mało wolnego miejsca na pobranie lub połączenie pliku.")

    def progress(self, event: dict):
        key = str(event.get("filename", "media"))
        self.stream_bytes[key] = max(
            self.stream_bytes.get(key, 0), event.get("downloaded_bytes") or 0,
        )
        if sum(self.stream_bytes.values()) > self.max_file_bytes:
            raise DownloadLimitError("Pobierany plik przekroczył dozwolony limit rozmiaru.")
        self.check_space()

    def postprocess(self, event: dict):
        if event.get("status") == "started":
            # ffmpeg temporarily keeps source streams alongside the merged output.
            self.check_space(extra_bytes=sum(self.stream_bytes.values()))

    def check_result(self, path: str):
        if Path(path).stat().st_size > self.max_file_bytes:
            raise DownloadLimitError("Gotowy plik przekroczył dozwolony limit rozmiaru.")
        self.check_space()
