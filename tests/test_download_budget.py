"""Enforce actual byte counts and merge space when source estimates are missing."""

from types import SimpleNamespace

import pytest

from bot.download_budget import DownloadBudget, DownloadLimitError


def test_budget_counts_separate_video_and_audio_streams(tmp_path):
    budget = DownloadBudget(str(tmp_path), 100, 0)
    budget.progress({"filename": "video", "downloaded_bytes": 80})
    budget.progress({"filename": "video", "downloaded_bytes": 80})
    with pytest.raises(DownloadLimitError, match="limit"):
        budget.progress({"filename": "audio", "downloaded_bytes": 21})


def test_budget_reserves_space_for_merge_and_checks_final_size(tmp_path, monkeypatch):
    budget = DownloadBudget(str(tmp_path), 100, 20)
    monkeypatch.setattr("bot.download_budget.shutil.disk_usage", lambda _: SimpleNamespace(free=90))
    budget.progress({"filename": "video", "downloaded_bytes": 80})
    with pytest.raises(DownloadLimitError, match="miejsca"):
        budget.postprocess({"status": "started"})
    path = tmp_path / "merged.mp4"
    path.write_bytes(b"x" * 101)
    with pytest.raises(DownloadLimitError, match="Gotowy plik"):
        budget.check_result(str(path))


def test_budget_rejects_low_disk_before_downloading(tmp_path, monkeypatch):
    monkeypatch.setattr("bot.download_budget.shutil.disk_usage", lambda _: SimpleNamespace(free=10))
    with pytest.raises(DownloadLimitError, match="miejsca"):
        DownloadBudget(str(tmp_path), 100, 20).check_space()


def test_async_download_enforces_budget_and_keeps_existing_hooks(tmp_path, monkeypatch):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from unittest.mock import AsyncMock

    from bot.services import download_service as ds

    monkeypatch.setattr(ds, "get_video_info", lambda _: {"title": "Test", "duration": 5})
    plan = ds.prepare_download_plan(url="https://example.invalid/", media_type="video",
                                    format_choice="best", chat_download_path=str(tmp_path))
    observed = []
    plan.ydl_opts["progress_hooks"] = [lambda event: observed.append(event["downloaded_bytes"])]

    class FakeYoutubeDL:
        def __init__(self, opts):
            self.opts = opts

        def download(self, urls):
            assert self.opts["max_filesize"] == 20
            for hook in self.opts["progress_hooks"]:
                hook({"filename": "video", "downloaded_bytes": 21})

    monkeypatch.setattr(ds.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    state = {}
    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(DownloadLimitError):
            asyncio.run(ds.execute_download(
                plan, chat_id=1, executor=executor, progress_hook_factory=lambda _: lambda event: None,
                progress_state=state, status_callback=AsyncMock(), format_bytes=str, format_eta=str,
                max_file_bytes=20, min_free_bytes=0,
            ))
    assert observed == [21]
    assert state == {}
