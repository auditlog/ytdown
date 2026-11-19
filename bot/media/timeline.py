"""Validated source timelines, subtitle cues and caption rendering."""

from __future__ import annotations

import html
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Segment:
    id: str
    start: float
    end: float
    text: str
    speaker: int | None = None


def checked_segment(value: dict) -> Segment:
    start, end = float(value["start"]), float(value["end"])
    text = str(value["text"]).strip()
    identity = str(value["id"])
    if (
        not math.isfinite(start)
        or not math.isfinite(end)
        or start < 0
        or end <= start
        or not text
        or len(text) > 4000
        or not re.fullmatch(r"s\d{1,7}", identity)
    ):
        raise ValueError("Invalid source segment")
    return Segment(identity, start, end, text, value.get("speaker"))


def group_words(words) -> list[Segment]:
    """Keep sentence-sized units without inventing times for untimed text."""
    result, pending = [], []
    for word in words:
        start, end = float(word.start), float(word.end)
        if not all(map(math.isfinite, (start, end))) or start < 0 or end <= start:
            continue
        if pending and (word.speaker != pending[-1].speaker or start - pending[-1].end > 2.0):
            _append(result, pending)
            pending = []
        pending.append(word)
        if re.search(r"[.!?][\"'»”)]*$", word.text) or end - pending[0].start >= 20:
            _append(result, pending)
            pending = []
    _append(result, pending)
    return result


def _append(result, pending):
    if pending:
        text = re.sub(r"\s+([,.;:!?])", r"\1", " ".join(word.text for word in pending))
        result.append(
            Segment(
                f"s{len(result) + 1:05d}",
                pending[0].start,
                pending[-1].end,
                text,
                pending[0].speaker,
            )
        )


_STAMP = r"(?:\d{1,3}:)?\d{2}:\d{2}[.,]\d{3}"
_CUE = re.compile(rf"(?m)^(?P<start>{_STAMP})\s*-->\s*(?P<end>{_STAMP})[^\n]*\n")


def seconds(stamp: str) -> float:
    result = 0.0
    for part in stamp.replace(",", ".").split(":"):
        result = result * 60 + float(part)
    return result


def subtitle_segments(content: str) -> list[Segment]:
    """Read VTT/SRT cue times and collapse rolling-caption duplicate prefixes."""
    cues = list(_CUE.finditer(content.replace("\r\n", "\n")))
    content = content.replace("\r\n", "\n")
    result = []
    previous = ""
    for index, cue in enumerate(cues):
        end_at = cues[index + 1].start() if index + 1 < len(cues) else len(content)
        body = content[cue.end() : end_at].split("\n\n", 1)[0]
        text = " ".join(html.unescape(re.sub(r"<[^>]*>", "", body)).split())
        if not text or text == previous:
            continue
        raw = text
        if previous and text.startswith(previous):
            text = text[len(previous) :].strip()
        previous = raw
        if not text:
            continue
        value = {
            "id": f"s{len(result) + 1:05d}",
            "start": seconds(cue["start"]),
            "end": seconds(cue["end"]),
            "text": text,
        }
        try:
            result.append(checked_segment(value))
        except ValueError:
            continue
    return result


def timeline_path(transcript_path: str | Path) -> Path:
    return Path(transcript_path).with_suffix(".timeline.json")


def save_timeline(transcript_path, words, *, complete: bool) -> None:
    segments = group_words(words)
    if segments:
        timeline_path(transcript_path).write_text(
            json.dumps(
                {
                    "version": 1,
                    "complete": complete,
                    "segments": [asdict(item) for item in segments],
                    "words": [asdict(item) for item in words],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


def load_timeline(transcript_path) -> list[Segment]:
    path = timeline_path(transcript_path)
    if not path.exists():
        return []
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Timeline exceeds size limit")
    data = json.loads(path.read_text(encoding="utf-8"))
    return [checked_segment(item) for item in data["segments"]]


def caption_srt(segments: list[Segment], start: float, end: float) -> str:
    """Render only existing cues, clamped and rebased to the selected clip."""
    cues = []
    for segment in segments:
        if segment.end <= start or segment.start >= end:
            continue
        text = html.unescape(re.sub(r"<[^>]*>", "", segment.text))
        text = text.replace("\x00", "").replace("\r", " ").replace("\n", " ")
        cues.append(
            f"{len(cues) + 1}\n{_srt_time(max(0, segment.start - start))} --> "
            f"{_srt_time(min(end, segment.end) - start)}\n{text}\n"
        )
    return "\n".join(cues)


def _srt_time(value: float) -> str:
    milliseconds = max(0, round(value * 1000))
    sec, ms = divmod(milliseconds, 1000)
    minutes, sec = divmod(sec, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{sec:02d},{ms:03d}"
