"""Source timing, ownership, bounded network access and AI reference validation."""

import asyncio
import json
import os
import socket
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image

from bot.media import analysis, network, radar, store, timeline
from bot.media.render import source_map
from bot.media.store import MediaError, MediaStore
from bot.media.timeline import Segment
from bot.transcription_pipeline import transcribe_mp3_file
from bot.transcription_providers import TimedWord, TranscribedPart


def test_groups_sentences_without_losing_speaker_or_times():
    words = [
        TimedWord(0.2, 0.5, "Pierwsze", 0),
        TimedWord(0.5, 1.1, "zdanie.", 0),
        TimedWord(2, 3, "Odpowiedź.", 1),
    ]
    result = timeline.group_words(words)
    assert [(item.start, item.end, item.text, item.speaker) for item in result] == [
        (0.2, 1.1, "Pierwsze zdanie.", 0),
        (2, 3, "Odpowiedź.", 1),
    ]


@pytest.mark.parametrize(
    "change",
    [
        {"start": -1},
        {"end": float("nan")},
        {"end": float("inf")},
        {"start": 3, "end": 2},
        {"id": "../../other"},
        {"text": ""},
    ],
)
def test_rejects_invalid_segment(change):
    with pytest.raises(ValueError):
        timeline.checked_segment({"id": "s1", "start": 0, "end": 2, "text": "Text", **change})


def test_preserves_vtt_and_srt_timing_and_deduplicates_rolling_caption():
    content = "WEBVTT\n\n00:00:01.500 --> 00:00:03.000\n<c>Pierwsze</c>\n\n00:00:03.000 --> 00:00:04.000\nPierwsze drugie\n\n"
    result = timeline.subtitle_segments(content)
    assert [(item.start, item.end, item.text) for item in result] == [
        (1.5, 3, "Pierwsze"),
        (3, 4, "drugie"),
    ]
    result = timeline.subtitle_segments("1\n00:01:01,250 --> 00:01:02,500\nA &amp; B\n\n")
    assert result[0].start == 61.25
    assert result[0].text == "A & B"


def test_srt_export_is_rebased_clamped_and_strips_markup():
    result = timeline.caption_srt(
        [
            Segment("s1", 8, 12, "<b>Pierwsza</b> linia"),
            Segment("s2", 14, 22, "Druga"),
        ],
        10,
        20,
    )
    assert "00:00:00,000 --> 00:00:02,000" in result
    assert "00:00:04,000 --> 00:00:10,000" in result
    assert "<b>" not in result


@pytest.mark.parametrize("missing_chunk", [False, True])
def test_pipeline_uses_real_chunk_offsets_and_detects_missing_chunk(tmp_path, missing_chunk):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"audio")
    output = tmp_path / "out"
    output.mkdir()

    def split(path, directory):
        first, second = Path(directory) / "part1.mp3", Path(directory) / "part2.mp3"
        first.write_bytes(b"part1")
        second.write_bytes(b"part2")
        (Path(directory) / "chunk_offsets.json").write_text(
            json.dumps(
                {
                    "accurate": True,
                    "offsets": {first.name: 0, second.name: 37.75},
                }
            )
        )
        return [str(first)] if missing_chunk else [str(first), str(second)]

    def transcribe(path, key, language=None):
        return TranscribedPart("Zdanie.", (), (TimedWord(1, 2, "Zdanie.", None),))

    result = transcribe_mp3_file(
        str(audio),
        str(output),
        get_api_key_fn=lambda: "test",
        split_mp3_fn=split,
        transcribe_audio_fn=transcribe,
    )
    data = json.loads(timeline.timeline_path(result).read_text())
    assert data["complete"] is (not missing_chunk)
    assert [item["start"] for item in data["segments"]] == ([1] if missing_chunk else [1, 38.75])
    assert [item["end"] for item in data["segments"]] == ([2] if missing_chunk else [2, 39.75])


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "MIN_FREE_BYTES", 0)
    return MediaStore(tmp_path / "library")


