"""Tests for bot.transcription_chunking public functions."""

from __future__ import annotations

import subprocess
import types
from unittest.mock import MagicMock

import pytest

from bot.transcription_chunking import find_silence_points, get_part_number, split_mp3


# ---------------------------------------------------------------------------
# get_part_number
# ---------------------------------------------------------------------------


def test_get_part_number_with_valid_suffix_returns_integer():
    assert get_part_number("audio_part3.mp3") == 3


def test_get_part_number_with_part1_returns_1():
    assert get_part_number("recording_part1.mp3") == 1


def test_get_part_number_with_large_number_returns_correct_value():
    assert get_part_number("track_part42.mp3") == 42


def test_get_part_number_with_no_part_suffix_returns_zero():
    assert get_part_number("audio.mp3") == 0


def test_get_part_number_with_empty_string_returns_zero():
    assert get_part_number("") == 0


def test_get_part_number_with_unrelated_digits_in_name_returns_zero():
    # digits not preceded by "part" should not match
    assert get_part_number("audio2024.mp3") == 0


def test_get_part_number_with_part_embedded_in_path_returns_number():
    assert get_part_number("/tmp/chunks/audio_part5.mp3") == 5


# ---------------------------------------------------------------------------
# find_silence_points
# ---------------------------------------------------------------------------

_SILENCE_OUTPUT = (
    "    [silencedetect @ 0x...] silence_start: 10.5\n"
    "    [silencedetect @ 0x...] silence_end: 12.3 | silence_duration: 1.8\n"
    "    [silencedetect @ 0x...] silence_start: 30.0\n"
    "    [silencedetect @ 0x...] silence_end: 31.5 | silence_duration: 1.5\n"
)


def _make_subprocess_mock(stderr_text: str):
    """Return a mock subprocess module whose .run() yields the given stderr."""
    mock_result = MagicMock()
    mock_result.stderr = stderr_text
    mock_module = MagicMock()
    mock_module.run.return_value = mock_result
    # PIPE sentinel must be the real one so it can be passed through
    mock_module.PIPE = subprocess.PIPE
    return mock_module


def test_find_silence_points_parses_silence_end_timestamps():
    mock_subprocess = _make_subprocess_mock(_SILENCE_OUTPUT)

    points = find_silence_points("fake.mp3", num_parts=3, subprocess_module=mock_subprocess)

    assert points == [12.3, 31.5]


def test_find_silence_points_returns_sorted_list():
    # Reverse the order in the output to verify sorting
    reversed_output = (
        "    [silencedetect] silence_end: 50.0 | silence_duration: 1.0\n"
        "    [silencedetect] silence_end: 20.0 | silence_duration: 1.0\n"
        "    [silencedetect] silence_end: 35.0 | silence_duration: 1.0\n"
    )
    mock_subprocess = _make_subprocess_mock(reversed_output)

    points = find_silence_points("fake.mp3", num_parts=4, subprocess_module=mock_subprocess)

    assert points == [20.0, 35.0, 50.0]


def test_find_silence_points_with_no_silence_returns_empty_list():
    mock_subprocess = _make_subprocess_mock("No silence detected in this file.\n")

    points = find_silence_points("fake.mp3", num_parts=2, subprocess_module=mock_subprocess)

    assert points == []


def test_find_silence_points_with_subprocess_error_returns_empty_list():
    mock_module = MagicMock()
    mock_module.run.side_effect = subprocess.SubprocessError("ffmpeg crashed")
    mock_module.PIPE = subprocess.PIPE
    mock_module.SubprocessError = subprocess.SubprocessError

    points = find_silence_points("fake.mp3", num_parts=2, subprocess_module=mock_module)

    assert points == []


def test_find_silence_points_calls_ffmpeg_with_silencedetect_filter():
    mock_subprocess = _make_subprocess_mock("")

    find_silence_points("audio.mp3", num_parts=2, subprocess_module=mock_subprocess)

    call_args = mock_subprocess.run.call_args
    cmd = call_args[0][0]
    assert cmd[0] == "ffmpeg"
    assert "silencedetect" in " ".join(cmd)
    assert "audio.mp3" in cmd


# ---------------------------------------------------------------------------
# split_mp3
# ---------------------------------------------------------------------------


def _make_mp3_factory(duration_seconds: float):
    """Return a factory that mimics mutagen.mp3.MP3(path).info.length."""
    def factory(path):
        audio = MagicMock()
        audio.info.length = duration_seconds
        return audio
    return factory


def test_split_mp3_copies_file_when_already_small_enough(tmp_path):
    # Source file lives in a subdirectory; output_dir is tmp_path so that
    # shutil.copy writes to a different path and does not raise SameFileError.
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    src = src_dir / "small.mp3"
    src.write_bytes(b"x")

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    result = split_mp3(
        str(src),
        str(out_dir),
        max_size_mb=20,
        mp3_factory=_make_mp3_factory(60.0),
        subprocess_module=MagicMock(),
        find_silence_points_fn=lambda *_a, **_k: [],
    )

    assert len(result) == 1
    assert result[0].endswith("small.mp3")


def test_split_mp3_returns_list_with_existing_output_files(tmp_path):
    # Simulate a file larger than the limit so splitting is triggered.
    # We create a 25 MB-sized fake file.
    src = tmp_path / "big.mp3"
    # Write 25 MB of zeros
    src.write_bytes(b"\x00" * (25 * 1024 * 1024))

    # After split, ffmpeg would create the part files; we pre-create them
    # so the final filter (exists + size > 0) passes.
    part1 = tmp_path / "big_part1.mp3"
    part2 = tmp_path / "big_part2.mp3"
    part1.write_bytes(b"a")
    part2.write_bytes(b"b")

    mock_subprocess = MagicMock()
    mock_subprocess.PIPE = subprocess.PIPE

    result = split_mp3(
        str(src),
        str(tmp_path),
        max_size_mb=20,
        mp3_factory=_make_mp3_factory(200.0),
        subprocess_module=mock_subprocess,
        find_silence_points_fn=lambda *_a, **_k: [],
    )

    assert part1.as_posix() in result
    assert part2.as_posix() in result


