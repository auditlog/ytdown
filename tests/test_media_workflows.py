"""End-to-end orchestration without paid model calls or Telegram traffic."""

import asyncio
import threading
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image

from bot.handlers import media_studio as studio
from bot.jobs import busy_chats, job_registry
from bot.media import ingest, radar, store, visual
from bot.media.analysis import Selection
from bot.media.store import MediaError, MediaStore
from bot.media.timeline import Segment


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "MIN_FREE_BYTES", 0)
    return MediaStore(tmp_path / "library")


def _context(user=7, chat=7):
    tasks = []

    def schedule(coro, update=None):
        task = asyncio.create_task(coro)
        tasks.append(task)
        return task

    progress = SimpleNamespace(edit_text=AsyncMock(), edit_reply_markup=AsyncMock())
    bot = SimpleNamespace(send_message=AsyncMock(return_value=progress), send_document=AsyncMock())
    context = SimpleNamespace(
        bot=bot, user_data={}, application=SimpleNamespace(bot_data={}, create_task=schedule)
    )
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat),
        effective_user=SimpleNamespace(id=user),
        effective_message=SimpleNamespace(text="/studio"),
        callback_query=None,
    )
    return update, context, tasks


def test_import_prefers_captions_and_never_calls_paid_transcription(library, monkeypatch):
    monkeypatch.setattr(ingest, "_metadata", lambda url: {"title": "Film", "duration": 60})

    def download(url, info, directory, video, cancellation):
        path = directory / "source.mp3"
        path.write_bytes(b"audio")
        return path

    monkeypatch.setattr(ingest, "_download", download)
    monkeypatch.setattr(ingest, "probe_duration", AsyncMock(return_value=60))
    monkeypatch.setattr(ingest, "_captions", lambda *a: [Segment("s1", 1, 30, "Źródłowe napisy.")])
    paid = AsyncMock(side_effect=AssertionError("ASR should not be called"))
    monkeypatch.setattr(ingest, "run_transcription_with_progress", paid)
    result = asyncio.run(ingest.import_source(library, 7, url="https://youtu.be/abcdefghijk"))
    assert result["provenance"] == "captions"
    assert result["segments"][0]["start"] == 1
    assert library.media_path(7, result["id"]).is_file()
    paid.assert_not_called()


def test_failed_import_removes_all_staged_files(library, monkeypatch, tmp_path):
    source = tmp_path / "audio.mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(ingest, "probe_duration", AsyncMock(return_value=60))
    monkeypatch.setattr(ingest, "run_transcription_with_progress", AsyncMock(return_value=None))
    with pytest.raises(MediaError):
        asyncio.run(ingest.import_source(library, 7, local_path=source))
    assert list(library.chat_dir(7).iterdir()) == []


def test_gallery_uses_all_pages_in_order_and_cleans_downloads(library, monkeypatch):
    visited = []

    def download(url, *, destination, **kwargs):
        visited.append(url)
        Image.new("RGB", (30, 30), "white").save(destination, "JPEG")
        return b"", url

    def analyze(paths, query):
        assert [path.name for path in paths] == ["page_001.image", "page_002.image"]
        return {
            "summary": "Wynik",
            "slides": [{"page": 1, "text": "A"}, {"page": 2, "text": "B"}],
            "findings": [{"text": "Wniosek", "pages": [1, 2]}],
        }

    monkeypatch.setattr(visual, "fetch_public", download)
    monkeypatch.setattr(visual, "analyze_images", analyze)
    result, artifact = asyncio.run(
        visual.analyze_gallery(
            library,
            7,
            gallery={"photos": [{"url": "https://a.test/1"}, {"url": "https://a.test/2"}]},
            url="https://tiktok.com/@a/photo/1",
            query="Czytaj",
        )
    )
    assert visited == ["https://a.test/1", "https://a.test/2"]
    assert "strony: 1, 2" in artifact.read_text()
    assert not list(artifact.parent.glob("*.image"))
    assert library.get(7, result["id"])["kind"] == "gallery"


