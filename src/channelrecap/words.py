"""Shared Unicode words, original sentences, and segment timestamps."""
from dataclasses import dataclass, field
from importlib.resources import files
import re
from . import NAME
from .transcript import Segment

WORD_PATTERN = re.compile(r"[^\W_]+(?:'[^\W_]+)*")


def normalise(text: str) -> str:
    return text.replace("’", "'").replace("‘", "'").casefold()


def split_words(text: str) -> list[str]:
    return WORD_PATTERN.findall(normalise(text))


def word_spans(text: str) -> list[tuple[str, int, int]]:
    """Casefolded words paired with offsets into the original text.

    A character may expand into several letters or even split a word on folding.
    Map every folded character back before matching; never zip two word scanners.
    """
    parts: list[str] = []
    origins: list[int] = []
    for position, char in enumerate(text):
        folded = normalise(char)
        parts.append(folded)
        origins.extend([position] * len(folded))
    return [(match.group(), origins[match.start()], origins[match.end() - 1] + 1)
            for match in WORD_PATTERN.finditer("".join(parts))]


def package_text(name: str) -> str:
    return files(__package__).joinpath("data", name).read_text(encoding="utf-8").replace("{tool_name}", NAME)


@dataclass(frozen=True)
class SpokenWord:
    text: str
    start: float
    sentence_start: bool
    begin: int = 0
    end: int = 0


@dataclass
class Sentence:
    text: str
    end_mark: str
    start: float
    words: list[SpokenWord] = field(default_factory=list)


def sentences(segments: list[Segment]) -> list[Sentence]:
    result: list[Sentence] = []
    pending: list[str] = []
    pending_length = 0
    words: list[SpokenWord] = []
    for segment in segments:
        for match in re.finditer(r"[^.!?]+[.!?]*|[.!?]+", segment.text):
            chunk = " ".join(match.group().split())
            if not chunk:
                continue
            offset = pending_length + bool(pending)
            pending.append(chunk)
            pending_length = offset + len(chunk)
            for word, begin, end in word_spans(chunk):
                words.append(SpokenWord(word, segment.start, not words, offset + begin, offset + end))
            ending = re.search(r"[.!?]+$", chunk)
            if ending:
                if words:
                    result.append(Sentence(" ".join(" ".join(pending).split()), ending.group(), words[0].start, words))
                pending, words, pending_length = [], [], 0
    if words:
        result.append(Sentence(" ".join(" ".join(pending).split()), "", words[0].start, words))
    return result


def spoken_words(segments: list[Segment]) -> list[SpokenWord]:
    return [word for sentence in sentences(segments) for word in sentence.words]
