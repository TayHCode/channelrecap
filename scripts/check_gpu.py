"""GPU smoke check; downloads the tiny.en model on first use."""
from __future__ import annotations

import tempfile
import wave
from pathlib import Path

from faster_whisper import WhisperModel


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        audio_path = Path(directory) / "silence.wav"
        with wave.open(str(audio_path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\x00\x00" * 16000)

        model = WhisperModel("tiny.en", device="cuda", compute_type="float16")
        segments, info = model.transcribe(str(audio_path), vad_filter=False)
        list(segments)
        print(f"gpu_transcription=ok model=tiny.en duration={info.duration:.1f}s")


if __name__ == "__main__":
    main()