def test_gallery_failure_leaves_no_library_record(library, monkeypatch):
    monkeypatch.setattr(visual, "fetch_public", Mock(side_effect=MediaError("Network")))
    with pytest.raises(MediaError):
        asyncio.run(
            visual.analyze_gallery(
                library,
                7,
                gallery={"photos": [{"url": "https://a.test"}]},
                url="https://tiktok.com/@a/photo/1",
                query="Czytaj",
            )
        )
    assert list(library.chat_dir(7).iterdir()) == []


@pytest.mark.parametrize(
    "handler",
    [
        studio.studio_command,
        studio.materials_command,
        studio.save_command,
        studio.montage_command,
        studio.clips_command,
        studio.analyze_command,
        studio.radar_command,
    ],
)
def test_every_new_command_requires_authorization(monkeypatch, handler):
    update, context, tasks = _context()
    monkeypatch.setattr(studio, "_allowed", lambda *a: False)
    asyncio.run(handler(update, context))
    assert not tasks
    assert "PIN" in context.bot.send_message.await_args.kwargs["text"]


def test_commands_release_update_loop_but_hold_busy_guard_until_finished(monkeypatch):
    update, context, tasks = _context()
    monkeypatch.setattr(studio, "_allowed", lambda *a: True)
    monkeypatch.setattr(studio, "check_rate_limit", lambda user: True)
    called = []

    async def scenario():
        gate = asyncio.Event()

        async def operation(cancellation, status):
            called.append(cancellation.job_id)
            await gate.wait()

        await studio._launch(update, context, "Test", operation)
        assert 7 in busy_chats
        await studio._launch(update, context, "Second", operation)
        assert len(tasks) == 1
        await asyncio.sleep(0)
        assert len(job_registry.list_for_chat(7)) == 1
        gate.set()
        await asyncio.gather(*tasks)

    asyncio.run(scenario())
    assert len(called) == 1
    assert 7 not in busy_chats
    assert not job_registry.list_for_chat(7)


def test_failed_background_job_always_releases_guard(monkeypatch):
    update, context, tasks = _context()
    monkeypatch.setattr(studio, "_allowed", lambda *a: True)
    monkeypatch.setattr(studio, "check_rate_limit", lambda user: True)

    async def scenario():
        async def operation(cancellation, status):
            raise MediaError("Expected failure")

        await studio._launch(update, context, "Test", operation)
        await asyncio.gather(*tasks)

    asyncio.run(scenario())
    assert 7 not in busy_chats
    assert not job_registry.list_for_chat(7)
    assert any(
        call.kwargs.get("text") == "Expected failure"
        for call in context.bot.send_message.await_args_list
    )


def test_stale_source_button_cannot_import_new_url(monkeypatch):
    from bot.session_store import user_urls

    update, context, _ = _context()
    monkeypatch.setattr(studio, "_allowed", lambda *a: True)
    monkeypatch.setattr(studio, "import_source", AsyncMock())
    user_urls[7] = "https://youtu.be/newnewnew12"
    old = "https://youtu.be/oldoldold12"
    asyncio.run(
        studio.handle_media_callback(
            update, context, "media_import_audio_" + studio.source_tag(old)
        )
    )
    studio.import_source.assert_not_called()


def test_local_upload_must_belong_to_same_chat(monkeypatch, tmp_path):
    update, context, _ = _context()
    monkeypatch.setattr(studio, "DOWNLOAD_PATH", str(tmp_path))
    other = tmp_path / "8" / "file.mp3"
    other.parent.mkdir()
    other.write_bytes(b"audio")
    context.user_data["audio_file_path"] = str(other)
    with pytest.raises(MediaError, match="tego czatu"):
        studio._capture_input(update, context)


