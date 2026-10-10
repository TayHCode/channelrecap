"""Narrow subprocess interfaces for optional media programs."""
from datetime import datetime
import importlib.util
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any
import wave
from .errors import RowError, ToolError, DamagedMediaError, NoAudioError, InvalidTextError, safe_error, validate_strings

TRANSCRIBE_INSTALL = 'from the clone folder, run: python -m pip install --upgrade ".[transcribe]" "av<19"'


def has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ValueError, ImportError):
        return False


def downloader() -> list[str] | None:
    executable = shutil.which("yt-dlp")
    if executable:
        return [executable]
    return [sys.executable, "-m", "yt_dlp"] if has_module("yt_dlp") else None


def preflight(kinds: set[str], *, injected: bool = False) -> None:
    missing: list[str] = []
    if kinds & {"media", "link"}:
        if not shutil.which("ffmpeg"):
            missing.append("ffmpeg: install the ffmpeg package with your system package manager")
        if not injected and not has_module("faster_whisper"):
            missing.append(f'faster-whisper: {TRANSCRIBE_INSTALL}')
    if "link" in kinds and downloader() is None:
        missing.append('yt-dlp: from the clone folder, run: python -m pip install ".[links]"')
    if missing:
        raise ToolError("Missing components:\n" + "\n".join(missing))


def media_failure(detail: str, program: str) -> RowError:
    """Report only known diagnostic phrases, never free-form filenames or metadata."""
    lower = detail.casefold()
    if any(s in lower for s in ("does not contain any stream", "matches no streams", "no audio stream")):
        return NoAudioError("no audio stream: use a file or time range containing audio.")
    reasons = ("moov atom not found", "invalid data found when processing input", "end of file",
               "invalid header", "permission denied", "no such file or directory", "error opening input")
    reason = next((s for s in reasons if s in lower), "media decoding failed")
    return DamagedMediaError(f"could not read this media file (damaged or incomplete): decoder says: {reason}")


def invoke(args: list[str], *, timeout: float | None = None, link: bool = False, media: bool = False) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', timeout=timeout, check=False)
    except UnicodeError:
        raise InvalidTextError("External program returned invalid Unicode text.") from None
    except FileNotFoundError:
        raise ToolError(f"{Path(args[0]).name} missing: install the required tool or add it to PATH.") from None
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RowError(safe_error(f"External program failed: {exc}", *args)) from None
    validate_strings([result.stdout, result.stderr])
    if result.returncode:
        detail = result.stderr.strip() or f"exit {result.returncode}"
        if media:
            raise media_failure(detail, Path(args[0]).name)
        hint = "; try upgrading yt-dlp, or use the original file" if link else ""
        raise RowError(safe_error(f"{Path(args[0]).name}: {detail}{hint}", *(arg for arg in args if Path(arg).is_absolute())))
    return result.stdout


def safe_argument(path: Path) -> str:
    value = str(path)
    return "./" + value if value.startswith("-") else value


def clip_audio(source: Path, output: Path, start: float | None, end: float | None) -> None:
    if source.is_file() and shutil.which("ffprobe"):
        probe = invoke(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                        "-of", "json", safe_argument(source)], media=True)
        try:
            streams = json.loads(probe)["streams"]
        except (ValueError, KeyError, TypeError):
            raise DamagedMediaError("could not read this media file (damaged or incomplete): decoder says: invalid stream metadata") from None
        if not streams:
            raise NoAudioError("no audio stream: use a file or time range containing audio.")
    output.parent.mkdir(parents=True, exist_ok=True)
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if start is not None:
        args.extend(["-ss", f"{start:.3f}"])
    args.extend(["-i", safe_argument(source)])
    if end is not None:
        args.extend(["-t", f"{end - (start or 0):.3f}"])
    args.extend(["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", safe_argument(output)])
    invoke(args, media=True)
    try:
        with wave.open(str(output), "rb") as audio:
            length = audio.getnframes() / audio.getframerate()
    except (OSError, EOFError, wave.Error):
        length = 0
    if length < 0.5:
        raise NoAudioError("no audio stream in that part of the source: No audio found; select a longer range.")