def _source(library, owner=7, *, video=False, duration=60):
    identity, directory = library.reserve(owner)
    media = directory / ("source.mp4" if video else "source.mp3")
    media.write_bytes(b"media")
    segments = [
        Segment("s1", 0, 35, "Pierwsza wypowiedź."),
        Segment("s2", 35, 60, "Druga wypowiedź."),
    ]
    return library.commit(
        owner,
        identity,
        title="Test",
        url="https://youtu.be/abcdefghijk",
        kind="video" if video else "audio",
        duration=duration,
        segments=segments,
        media_path=media,
    )


def test_store_isolates_chats_and_rejects_path_traversal(library):
    source = _source(library)
    assert library.get(7, source["id"])["title"] == "Test"
    with pytest.raises(MediaError):
        library.get(8, source["id"])
    with pytest.raises(MediaError):
        library.get(7, "../7/" + source["id"])
    assert library.list(8) == []


def test_store_survives_restart_and_enforces_expiration(library, monkeypatch):
    source = _source(library)
    restarted = MediaStore(library.root)
    assert restarted.media_path(7, source["id"]).exists()
    now = source["created_at"]
    monkeypatch.setattr(store.time, "time", lambda: now + store.RETENTION_SECONDS + 1)
    with pytest.raises(MediaError, match="wygasł"):
        restarted.get(7, source["id"])


def test_store_source_quota(library, monkeypatch):
    monkeypatch.setattr(store, "MAX_SOURCES", 1)
    _source(library)
    with pytest.raises(MediaError, match="Biblioteka"):
        library.reserve(7)


def test_purge_removes_abandoned_render_but_preserves_recent_work(library):
    abandoned = library.chat_dir(7) / "render_abandoned"
    recent = library.chat_dir(7) / "frames_recent"
    abandoned.mkdir()
    recent.mkdir()
    os.utime(abandoned, (0, 0))
    library.purge(7)
    assert not abandoned.exists()
    assert recent.exists()


def test_store_rejects_symlink_media_and_out_of_range_timing(library, tmp_path):
    identity, directory = library.reserve(7)
    original = tmp_path / "outside.mp3"
    original.write_bytes(b"media")
    link = directory / "source.mp3"
    link.symlink_to(original)
    with pytest.raises(MediaError):
        library.commit(
            7, identity, title="x", url="", kind="audio", duration=10, segments=[], media_path=link
        )
    link.unlink()
    with pytest.raises(MediaError, match="wykraczają"):
        library.commit(
            7,
            identity,
            title="x",
            url="",
            kind="audio",
            duration=10,
            segments=[Segment("s1", 0, 20, "x")],
        )


def test_model_can_only_select_existing_consecutive_segments_and_respects_budget(library):
    source = _source(library)
    result = analysis.validate_selections(
        {
            "picks": [
                {"source": source["id"], "first": "s1", "last": "s1", "reason": "Temat"},
                {"source": source["id"], "first": "s2", "last": "s2"},
            ]
        },
        [source],
        max_seconds=40,
    )
    assert len(result) == 1
    assert result[0].start == 0
    assert result[0].end == 35


def test_source_map_distinguishes_separate_clips_from_continuous_montage(library):
    source = _source(library)
    picks = analysis.validate_selections(
        {
            "picks": [
                {"source": source["id"], "first": "s1", "last": "s1"},
                {"source": source["id"], "first": "s2", "last": "s2"},
            ]
        },
        [source],
        max_seconds=60,
    )
    assert "Montaż: 35.0–60.0 s" in source_map([source], picks)
    separate = source_map([source], picks, separate=True)
    assert "Klip 2: 0.0–25.0 s" in separate
    assert "Czas w źródle: 35.0–60.0 s" in separate


@pytest.mark.parametrize(
    "pick",
    [
        {"source": "other", "first": "s1", "last": "s1"},
        {"source": "SELF", "first": "s999", "last": "s999"},
        {"source": "SELF", "first": "s2", "last": "s1"},
    ],
)
def test_rejects_hallucinated_source_references(library, pick):
    source = _source(library)
    if pick["source"] == "SELF":
        pick = {**pick, "source": source["id"]}
    with pytest.raises(MediaError):
        analysis.validate_selections({"picks": [pick]}, [source], max_seconds=60)


