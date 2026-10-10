"""One library, atomic row checkpoints, and isolated source failures."""
from dataclasses import asdict, dataclass, field
import json
import math
import shutil
import sys
from pathlib import Path
from typing import Any

from . import NAME, __version__
from .speech import speech_status, speech_notice
from .captions import Cue, read_captions
from .errors import InputError, RowError, InvalidTextError, safe_error, display_source, validate_text, validate_strings
from .manifest import Row, video_id
from . import tools
from .transcriber import Settings, Transcriber, WhisperTranscriber
from .transcript import Segment, Transcript, atomic_json, read_data, word_count, write_data, write_text, validate_transcript


@dataclass
class RunOptions:
    force: bool = False
    fail_fast: bool = False
    limit: int | None = None
    language: str | None = None
    keep_audio: bool = False
    keep_downloads: bool = False
    settings: Settings = field(default_factory=Settings)
    save_site_captions: bool = False
    invalid_input: bool = field(default=False, init=False)
    failure_code: int = field(default=1, init=False)


def load_library(out_dir: Path, *, required: bool = False) -> dict[str, Any]:
    validate_text(str(out_dir), "--out")
    try:
        str(out_dir).encode("utf-8")
    except UnicodeError:
        raise InputError("--out must contain valid Unicode text.") from None
    for folder in (out_dir, *out_dir.parents):
        if folder.exists() and not folder.is_dir():
            raise InputError("--out must be a folder, with folders for every parent.")
    path = out_dir / "library.json"
    if not path.exists() and not required:
        return {"schema_version": 1, "tool": NAME, "tool_version": __version__, "videos": []}
    try:
        library = json.loads(path.read_text(encoding="utf-8"))
        validate_strings(library)
        # JSON escapes can introduce lone surrogates despite valid input bytes.
        # Check retained metadata too, before a later checkpoint reserializes it.
        json.dumps(library, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if not isinstance(library, dict) or type(library.get("schema_version")) is not int or library['schema_version'] != 1 or not isinstance(library["videos"], list):
            raise ValueError("unsupported library format")
        for entry in library["videos"]:
            if not isinstance(entry, dict) or not all(k in entry for k in ("key", "video_id", "status", "order")):
                raise ValueError("incomplete library entry")
            if not all(isinstance(entry[k], str) and entry[k] for k in ("key", "video_id", "status")) or type(entry['order']) is not int:
                raise ValueError("invalid library identity, status or order")
            if entry['status'] not in ('done', 'failed'):
                raise ValueError("unsupported video status")
            fields = ('source', 'error') if entry['status'] == 'failed' else ('source', 'title', 'date', 'language', 'transcript', 'data')
            for field in fields:
                if not isinstance(entry.get(field), str) or '\x00' in entry[field]:
                    raise ValueError(f"missing or invalid library field: {field}")
            if entry['status'] == 'done':
                for field in ('duration', 'words', 'start', 'end'):
                    value = entry[field]
                    if value is None and field in ('start', 'end'):
                        continue
                    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                        raise ValueError(f"invalid library field: {field}")
                for field in ('data', 'transcript'):
                    if not entry[field] or Path(entry[field]).is_absolute():
                        raise ValueError(f"invalid library path: {field}")
                    library_path(out_dir, entry[field])
        return library
    except (InputError, OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        raise InputError(f"Cannot read library.json: {exc}. Rebuild from corrected sources into a new --out folder; keep this folder as a backup. See docs/output-format.md.") from None


def library_path(out_dir: Path, relative: str) -> Path:
    try:
        path = (out_dir / relative).resolve()
    except (OSError, ValueError, RuntimeError, TypeError):
        raise InputError("Invalid library file path.") from None
    if not path.is_relative_to(out_dir.resolve()):
        raise InputError("Library file path must stay inside the output folder.")
    return path


def finished(out_dir: Path) -> tuple[dict[str, Any], list[tuple[dict[str, Any], Transcript]]]:
    library = load_library(out_dir, required=True)
    entries = [entry for entry in library["videos"] if entry["status"] == "done"]
    entries.sort(key=(lambda e: (e["date"], e["order"])) if all(e["date"] for e in entries) else (lambda e: e["order"]))
    return library, [(e, read_data(library_path(out_dir, e["data"]))) for e in entries]


def transcribe(rows: list[Row], out_dir: Path, options: RunOptions,
               *, transcriber_factory: Any = None) -> tuple[int, int, int]:
    validate_strings(asdict(options))
    for row in rows:
        validate_strings(asdict(row))
    library = load_library(out_dir)
    for entry in library['videos']:
        if entry['status'] == 'done':
            path = library_path(out_dir, entry['data'])
            if path.is_file():
                read_data(path)
    by_key = {entry["key"]: entry for entry in library["videos"]}
    def complete(row: Row) -> bool:
        entry = by_key.get(row.key)
        return bool(entry and entry["status"] == "done" and
                    all(library_path(out_dir, entry[name]).is_file() for name in ("data", "transcript")))
    pending = [r for r in rows if options.force or not complete(r)]
    skipped = len(rows) - len(pending)
    if options.limit is not None:
        pending = pending[:options.limit]
    if options.settings.device == "cuda" and any(r.kind != "captions" and complete(r) for r in rows) and not options.force:
        print("warning: CUDA was not validated for reused cached media; those rows load no speech model. "
              "Use doctor --device cuda and --force to check GPU transcription.")
    # Decode all pending captions before any output/checkpoint mutation. Ordinary
    # missing-cue errors remain isolated rows; invalid text leaves the library intact.
    caption_inputs: dict[str, list[Cue] | RowError] = {}
    for row in pending:
        if row.kind == 'captions':
            assert row.path is not None
            try:
                caption_inputs[row.key] = read_captions(row.path, start=row.start, end=row.end, source=row.source)
            except InvalidTextError:
                raise
            except RowError as exc:
                caption_inputs[row.key] = exc
    tools.preflight({r.kind for r in pending}, injected=transcriber_factory is not None)
    if any(r.kind == "link" for r in pending):
        print(f'Reminder: {NAME} is for your own videos. See the README section "For your own videos".')
    for row in rows:
        if row.key in by_key:
            by_key[row.key]["order"] = row.order
    done = failed = 0
    model: Transcriber | None = None
    cache = out_dir / ".cache"
    try:
        for position, row in enumerate(pending, 1):
            prior = by_key.get(row.key)
            fresh = prior is None
            entry = dict(prior) if prior else dict(video_id=video_id(row.title, row.key))
            entry["key"] = row.key
            entry.update(order=row.order, source=row.source, source_kind=row.kind, title=row.title or "Untitled",
                         date=row.date, start=row.start, end=row.end, language=row.language or options.language or "",
                         status="failed", error="", duration=0.0, words=0, transcript="", data="")
            wav = cache / "audio" / f"{row.key}.wav"
            initial = dict(entry)
            invalid = False
            try:
                if row.kind == "captions":
                    cues = caption_inputs[row.key]
                    if isinstance(cues, RowError):
                        raise cues
                    duration = max(c.end for c in cues) - (row.start or 0)
                    transcript = Transcript(language=entry["language"], duration=round(duration, 3),
                            segments=[Segment(c.start, c.end, c.text) for c in cues], offset=row.start or 0)
                    entry["transcription"] = {"engine": "captions"}
                else:
                    source = row.path
                    if row.kind == "link":
                        metadata = tools.link_metadata(row.source)
                        entry["title"] = row.title or metadata.get("title") or "Untitled"
                        entry["date"] = row.date or metadata["date"]
                        if fresh:
                            entry["video_id"] = video_id(entry["title"], row.key)
                        if options.save_site_captions:
                            tools.save_site_captions(row.source, out_dir / "site-captions" / entry["video_id"],
                                                     row.language or options.language or "en")
                        source = tools.download_audio(row.source, metadata, cache / "downloads")
                    assert source is not None
                    tools.clip_audio(source, wav, row.start, row.end)
                    if model is None:
                        model = (transcriber_factory or WhisperTranscriber)(options.settings)
                    label = entry["video_id"]
                    def progress(percent: int) -> None:
                        print(f"[{position}/{len(pending)}] transcribing {label} {percent}%")
                    transcript = model.transcribe(wav, language=entry["language"] or None, progress=progress)
                    validate_transcript(transcript)
                    transcript.add_offset(row.start or 0)
                    device = getattr(model, "device", options.settings.device)
                    compute = getattr(model, "compute_type", options.settings.compute_type or ("float16" if device == "cuda" else "int8"))
                    entry["transcription"] = {"engine": "faster-whisper", "model": options.settings.model,
                            "device": device, "compute_type": compute, "verbatim_hint": options.settings.verbatim_hint}
                transcript.video_id = entry["video_id"]
                validate_transcript(transcript)
                entry.update(status="done", duration=transcript.duration, language=transcript.language,
                             words=word_count(" ".join(s.text for s in transcript.segments)),
                             transcript=f"transcripts/{entry['video_id']}.txt", data=f"data/{entry['video_id']}.json")
                entry["speech_status"] = speech_status(transcript)
                entry["trend_usable"] = entry["speech_status"] == "usable"
                entry["notice"] = "" if entry["trend_usable"] else speech_notice(entry["speech_status"], entry["video_id"])
                if entry["notice"]:
                    print(entry["notice"] + "; transcript retained.")
                validate_strings(entry)
                json.dumps(entry, ensure_ascii=False, allow_nan=False).encode('utf-8')
                write_data(library_path(out_dir, entry["data"]), transcript)
                write_text(library_path(out_dir, entry["transcript"]), transcript, entry)
                done += 1
            except (InputError, RowError, OSError, UnicodeError) as exc:
                invalid = isinstance(exc, (InputError, UnicodeError))
                options.invalid_input |= invalid
                entry = initial
                entry.update(status="failed", error=safe_error(str(exc), row.source, wav, out_dir))
                options.failure_code = max(options.failure_code, getattr(exc, "exit_code", 1))
                print(f"error: {display_source(row.source)}: {entry['error']}", file=sys.stderr)
                failed += 1
            finally:
                if not options.keep_audio:
                    wav.unlink(missing_ok=True)
            # Invalid replacement metadata must never evict a usable prior row.
            if invalid and prior is not None:
                if options.fail_fast:
                    break
                continue
            if prior is not None:
                library['videos'][library['videos'].index(prior)] = entry
            else:
                library['videos'].append(entry)
            by_key[row.key] = entry
            atomic_json(out_dir / "library.json", library)
            if entry["status"] == "failed" and options.fail_fast:
                break
        if not pending and library["videos"]:
            atomic_json(out_dir / "library.json", library)
    finally:
        if pending and not options.keep_downloads and (cache / "downloads").exists():
            shutil.rmtree(cache / "downloads")
        for folder in (cache / "audio", cache):
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
    return done, skipped, failed
