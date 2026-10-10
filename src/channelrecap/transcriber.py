"""Optional local speech engine. Imports happen only on first media work."""
from dataclasses import dataclass
from contextlib import contextmanager
from pathlib import Path
import logging
import os
import sys
from typing import Any, Callable, Iterator, Protocol
from .errors import RowError, ToolError, InvalidTextError
from .transcript import Segment, Transcript, Word
from .tools import TRANSCRIBE_INSTALL

VERBATIM_HINT = "Um, so, like, this is a word for word transcript, uh, with every filler word kept, you know."


def complete_model(path: str) -> bool:
    folder = Path(path)
    required = [folder / name for name in ("config.json", "model.bin", "tokenizer.json")]
    vocabulary = list(folder.glob("vocabulary.*"))
    return bool(vocabulary) and all(p.is_file() and p.stat().st_size > 0 for p in [*required, *vocabulary])


@contextmanager
def download_messages() -> Iterator[None]:
    """Keep successful hub warnings on the CLI's notice stream; restore logging."""
    logger = logging.getLogger("huggingface_hub")
    handlers, propagate = logger.handlers[:], logger.propagate
    logger.handlers = [logging.StreamHandler(sys.stdout)]
    logger.propagate = False
    try:
        yield
    finally:
        logger.handlers = handlers
        logger.propagate = propagate


@dataclass
class Settings:
    model: str = "small"
    device: str = "cpu"
    compute_type: str | None = None
    prompt: str = ""
    verbatim_hint: bool = False

    @property
    def initial_prompt(self) -> str | None:
        text = " ".join(p for p in (self.prompt.strip(), VERBATIM_HINT if self.verbatim_hint else "") if p)
        return text or None


class Transcriber(Protocol):
    def transcribe(self, path: Path, *, language: str | None = None,
                   progress: Callable[[int], None] | None = None) -> Transcript: ...


class WhisperTranscriber:
    def __init__(self, settings: Settings) -> None:
        # ORT's 1DS uploader starts during import. The API alone is too late
        # for initialization events; opt out before either speech import.
        os.environ["ORT_DISABLE_TELEMETRY"] = "1"
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        os.environ["DO_NOT_TRACK"] = "1"
        try:
            import onnxruntime
            onnxruntime.disable_telemetry_events()
            import faster_whisper
        except (ImportError, OSError) as exc:
            raise ToolError(f"Cannot load faster-whisper: {exc}; {TRANSCRIBE_INSTALL}") from None
        self.settings = settings
        device = settings.device
        if device == "auto":
            try:
                import ctranslate2
                device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
            except (ImportError, RuntimeError, OSError):
                device = "cpu"
            print(f"Auto device: {device}")
        self.device = device
        self.compute_type = settings.compute_type or ("int8" if device == "cpu" else "float16")
        model_path = settings.model
        if Path(model_path).is_dir():
            if not complete_model(model_path):
                raise ToolError("Local speech model folder is incomplete; provide config.json, model.bin, tokenizer.json and vocabulary files.")
        else:
            try:
                model_path = faster_whisper.download_model(settings.model, local_files_only=True)
                if not complete_model(model_path):
                    raise FileNotFoundError("Incomplete cached model")
            except Exception:
                sizes = {"tiny": "75 MB", "base": "145 MB", "small": "0.5 GB", "medium": "1.5 GB", "large-v3": "3 GB"}
                size = f" (about {sizes[settings.model]})" if settings.model in sizes else ""
                if os.environ.get("HF_HUB_OFFLINE", "").upper() in {"1", "ON", "YES", "TRUE"}:
                    raise ToolError("Speech model is not available in the local cache. With HF_HUB_OFFLINE=1, download it first online or use --model with a complete local model folder.") from None
                print(f'Downloading speech model "{settings.model}" on first use{size}. Later runs load the cached files offline.')
                try:
                    with download_messages():
                        model_path = faster_whisper.download_model(settings.model)
                    if not complete_model(model_path):
                        raise FileNotFoundError("Downloaded model folder is incomplete")
                except Exception as exc:
                    raise ToolError(f"Cannot download speech model: {exc}") from None
        def load() -> object:
            return faster_whisper.WhisperModel(model_path, device=self.device,
                                               local_files_only=True,
                                               compute_type=self.compute_type)
        try:
            self.model = load()
        except UnicodeError:
            raise InvalidTextError("Speech model returned invalid Unicode text.") from None
        except Exception as exc:
            if settings.device == "auto" and self.device == "cuda":
                print("CUDA initialization failed; auto device is using cpu.")
                self.device = "cpu"
                self.compute_type = settings.compute_type or "int8"
                try:
                    self.model = load()
                except UnicodeError:
                    raise InvalidTextError("Speech model returned invalid Unicode text.") from None
                except Exception as fallback:
                    raise ToolError(f"Cannot initialize speech model: {fallback}") from None
            else:
                hint = " See docs/gpu.md." if device == "cuda" else ""
                raise ToolError(f"Cannot initialize speech model: {exc}.{hint}") from None

    def transcribe(self, path: Path, *, language: str | None = None,
                   progress: Callable[[int], None] | None = None) -> Transcript:
        try:
            data = transcribe_audio(self.model, path, language=language,
                                    initial_prompt=self.settings.initial_prompt, progress=progress)
            return Transcript(language=data['language'], language_probability=data['language_probability'],
                              duration=data['duration'], segments=[
                Segment(item['start'], item['end'], item['text'], [Word(**word) for word in item['words']])
                for item in data['segments']])
        except UnicodeError:
            raise InvalidTextError("Speech transcription returned invalid Unicode text.") from None
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            raise RowError(f"Speech transcription failed: {exc}") from None


def transcribe_audio(
    model: Any,
    audio_path: Path,
    *,
    language: str | None,
    initial_prompt: str | None,
    progress: Callable[[int], None] | None = None,
) -> dict[str, Any]:
    segments_iter, info = model.transcribe(
        str(audio_path),
        language=language or None,
        beam_size=5,
        vad_filter=True,
        word_timestamps=True,
        initial_prompt=initial_prompt,
    )
    segments: list[dict[str, Any]] = []
    next_step = 5
    for segment in segments_iter:
        segments.append(
            {
                "start": round(float(segment.start), 3),
                "end": round(float(segment.end), 3),
                "text": segment.text.strip(),
                "words": [
                    {
                        "start": round(float(word.start), 3) if word.start is not None else None,
                        "end": round(float(word.end), 3) if word.end is not None else None,
                        "word": word.word,
                        "probability": round(float(word.probability), 5),
                    }
                    for word in (segment.words or [])
                ],
            }
        )
        percent = min(100, int(segment.end / info.duration * 100)) if info.duration else 100
        while next_step <= percent:
            if progress:
                progress(next_step)
            next_step += 5
    return {
        "language": info.language,
        "language_probability": round(float(info.language_probability), 5),
        "duration": round(float(info.duration), 3),
        "segments": segments,
    }