def _watch(library, monkeypatch):
    monkeypatch.setattr(radar, "feed_entries", lambda url: [{"key": "old"}])
    identity = radar.add_watch(library, 7, 7, "https://example.test/feed", "ocena agentów")
    entry = {
        "key": "new",
        "title": "Episode",
        "url": "https://example.test/episode",
        "transcript": "https://example.test/text.vtt",
    }
    monkeypatch.setattr(radar, "feed_entries", lambda url: [entry, {"key": "old"}])
    monkeypatch.setattr(
        radar,
        "fetch_public",
        lambda url: (
            b"WEBVTT\n\n00:00:00.000 --> 00:00:10.000\nNew experimental evidence about agent evaluation.\n\n",
            url,
        ),
    )
    assess = Mock(
        return_value={
            "relevant": True,
            "novel": True,
            "reason": "Nowy eksperyment",
            "quote": "New experimental evidence about agent evaluation.",
            "claims": [],
        }
    )
    monkeypatch.setattr(radar, "assess_novelty", assess)
    return identity, entry, assess


def test_radar_failed_delivery_is_retried_from_durable_outbox_without_new_ai(library, monkeypatch):
    identity, entry, assess = _watch(library, monkeypatch)
    notify = AsyncMock(side_effect=RuntimeError("Telegram down"))
    asyncio.run(radar.check_watches(library, 7, allowed_user=lambda user: True, notify=notify))
    config = radar.radar_config(library, 7)
    assert config["watches"][0]["pending"]["entry"]["key"] == "new"
    assert "new" not in config["watches"][0]["seen"]
    notify = AsyncMock()
    asyncio.run(
        radar.check_watches(
            MediaStore(library.root), 7, allowed_user=lambda user: True, notify=notify
        )
    )
    notify.assert_awaited_once()
    assess.assert_called_once()
    assert "new" in radar.radar_config(library, 7)["watches"][0]["seen"]


def test_radar_rechecks_pause_after_long_ai_call(library, monkeypatch):
    identity, entry, assess = _watch(library, monkeypatch)
    original = assess.return_value

    def pause(*args):
        radar.update_watch(library, 7, identity, enabled=False)
        return original

    monkeypatch.setattr(radar, "assess_novelty", pause)
    notify = AsyncMock()
    asyncio.run(radar.check_watches(library, 7, allowed_user=lambda user: True, notify=notify))
    notify.assert_not_called()


def test_radar_stop_during_analysis_prevents_delivery_and_seen_marker(library, monkeypatch):
    identity, entry, assess = _watch(library, monkeypatch)
    original = assess.return_value
    cancellation = SimpleNamespace(event=threading.Event())

    def stop(*args):
        cancellation.event.set()
        return original

    monkeypatch.setattr(radar, "assess_novelty", stop)
    notify = AsyncMock()
    asyncio.run(
        radar.check_watches(
            library, 7, allowed_user=lambda user: True, notify=notify, cancellation=cancellation
        )
    )
    notify.assert_not_called()
    config = radar.radar_config(library, 7)
    assert entry["key"] not in config["watches"][0]["seen"]
    assert config["watches"][0]["pending"] is None


def test_explicit_video_analysis_id_overrides_old_gallery(library, monkeypatch):
    identity, directory = library.reserve(7)
    path = directory / "source.mp4"
    path.write_bytes(b"video")
    library.commit(
        7,
        identity,
        title="Film",
        url="",
        kind="video",
        duration=60,
        segments=[Segment("s1", 0, 60, "Text")],
        media_path=path,
    )
    update, context, tasks = _context()
    update.effective_message.text = f"/analizuj {identity} Opisz slajdy"
    context.user_data["tiktok_gallery"] = {"photos": [{"url": "https://old.test/photo"}]}
    monkeypatch.setattr(studio, "media_store", library)
    monkeypatch.setattr(studio, "_allowed", lambda *args: True)
    video = AsyncMock(return_value=path)
    gallery = AsyncMock(side_effect=AssertionError("Stale gallery selected"))
    monkeypatch.setattr(studio, "analyze_video_frames", video)
    monkeypatch.setattr(studio, "analyze_gallery", gallery)
    monkeypatch.setattr(studio, "check_rate_limit", lambda user: True)

    async def scenario():
        await studio.analyze_command(update, context)
        await asyncio.gather(*tasks)

    asyncio.run(scenario())
    video.assert_awaited_once()
    gallery.assert_not_called()


