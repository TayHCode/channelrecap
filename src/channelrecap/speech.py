"""A conservative transcript-size guard for rates and trends."""
from .transcript import Transcript, word_count

MIN_WORDS = 20


def speech_status(transcript: Transcript) -> str:
    words = word_count(" ".join(s.text for s in transcript.segments))
    return "no_speech" if not words else "too_few_words" if words < MIN_WORDS else "usable"


def speech_notice(status: str, label: str) -> str:
    reason = "no speech detected" if status == "no_speech" else "too few words detected (fewer than 20)"
    return f"{reason} in {label}: excluded from rates and trends"
