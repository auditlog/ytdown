"""Tests for stable orchestration in transcription_pipeline."""

import json
from pathlib import Path

import pytest

from bot import transcription_pipeline as pipeline
from bot.transcription_providers import SpeakerTurn, TranscribedPart


def _part_result(text, turns=()):
    return TranscribedPart(text=text, turns=tuple(turns)) if text is not None else None


def _two_parts(tmp_path):
    part1 = tmp_path / "audio_part1.mp3"
    part2 = tmp_path / "audio_part2.mp3"
    part1.write_bytes(b"a")
    part2.write_bytes(b"b")
    return part1, part2


def _source(tmp_path, name="audio.mp3"):
    source = tmp_path / name
    source.write_bytes(b"x" * 100)
    return source


def _run(tmp_path, transcribe, parts, **overrides):
    kwargs = dict(
        get_api_key_fn=lambda: "or-key",
        split_mp3_fn=lambda *_args, **_kwargs: [str(part) for part in parts],
        get_part_number_fn=lambda filename: 1 if "part1" in filename else 2,
        transcribe_audio_fn=transcribe,
        rmtree_fn=lambda _path: None,
    )
    kwargs.update(overrides)
    return pipeline.transcribe_mp3_file(str(_source(tmp_path)), str(tmp_path), **kwargs)


def test_transcribe_mp3_file_pipeline_uses_injected_dependencies(tmp_path):
    part1, part2 = _two_parts(tmp_path)
    removed = []
    seen = []

    def transcribe(path, key, language=None):
        seen.append((Path(path).stem, key, language))
        return _part_result(f"{Path(path).stem} text")

    result = pipeline.transcribe_mp3_file(
        str(_source(tmp_path)),
        str(tmp_path),
        progress_callback=lambda _text: None,
        language="pl",
        get_api_key_fn=lambda: "or-key",
        split_mp3_fn=lambda *_args, **_kwargs: [str(part1), str(part2)],
        get_part_number_fn=lambda filename: 1 if "part1" in filename else 2,
        transcribe_audio_fn=transcribe,
        rmtree_fn=lambda path: removed.append(path),
    )

    assert result == str(tmp_path / "audio_transcript.md")
    content = Path(result).read_text(encoding="utf-8")
    assert "audio_part1 text\n\naudio_part2 text" in content
    assert seen == [("audio_part1", "or-key", "pl"), ("audio_part2", "or-key", "pl")]
    assert removed == [str(tmp_path / "temp_parts")]


def test_pipeline_saves_speaker_turns_of_every_part(tmp_path):
    part1, part2 = _two_parts(tmp_path)
    turns = {
        str(part1): [SpeakerTurn(0, 0.0, 1.0, "Pytanie?"), SpeakerTurn(1, 1.0, 2.0, "Odpowiedź.")],
        str(part2): [SpeakerTurn(0, 0.0, 1.0, "Dalej.")],
    }

    result = _run(
        tmp_path,
        lambda path, _key, language=None: _part_result("tekst", turns[path]),
        [part1, part2],
    )

    data = json.loads((tmp_path / "audio_speakers.json").read_text(encoding="utf-8"))
    assert result == str(tmp_path / "audio_transcript.md")
    assert [part["part"] for part in data["parts"]] == [1, 2]
    assert data["parts"][0]["turns"][1] == {"speaker": 1, "start": 1.0, "end": 2.0, "text": "Odpowiedź."}


def test_pipeline_writes_no_speaker_file_without_speaker_numbers(tmp_path):
    part1, _part2 = _two_parts(tmp_path)

    _run(tmp_path, lambda _path, _key, language=None: _part_result("tekst"), [part1])

    assert not (tmp_path / "audio_speakers.json").exists()


def test_pipeline_returns_none_when_api_key_missing(tmp_path):
    result = pipeline.transcribe_mp3_file(
        str(_source(tmp_path)),
        str(tmp_path),
        get_api_key_fn=lambda: "",
    )

    assert result is None


def test_pipeline_marks_a_failed_part_among_good_ones(tmp_path):
    part1, part2 = _two_parts(tmp_path)
    texts = {str(part1): "first part text", str(part2): None}
    progress = []

    result = _run(
        tmp_path,
        lambda path, _key, language=None: _part_result(texts[path]),
        [part1, part2],
        progress_callback=progress.append,
    )

    content = Path(result).read_text(encoding="utf-8")
    assert "first part text" in content
    assert content.count(pipeline.EMPTY_PART_MARKER) == 1
    # The user is told how many parts are missing, not just shown a marker.
    assert any("Części bez transkrypcji: 1 z 2" in status for status in progress)


