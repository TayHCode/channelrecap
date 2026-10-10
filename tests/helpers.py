"""Offline test support. Import before the package under test."""
import math
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
from types import SimpleNamespace
from typing import Any
import wave


def no_network(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("network use in tests")


socket.socket.connect = no_network
socket.create_connection = no_network
os.environ["HF_HUB_OFFLINE"] = "1"
# Speech tests inject Whisper doubles; never import a native telemetry runtime.
sys.modules["onnxruntime"] = SimpleNamespace(disable_telemetry_events=lambda: None)
ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / "scratch"
SCRATCH.mkdir(exist_ok=True)
tempfile.tempdir = str(SCRATCH)
os.environ["TMPDIR"] = str(SCRATCH)


def make_model(path: Path) -> str:
    """File-presence fixture; speech engine calls still use test doubles."""
    path.mkdir(parents=True, exist_ok=True)
    for name in ("config.json", "model.bin", "tokenizer.json", "vocabulary.json"):
        (path / name).write_text("invented fixture", encoding="utf-8")
    return str(path)


CACHED_MODEL = make_model(SCRATCH / "model-fixture")


def make_wav(path: Path, seconds: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i * 0.17)))
                                for i in range(int(16000 * seconds))))


def make_stub_tool(directory: Path, name: str, script: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!{sys.executable}\n" + script, encoding="utf-8")
    path.chmod(0o755)
    return path


class FakeTranscriber:
    created = 0

    def __init__(self, settings: Any = None) -> None:
        type(self).created += 1
        self.settings = settings

    def transcribe(self, path: Path, *, language: str | None = None,
                   progress: Any = None) -> Any:
        from channelrecap.transcript import Segment, Transcript, Word
        return Transcript(language=language or "en", language_probability=1.0,
                          duration=1.0, segments=[Segment(0.0, 1.0,
                          "Um, coffee water tastes clear.", [Word(0.0, 0.2, "Um")])])


def install_media_stubs(root: Path) -> Path:
    """Executable doubles record argv and never use a network or model."""
    common = '''import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as stream:
    stream.write(json.dumps([Path(sys.argv[0]).name, args]) + "\\n")
'''
    directory = root / 'bin'
    make_stub_tool(directory, 'ffmpeg', common + '''import wave
if os.environ.get("STUB_FFMPEG_FAIL"):
    print("synthetic decode failure", file=sys.stderr)
    sys.exit(1)
path = Path(args[-1])
path.parent.mkdir(parents=True, exist_ok=True)
with wave.open(str(path), "wb") as out:
    out.setnchannels(1)
    out.setsampwidth(2)
    out.setframerate(16000)
    out.writeframes(b"\\0\\0" * int(16000 * float(os.environ.get("STUB_SECONDS", "1"))))
''')
    make_stub_tool(directory, 'ffprobe', common + 'print(json.dumps({"streams": [{"index": 0}]}))\n')
    make_stub_tool(directory, 'yt-dlp', common + '''if "--dump-single-json" in args:
    mode = os.environ.get("STUB_LINK_MODE", "finished")
    data = {"extractor": "demo", "id": "sample", "title": "A sample talk", "upload_date": "20260110"}
    if mode == "playlist": data["entries"] = []
    if mode == "live": data["live_status"] = "is_live"
    if mode == "upcoming": data["live_status"] = "is_upcoming"
    print(json.dumps(data))
elif "--write-subs" in args:
    if os.environ.get("STUB_CAPTIONS_FAIL"):
        sys.exit(1)
    template = args[args.index("--output") + 1]
    path = template % {"id":"sample","ext":"vtt"}
    Path(path).write_text("WEBVTT")
else:
    path = args[args.index("--output") + 1]
    path = path.replace("%(extractor)s", "demo").replace("%(id)s", "sample").replace("%(ext)s", "wav")
    Path(path.replace("%%", "%")).write_bytes(b"synthetic download")
''')
    return directory