@pytest.mark.parametrize("shorts", [False, True])
def test_edit_workflow_delivers_artifact_and_correct_source_map(library, monkeypatch, shorts):
    identity, directory = library.reserve(7)
    media = directory / "source.mp4"
    media.write_bytes(b"video")
    cue = Segment("s1", 5, 40, "Complete statement about AI agents.")
    library.commit(
        7,
        identity,
        title="Film",
        url="https://youtu.be/abcdefghijk",
        kind="video",
        duration=60,
        segments=[cue],
        media_path=media,
    )
    update, context, tasks = _context()
    update.effective_message.text = "/klipy agenci AI" if shorts else "/montaz 2 agenci AI"
    context.bot.send_audio = AsyncMock()
    context.bot.send_video = AsyncMock()
    delivered = []

    async def document(**kwargs):
        delivered.append(kwargs["document"].read().decode())

    async def render(*args, **kwargs):
        output = args[3]
        output.write_bytes(b"rendered")
        return output

    monkeypatch.setattr(studio, "media_store", library)
    monkeypatch.setattr(studio, "_allowed", lambda *args: True)
    monkeypatch.setattr(studio, "check_rate_limit", lambda *args: True)
    monkeypatch.setattr(
        studio,
        "select_fragments",
        lambda *args, **kwargs: [Selection(identity, 5, 40, "Complete relevant statement", (cue,))],
    )
    monkeypatch.setattr(studio, "polish_captions", lambda segments: list(segments))
    monkeypatch.setattr(studio, "assemble_audio", render)
    monkeypatch.setattr(studio, "render_short", render)
    history = Mock()
    monkeypatch.setattr(studio, "record_download_for", history)
    context.bot.send_document = AsyncMock(side_effect=document)

    async def scenario():
        await (
            studio.clips_command(update, context)
            if shorts
            else studio.montage_command(update, context)
        )
        await asyncio.gather(*tasks)

    asyncio.run(scenario())
    (context.bot.send_video if shorts else context.bot.send_audio).assert_awaited_once()
    assert len(delivered) == 1
    assert ("Klip 1: 0.0–35.0 s" if shorts else "Montaż: 0.0–35.0 s") in delivered[0]
    assert "Czas w źródle: 5.0–40.0 s" in delivered[0]
    assert "https://youtu.be/abcdefghijk" in delivered[0]
    history.assert_called_once()
    assert 7 not in busy_chats and job_registry.list_for_chat(7) == []


def test_radar_does_not_transcribe_audio_without_explicit_opt_in(library, monkeypatch):
    _watch(library, monkeypatch)
    entry = {
        "key": "new",
        "title": "Episode",
        "url": "https://example.test/episode",
        "audio": "https://example.test/audio.mp3",
    }
    monkeypatch.setattr(radar, "feed_entries", lambda url: [entry])
    paid = AsyncMock()
    monkeypatch.setattr(radar, "import_source", paid)
    asyncio.run(radar.check_watches(library, 7, allowed_user=lambda user: True, notify=AsyncMock()))
    paid.assert_not_called()
    assert "napisów" in radar.radar_config(library, 7)["watches"][0]["last_error"]


def test_radar_transcription_opt_in_is_limited_to_one_per_day(library, monkeypatch):
    identity, entry, assess = _watch(library, monkeypatch)
    radar.update_watch(library, 7, identity, transcribe=True)
    entries = [
        {
            "key": f"new{i}",
            "title": "Episode",
            "url": "https://example.test/episode",
            "audio": f"https://example.test/audio{i}.mp3",
        }
        for i in range(2)
    ]
    monkeypatch.setattr(radar, "feed_entries", lambda url: entries)
    paid = AsyncMock(
        return_value={"segments": [asdict(Segment("s1", 0, 10, "Source text for topic radar."))]}
    )
    monkeypatch.setattr(radar, "import_source", paid)
    asyncio.run(radar.check_watches(library, 7, allowed_user=lambda user: True, notify=AsyncMock()))
    paid.assert_awaited_once()
    assert paid.await_args.kwargs["max_duration"] == 3600
    assert len(radar.radar_config(library, 7)["transcriptions"]) == 1
