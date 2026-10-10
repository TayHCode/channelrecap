"""Transcript structures, source-time conversion and stable file formats."""
from dataclasses import asdict, dataclass, field
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any

from .errors import InputError, display_source, validate_strings, validate_text
from .timecodes import covered, format_time


@dataclass
class Word:
    start: float | None
    end: float | None
    word: str
    probability: float | None = None


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


@dataclass
class Transcript:
    language: str = ""
    language_probability: float | None = None
    duration: float = 0.0
    segments: list[Segment] = field(default_factory=list)
    offset: float = 0.0
    video_id: str = ""
    schema_version: int = 1

    def add_offset(self, offset: float) -> None:
        self.offset = offset
        for segment in self.segments:
            segment.start = round(segment.start + offset, 3)
            segment.end = round(segment.end + offset, 3)
            for word in segment.words:
                word.start = round(word.start + offset, 3) if word.start is not None else None
                word.end = round(word.end + offset, 3) if word.end is not None else None
        self.duration = round(self.duration, 3)


def word_count(text: str) -> int:
    from .words import split_words
    return len(split_words(text))


def paragraphs(segments: list[Segment]) -> list[tuple[float, str]]:
    result: list[tuple[float, str]] = []
    previous: Segment | None = None
    for segment in segments:
        if not segment.text.strip():
            continue
        new = previous is None
        if previous is not None:
            elapsed = segment.start - result[-1][0]
            new = segment.start - previous.end >= 2 or elapsed >= 60 or (elapsed >= 30 and previous.text.rstrip().endswith((".", "?", "!")))
        if new:
            result.append((segment.start, " ".join(segment.text.split())))
        else:
            result[-1] = (result[-1][0], result[-1][1] + " " + " ".join(segment.text.split()))
        previous = segment
    return result


def atomic_json(path: Path, value: Any) -> None:
    validate_strings(value)
    try:
        text = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        text.encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        raise InputError("Invalid JSON output data.") from None
    atomic_text(path, text)


def atomic_text(path: Path, text: str) -> None:
    validate_text(text, "Output text")
    path.parent.mkdir(parents=True, exist_ok=True)
    name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, prefix=".writing-", delete=False) as stream:
            name = stream.name
            stream.write(text)
        os.replace(name, path)
    finally:
        if name is not None:
            Path(name).unlink(missing_ok=True)


def write_data(path: Path, transcript: Transcript) -> None:
    data = asdict(transcript)
    transcript_from_data(data)
    atomic_json(path, data)


def read_data(path: Path) -> Transcript:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return transcript_from_data(data)
    except (InputError, OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        raise InputError(f"Cannot read transcript data {path.name}: {exc}. Rebuild from corrected sources into a new --out folder; keep this folder as a backup. See docs/output-format.md.") from None


def transcript_from_data(data: Any) -> Transcript:
    try:
        validate_strings(data)
        if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data['schema_version'] != 1:
            raise ValueError("unsupported schema version")
        for name in ('duration', 'offset'):
            value = data[name]
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite nonnegative number")
        probability = data['language_probability']
        if probability is not None and (type(probability) not in (int, float) or
                not math.isfinite(probability) or not 0 <= probability <= 1):
            raise ValueError("language_probability must be null or a number between zero and one")
        for name in ('video_id', 'language'):
            validate_text(data[name])
        if not isinstance(data.get('segments'), list):
            raise ValueError("segments must be a list")
        for segment in data['segments']:
            if not isinstance(segment, dict) or not isinstance(segment.get('text'), str) or not isinstance(segment.get('words'), list):
                raise ValueError("invalid segment")
            validate_text(segment['text'])
            for item in [segment, *segment['words']]:
                if not isinstance(item, dict):
                    raise ValueError("invalid word")
                for field in ('start', 'end'):
                    value = item[field]
                    if value is None and item is not segment:
                        continue
                    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                        raise ValueError("invalid speech time")
                if item['end'] is not None and item['start'] is not None and item['end'] < item['start']:
                    raise ValueError("speech end precedes start")
            for word in segment['words']:
                validate_text(word.get('word'))
                value = word.get('probability')
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1):
                    raise ValueError("word probability must be null or a number between zero and one")
        segments = [Segment(s["start"], s["end"], s["text"], [Word(**w) for w in s["words"]]) for s in data["segments"]]
        return Transcript(segments=segments, **{k: v for k, v in data.items() if k != "segments"})
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise InputError(f"Invalid transcript data: {exc}") from None


def validate_transcript(transcript: Transcript) -> None:
    if not isinstance(transcript, Transcript):
        raise InputError("Invalid speech result: expected transcript data.")
    transcript_from_data(asdict(transcript))


def write_text(path: Path, transcript: Transcript, entry: dict[str, Any]) -> None:
    validate_transcript(transcript)
    validate_strings(entry)
    source = display_source(entry["source"])
    lines = [f"Title: {entry['title']}", f"Video id: {entry['video_id']}",
             f"Source: {source}", f"Date: {entry['date']}",
             f"Covered: {covered(entry['start'], entry['end'])}",
             f"Length: {format_time(transcript.duration)}", f"Words: {entry['words']}",
             f"Language: {transcript.language}", "Timestamps show the time in the original video."]
    lines = [" ".join(line.split()) for line in lines]
    body = "\n\n".join(f"[{format_time(time)}] {text}" for time, text in paragraphs(transcript.segments))
    atomic_text(path, "\n".join(lines) + "\n\n" + body + "\n")
