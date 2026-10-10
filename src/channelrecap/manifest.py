"""Validate every input before starting any output work."""
import csv
from dataclasses import dataclass
from datetime import date as Date
import hashlib
from pathlib import Path
import re
from collections.abc import Iterable
from urllib.parse import urlsplit, parse_qs

from .errors import InputError, UnsupportedTypeError, display_source, validate_text
from .timecodes import parse_time

COLUMNS = {"source", "url", "start", "end", "title", "date", "language"}


@dataclass(frozen=True)
class Row:
    source: str
    start: float | None
    end: float | None
    title: str
    date: str
    language: str
    key: str
    kind: str
    path: Path | None
    order: int


def source_key(source: str, start: float | None, end: float | None) -> str:
    first = "" if start is None else f"{start:.3f}"
    last = "" if end is None else f"{end:.3f}"
    return hashlib.sha256(f"{source}\n{first}\n{last}".encode()).hexdigest()[:8]


def slug(text: str) -> str:
    return re.sub("[^a-z0-9]+", "-", text.lower()).strip("-")[:60].rstrip("-")


def video_id(title: str, key: str) -> str:
    return f"{slug(title) or 'video'}-{key}"


def _validate(records: Iterable[tuple[int, dict[str, str]]], base_dir: Path) -> list[Row]:
    rows: list[Row] = []
    problems: list[str] = []
    seen: set[str] = set()
    unsupported = False
    for line, record in records:
        try:
            for name, value in record.items():
                for field in value if isinstance(value, list) else [value]:
                    if field is not None:
                        validate_text(field, f"row {line}: {name or 'extra field'}")
        except InputError as exc:
            problems.append(str(exc))
            continue
        values = {k: (v or "").strip() for k, v in record.items() if k in COLUMNS}
        if not any(values.values()):
            continue
        try:
            for value in values.values():
                value.encode("utf-8")
        except UnicodeError:
            problems.append(f"row {line}: fields must contain valid Unicode text")
            continue
        row_problems: list[str] = []
        source = values.get("source", values.get("url", ""))
        if not source:
            continue
        if "\x00" in source:
            row_problems.append("source contains a NUL byte")
        times: list[float | None] = []
        for name in ("start", "end"):
            try:
                times.append(parse_time(values[name]) if values.get(name) else None)
            except InputError as exc:
                times.append(None)
                row_problems.append(f"{name}: {exc}")
        start, end = times
        if end is not None and end <= (start or 0):
            row_problems.append("end must be greater than start")
        date = values.get("date", "")
        if date:
            try:
                if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date):
                    raise ValueError
                Date.fromisoformat(date)
            except ValueError:
                row_problems.append("date must be a valid YYYY-MM-DD date")
        link = source.startswith(("http://", "https://"))
        if link:
            try:
                url = urlsplit(source)
            except ValueError:
                row_problems.append("invalid URL: use an individual video URL")
            else:
                if 'list' in parse_qs(url.query, keep_blank_values=True) or re.search(r"/(?:playlist|channel|c|user)(?:/|$)|/@", url.path):
                    row_problems.append("channel/playlist URLs are not crawled, including manifest rows; list each individual video URL")
        path = None
        if not link:
            try:
                path = (base_dir / Path(source).expanduser()).resolve()
            except (ValueError, RuntimeError, OSError):
                row_problems.append("source is not a usable local path; check its spelling and user prefix")
        kind = "link" if link else "captions" if path and path.suffix.lower() in {".vtt", ".srt"} else "media"
        if path is not None and kind == "media" and path.suffix and path.suffix.lower() not in {
            '.aac', '.aiff', '.aif', '.au', '.flac', '.m4a', '.mp3', '.ogg', '.oga', '.opus', '.wav', '.wma',
            '.mp4', '.mkv', '.mov', '.avi', '.webm', '.mpeg', '.mpg', '.ts', '.m2ts', '.3gp', '.wmv',
            '.flv', '.m4v', '.mxf', '.ogv', '.caf'
        }:
            row_problems.append(f"unsupported file type ({path.suffix.lower()}): use audio/video (or .vtt/.srt captions)")
            unsupported = True
        if path is not None and (not path.is_file()):
            row_problems.append(f"missing local file: {display_source(source)}")
        elif path is not None:
            try:
                with path.open("rb") as stream:
                    stream.read(1)
            except OSError:
                row_problems.append(f"unreadable local file: {display_source(source)}")
        key = source_key(source, start, end)
        if key in seen:
            row_problems.append("duplicate source and time range")
        seen.add(key)
        if row_problems:
            problems.extend(f"row {line}: {problem}" for problem in row_problems)
            continue
        rows.append(Row(source, start, end, values.get("title", "") or (path.stem if path else ""),
                        date, values.get("language", ""), key, kind, path, len(rows) + 1))
    if problems:
        error = UnsupportedTypeError if unsupported else InputError
        raise error("Input validation failed; no rows were processed.\n" + "\n".join(problems))
    if not rows:
        raise InputError("No usable rows.")
    return rows


def read_manifest(path: Path) -> list[Row]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            for name in reader.fieldnames or []:
                validate_text(name, "Manifest header")
            if reader.fieldnames and {"source", "url"}.issubset(reader.fieldnames):
                raise InputError("Use source or url, not both columns.")
            if not reader.fieldnames or not {"source", "url"}.intersection(reader.fieldnames):
                raise InputError("Manifest requires an exact lowercase source column in comma-separated CSV; remove surrounding header spaces.")
            unknown = set(reader.fieldnames) - COLUMNS
            if unknown:
                print("warning: unknown columns ignored: " + ", ".join(sorted(unknown)))
            return _validate(((reader.line_num, row) for row in reader), path.resolve().parent)
    except (OSError, UnicodeError, ValueError, csv.Error) as exc:
        raise InputError(f"Cannot read manifest {path}: {exc}") from None


def read_sources(sources: list[str], base_dir: Path | None = None) -> list[Row]:
    return _validate(((n, {"source": s}) for n, s in enumerate(sources, 1)), base_dir or Path.cwd())