def test_split_mp3_failure_propagates_exception(tmp_path):
    """ffmpeg failure during splitting should propagate as-is, not be swallowed."""

    def failing_split(*_args, **_kwargs):
        raise RuntimeError("ffmpeg exited with code 1")

    with pytest.raises(RuntimeError, match="ffmpeg exited with code 1"):
        _run(tmp_path, lambda *_a, **_k: _part_result("text"), [], split_mp3_fn=failing_split)


def test_pipeline_fails_when_every_part_fails(tmp_path):
    """A run where every part came back empty is a failure, not a transcript.

    Delivering a file made only of placeholders would be reported to the user as
    success and could then be sent to paid summaries.
    """

    part1, part2 = _two_parts(tmp_path)

    result = _run(tmp_path, lambda _path, _key, language=None: None, [part1, part2])

    assert result is None
    assert not (tmp_path / "audio_transcript.md").exists()


def test_pipeline_fails_when_no_audio_parts_are_produced(tmp_path):
    # e.g. ffmpeg could not cut the source file into parts
    removed = []

    result = pipeline.transcribe_mp3_file(
        str(_source(tmp_path, "audio.m4a")),
        str(tmp_path),
        get_api_key_fn=lambda: "or-key",
        split_mp3_fn=lambda *_args, **_kwargs: [],
        transcribe_audio_fn=lambda *_args, **_kwargs: _part_result("unused"),
        rmtree_fn=removed.append,
    )

    assert result is None
    assert not (tmp_path / "audio_transcript.md").exists()
    assert removed == [str(tmp_path / "temp_parts")]


def test_transcribe_mp3_file_breaks_on_cancel(tmp_path):
    """When cancellation event is set before chunks start, no API calls are made."""

    import asyncio
    from bot.jobs import JobCancellation

    cancellation = JobCancellation(job_id="t", event=asyncio.Event())
    cancellation.event.set()
    part1, _part2 = _two_parts(tmp_path)
    api_called = {"n": 0}

    def fake_transcribe(_path, _key, language=None):
        api_called["n"] += 1
        return _part_result("should not be called")

    result = _run(tmp_path, fake_transcribe, [part1], cancellation=cancellation)

    # API must NOT be called for any chunk when cancellation is already set.
    assert api_called["n"] == 0
    assert result is None


def test_empty_part_file_marker_is_polish(tmp_path):
    part1, _part2 = _two_parts(tmp_path)

    _run(tmp_path, lambda _path, _key, language=None: None, [part1])

    part_text = (tmp_path / "audio_part1_transcript.txt").read_text(encoding="utf-8")
    assert part_text == "[brak transkrypcji tego fragmentu]"


def test_speaker_data_keeps_failed_and_unlabelled_parts_in_place(tmp_path):
    # A labelled transcript must not silently skip audio that the plain one marks as missing.
    part1, part2 = _two_parts(tmp_path)
    part3 = tmp_path / "audio_part3.mp3"
    part3.write_bytes(b"c")
    results = {
        str(part1): _part_result("Pytanie? Odpowiedź.", [SpeakerTurn(0, 0.0, 1.0, "Pytanie?"), SpeakerTurn(1, 1.0, 2.0, "Odpowiedź.")]),
        str(part2): None,
        str(part3): _part_result("Tekst bez mówców."),
    }

    _run(
        tmp_path,
        lambda path, _key, language=None: results[path],
        [part1, part2, part3],
        get_part_number_fn=lambda filename: int(filename.split("part")[1].split(".")[0]),
    )

    data = json.loads((tmp_path / "audio_speakers.json").read_text(encoding="utf-8"))
    assert [part["part"] for part in data["parts"]] == [1, 2, 3]
    assert data["parts"][1]["turns"] == [{"speaker": None, "start": 0.0, "end": 0.0, "text": pipeline.EMPTY_PART_MARKER}]
    assert data["parts"][2]["turns"] == [{"speaker": None, "start": 0.0, "end": 0.0, "text": "Tekst bez mówców."}]