def link_metadata(url: str) -> dict[str, Any]:
    command = downloader()
    if command is None:
        raise ToolError("yt-dlp is missing; install the links extra.")
    text = invoke(command + ["--no-playlist", "--skip-download", "--no-warnings", "--socket-timeout", "30", "--dump-single-json", url], timeout=120, link=True)
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError
    except ValueError:
        raise RowError("yt-dlp returned invalid metadata.") from None
    try:
        validate_strings(data)
    except InvalidTextError as exc:
        raise InvalidTextError(f"yt-dlp returned invalid Unicode or NUL metadata: {exc}") from None
    for field in ('title', 'live_status', '_type'):
        if data.get(field) is not None and not isinstance(data[field], str):
            raise RowError(f"yt-dlp returned invalid metadata field: {field} must be text.")
        try:
            (data.get(field) or "").encode("utf-8")
        except UnicodeError:
            raise RowError(f"yt-dlp returned invalid Unicode in metadata field: {field}.") from None
    if data.get("_type") == "playlist" or isinstance(data.get("entries"), list):
        raise RowError("This link is a playlist or channel; channel and playlist manifest rows are not crawled. List each of your videos on its own row.")
    if data.get("live_status") in {"is_live", "is_upcoming"}:
        raise RowError("This stream has not finished. Run again after it ends.")
    for field in ("extractor", "id"):
        value = data.get(field)
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", value) or value in {".", ".."}:
            raise RowError("yt-dlp returned an unsafe or missing video identity.")
    data["date"] = ""
    if data.get("upload_date"):
        try:
            data["date"] = datetime.strptime(data["upload_date"], "%Y%m%d").date().isoformat()
        except (TypeError, ValueError):
            pass
    return data


def download_audio(url: str, metadata: dict[str, Any], cache: Path) -> Path:
    prefix = f"{metadata['extractor']}_{metadata['id']}"
    def cached() -> list[Path]:
        return sorted(p for p in cache.glob(prefix + ".*") if p.stem == prefix and p.is_file() and not p.is_symlink()
                      and p.suffix.lower() not in {".json", ".part", ".vtt", ".srt", ".ytdl"})
    files = cached()
    if files:
        return files[0]
    cache.mkdir(parents=True, exist_ok=True)
    command = downloader()
    if command is None:
        raise ToolError("yt-dlp is missing; install the links extra.")
    template = str(Path(str(cache).replace('%', '%%')) / "%(extractor)s_%(id)s.%(ext)s")
    invoke(command + ["--no-playlist", "--no-overwrites", "--format", "bestaudio/best", "--output", template, url], link=True)
    files = cached()
    if not files:
        raise RowError("yt-dlp did not produce an audio file; try upgrading yt-dlp, or use the original file.")
    return files[0]


def check_audio_decode() -> None:
    """Decode generated PCM in memory: no file, network or speech model needed."""
    from faster_whisper.audio import decode_audio
    audio = io.BytesIO()
    with wave.open(audio, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\x00\x00" * 1600)
    audio.seek(0)
    samples = decode_audio(audio, sampling_rate=16000)
    if len(samples) != 1600:
        raise ValueError("audio decode returned an unexpected sample count")


def doctor(device: str = "cpu") -> int:
    missing: list[str] = []
    supported = sys.version_info >= (3, 11)
    print(f"Python {sys.version.split()[0]}: {'ok' if supported else 'unsupported'}")
    if not supported:
        missing.append("python")
    for name, present in (("ffmpeg", bool(shutil.which("ffmpeg"))), ("faster-whisper", has_module("faster_whisper")), ("yt-dlp", downloader() is not None)):
        print(f"{name}: {'ok' if present else 'missing'}")
        if not present and name != "yt-dlp":
            missing.append(name)
    if "faster-whisper" not in missing:
        try:
            check_audio_decode()
        except (ImportError, RuntimeError, ValueError, TypeError, OSError) as exc:
            missing.append("audio-decode")
            print(f"audio decode: unusable ({type(exc).__name__}); repair: {TRANSCRIBE_INSTALL}")
        else:
            print("audio decode: ok (generated PCM, no model)")
    runtime = next((n for n in ("deno", "node", "bun") if shutil.which(n)), "not found")
    print(f"JavaScript runtime: {runtime}; some sites need one for yt-dlp; see the yt-dlp documentation")
    if device == "cuda":
        try:
            import ctranslate2
            count = ctranslate2.get_cuda_device_count()
        except (ImportError, RuntimeError, OSError):
            count = 0
        print(f"CUDA devices: {count}")
        if count == 0:
            missing.append("cuda")
    print("doctor missing=" + ",".join(missing) if missing else "doctor ok")
    return 3 if missing else 0


def save_site_captions(url: str, folder: Path, language: str = "en") -> None:
    """Save optional reference copies; an unavailable caption never fails a row."""
    try:
        command = downloader()
        if command is None:
            return
        folder.mkdir(parents=True, exist_ok=True)
        template = str(Path(str(folder).replace('%', '%%')) / "%(id)s.%(ext)s")
        invoke(command + ["--no-playlist", "--skip-download", "--write-subs", "--write-auto-subs",
               "--sub-langs", f"{language}.*,{language}", "--sub-format", "vtt", "--output", template, url],
               timeout=120, link=True)
    except (RowError, ToolError, OSError):
        pass
    finally:
        try:
            folder.rmdir()
        except OSError:
            pass