def test_split_mp3_part_filenames_use_sequential_numbering(tmp_path):
    src = tmp_path / "episode.mp3"
    src.write_bytes(b"\x00" * (25 * 1024 * 1024))

    # Pre-create output parts so the filter sees them
    (tmp_path / "episode_part1.mp3").write_bytes(b"a")
    (tmp_path / "episode_part2.mp3").write_bytes(b"b")

    mock_subprocess = MagicMock()
    mock_subprocess.PIPE = subprocess.PIPE

    result = split_mp3(
        str(src),
        str(tmp_path),
        max_size_mb=20,
        mp3_factory=_make_mp3_factory(200.0),
        subprocess_module=mock_subprocess,
        find_silence_points_fn=lambda *_a, **_k: [],
    )

    basenames = [p.split("/")[-1] for p in result]
    assert "episode_part1.mp3" in basenames
    assert "episode_part2.mp3" in basenames


def test_split_mp3_invokes_ffmpeg_for_each_part(tmp_path):
    src = tmp_path / "long.mp3"
    src.write_bytes(b"\x00" * (25 * 1024 * 1024))

    (tmp_path / "long_part1.mp3").write_bytes(b"a")
    (tmp_path / "long_part2.mp3").write_bytes(b"b")

    mock_subprocess = MagicMock()
    mock_subprocess.PIPE = subprocess.PIPE

    split_mp3(
        str(src),
        str(tmp_path),
        max_size_mb=20,
        mp3_factory=_make_mp3_factory(200.0),
        subprocess_module=mock_subprocess,
        find_silence_points_fn=lambda *_a, **_k: [],
    )

    # ffmpeg should have been called once per part (2 parts for 25 MB / 20 MB)
    assert mock_subprocess.run.call_count == 2


def test_split_mp3_uses_silence_points_for_split_boundaries(tmp_path):
    src = tmp_path / "podcast.mp3"
    src.write_bytes(b"\x00" * (25 * 1024 * 1024))

    (tmp_path / "podcast_part1.mp3").write_bytes(b"a")
    (tmp_path / "podcast_part2.mp3").write_bytes(b"b")

    mock_subprocess = MagicMock()
    mock_subprocess.PIPE = subprocess.PIPE

    # Total duration = 200 s, ideal split at 100 s; provide silence at 98 s
    silence_called_with = []

    def fake_silence(path, num_parts, **_kwargs):
        silence_called_with.append((path, num_parts))
        return [98.0]

    split_mp3(
        str(src),
        str(tmp_path),
        max_size_mb=20,
        mp3_factory=_make_mp3_factory(200.0),
        subprocess_module=mock_subprocess,
        find_silence_points_fn=fake_silence,
    )

    # Ensure silence detection was called
    assert silence_called_with, "find_silence_points_fn was never called"

    # The split point passed to ffmpeg should be close to the silence point (98 s),
    # not the ideal (100 s); inspect the -ss argument of the first ffmpeg call.
    first_cmd = mock_subprocess.run.call_args_list[0][0][0]
    ss_index = first_cmd.index("-ss")
    start_of_part1 = float(first_cmd[ss_index + 1])
    assert start_of_part1 == 0.0  # part 1 always starts at 0

    second_cmd = mock_subprocess.run.call_args_list[1][0][0]
    ss_index2 = second_cmd.index("-ss")
    start_of_part2 = float(second_cmd[ss_index2 + 1])
    assert start_of_part2 == pytest.approx(98.0)


def test_split_mp3_excludes_empty_output_files_from_result(tmp_path):
    src = tmp_path / "audio.mp3"
    src.write_bytes(b"\x00" * (25 * 1024 * 1024))

    # part1 exists and has content; part2 exists but is empty (simulate ffmpeg failure)
    (tmp_path / "audio_part1.mp3").write_bytes(b"data")
    (tmp_path / "audio_part2.mp3").write_bytes(b"")  # empty => excluded

    mock_subprocess = MagicMock()
    mock_subprocess.PIPE = subprocess.PIPE

    result = split_mp3(
        str(src),
        str(tmp_path),
        max_size_mb=20,
        mp3_factory=_make_mp3_factory(200.0),
        subprocess_module=mock_subprocess,
        find_silence_points_fn=lambda *_a, **_k: [],
    )

    basenames = [p.split("/")[-1] for p in result]
    assert "audio_part1.mp3" in basenames
    assert "audio_part2.mp3" not in basenames


def test_split_mp3_falls_back_to_equal_parts_when_mutagen_fails(tmp_path):
    src = tmp_path / "broken.mp3"
    src.write_bytes(b"\x00" * (25 * 1024 * 1024))

    (tmp_path / "broken_part1.mp3").write_bytes(b"a")
    (tmp_path / "broken_part2.mp3").write_bytes(b"b")

    def failing_factory(path):
        raise OSError("mutagen read error")

    mock_subprocess = MagicMock()
    mock_subprocess.PIPE = subprocess.PIPE

    # Should not raise; falls back to size-based duration estimate
    result = split_mp3(
        str(src),
        str(tmp_path),
        max_size_mb=20,
        mp3_factory=failing_factory,
        subprocess_module=mock_subprocess,
        find_silence_points_fn=lambda *_a, **_k: [],
    )

    assert isinstance(result, list)