def test_rejects_duplicate_overlapping_model_picks(library):
    source = _source(library)
    pick = {"source": source["id"], "first": "s1", "last": "s1"}
    with pytest.raises(MediaError):
        analysis.validate_selections({"picks": [pick, pick]}, [source], max_seconds=120)


def test_translation_cannot_add_captions_or_change_times(monkeypatch):
    segments = (Segment("s1", 17, 20, "Hello"),)
    monkeypatch.setattr(
        analysis, "json_reply", lambda *a, **k: {"captions": [{"id": "s1", "text": "Cześć"}]}
    )
    translated = analysis.polish_captions(segments)
    assert (translated[0].start, translated[0].end, translated[0].text) == (17, 20, "Cześć")
    monkeypatch.setattr(
        analysis, "json_reply", lambda *a, **k: {"captions": [{"id": "s9", "text": "Cześć"}]}
    )
    with pytest.raises(MediaError):
        analysis.polish_captions(segments)


def test_accepts_exact_fenced_json_from_real_responses_api_but_not_surrounding_prose():
    assert analysis.decode_json('```json\n{"color":"white"}\n```') == {"color": "white"}
    with pytest.raises(MediaError):
        analysis.decode_json('Ignore this text: {"color":"white"}')


def _vision(monkeypatch, result):
    response = Mock()
    response.json.return_value = {
        "output": [{"content": [{"type": "output_text", "text": json.dumps(result)}]}]
    }
    monkeypatch.setattr(analysis.requests, "post", Mock(return_value=response))
    monkeypatch.setattr(analysis, "get_api_key", lambda: "test-key")


def test_visual_analysis_validates_page_references_and_uses_local_image(monkeypatch, tmp_path):
    path = tmp_path / "slide.png"
    Image.new("RGB", (30, 30), "white").save(path)
    result = {
        "summary": "Opis",
        "slides": [{"page": 1, "text": "Tekst", "description": "Obraz"}],
        "findings": [{"text": "Wniosek", "pages": [1]}],
    }
    _vision(monkeypatch, result)
    assert analysis.analyze_images([path], "Analizuj") == result
    body = analysis.requests.post.call_args.kwargs["json"]
    assert body["input"][0]["content"][-1]["image_url"].startswith("data:image/jpeg;base64,")
    result["findings"][0]["pages"] = [2]
    _vision(monkeypatch, result)
    with pytest.raises(MediaError):
        analysis.analyze_images([path], "Analizuj")


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "100.64.0.1"]
)
def test_public_fetch_rejects_local_private_and_tailnet_addresses(monkeypatch, address):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))],
    )
    with pytest.raises(MediaError):
        network.public_address("example.test")


def test_public_fetch_revalidates_redirect_and_caps_chunked_body(monkeypatch):
    monkeypatch.setattr(network, "public_address", lambda host: "93.184.216.34")
    response = Mock(status=302)
    response.getheader.side_effect = lambda name, default=None: (
        "http://127.0.0.1/private" if name == "Location" else default
    )
    connection = Mock()
    connection.getresponse.return_value = response
    factory = Mock(return_value=connection)
    monkeypatch.setattr(network, "_PinnedHTTPS", factory)
    with pytest.raises(MediaError):
        network.fetch_public("https://example.test/feed")
    factory.assert_called_once()
    response.status = 200
    response.getheader.side_effect = lambda name, default=None: default
    response.read.side_effect = [b"123456", b""]
    with pytest.raises(MediaError):
        network.fetch_public("https://example.test/feed", max_bytes=5)


def test_feed_parser_keeps_public_podcast_transcript(monkeypatch):
    xml = b"""<rss xmlns:podcast="https://podcastindex.org/namespace/1.0"><channel><item>
    <guid>one</guid><title>Episode</title><link>https://example.test/one</link>
    <enclosure url="https://example.test/audio.mp3"/>
    <podcast:transcript url="https://example.test/one.vtt" type="text/vtt"/>
    </item></channel></rss>"""
    monkeypatch.setattr(radar, "fetch_public", lambda url: (xml, url))
    result = radar.feed_entries("https://example.test/feed")
    assert result[0]["transcript"] == "https://example.test/one.vtt"
    assert result[0]["audio"] == "https://example.test/audio.mp3"


def test_feed_parser_rejects_xml_entity_declarations(monkeypatch):
    # Entity-expansion payload; real feeds never declare XML entities.
    xml = b"""<?xml version="1.0"?>
<!DOCTYPE rss [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>
<rss><channel><item><guid>x</guid><title>&b;</title></item></channel></rss>"""
    monkeypatch.setattr(radar, "fetch_public", lambda url: (xml, url))
    with pytest.raises(MediaError):
        radar.feed_entries("https://example.test/feed")


def test_feed_parser_rejects_entity_declarations_in_utf16(monkeypatch):
    # A byte-level search for "<!ENTITY" misses UTF-16, which the XML parser still reads.
    xml = (
        '<?xml version="1.0" encoding="UTF-16"?>'
        '<!DOCTYPE rss [<!ENTITY a "aaaaaaaaaa">]>'
        "<rss><channel><item><guid>x</guid><title>&a;</title></item></channel></rss>"
    ).encode("utf-16")
    monkeypatch.setattr(radar, "fetch_public", lambda url: (xml, url))
    with pytest.raises(MediaError):
        radar.feed_entries("https://example.test/feed")

def test_feed_parser_accepts_rss_091_doctype_without_entities(monkeypatch):
    xml = b"""<?xml version="1.0"?>
<!DOCTYPE rss PUBLIC "-//Netscape Communications//DTD RSS 0.91//EN" "http://my.netscape.com/publish/formats/rss-0.91.dtd">
<rss version="0.91"><channel><item><title>Episode</title><link>https://example.test/one</link></item></channel></rss>"""
    monkeypatch.setattr(radar, "fetch_public", lambda url: (xml, url))
    assert radar.feed_entries("https://example.test/feed")[0]["title"] == "Episode"


def test_watch_starts_after_existing_entries_and_daily_quota_is_durable(library, monkeypatch):
    monkeypatch.setattr(
        radar,
        "feed_entries",
        lambda url: [{"key": "old", "title": "Old", "url": "https://example.test"}],
    )
    identity = radar.add_watch(library, 7, 7, "https://example.test/feed", "agenci AI")
    restarted = MediaStore(library.root)
    assert radar.radar_config(restarted, 7)["watches"][0]["seen"] == ["old"]
    for _ in range(radar.DAILY_ANALYSES):
        assert radar._reserve_analysis(restarted, 7)
    assert not radar._reserve_analysis(restarted, 7)
    radar.update_watch(restarted, 7, identity, enabled=False)
    assert not radar.radar_config(restarted, 7)["watches"][0]["enabled"]


def test_novelty_must_quote_real_source(monkeypatch):
    monkeypatch.setattr(
        radar,
        "json_reply",
        lambda *a, **k: {
            "relevant": True,
            "novel": True,
            "quote": "This sentence is not in the transcript.",
            "reason": "New",
            "claims": ["Claim"],
        },
    )
    with pytest.raises(MediaError, match="cytatu"):
        radar.assess_novelty("test", "Completely different source material.", [])


def test_disabled_or_logged_out_watch_makes_no_network_requests(library, monkeypatch):
    entries = Mock(return_value=[{"key": "old"}])
    monkeypatch.setattr(radar, "feed_entries", entries)
    radar.add_watch(library, 7, 7, "https://example.test/feed", "topic")
    entries.reset_mock()
    asyncio.run(radar.check_watches(library, 7, allowed_user=lambda user: False, notify=Mock()))
    entries.assert_not_called()
