"""Deterministic hints for reading, never semantic conclusions."""
from collections import Counter
from dataclasses import dataclass, field
from statistics import fmean
from .transcript import Transcript
from .words import Sentence, package_text, sentences, split_words


def trend_label(values: list[float], counts: list[int]) -> str:
    """Conservative descriptive rate comparison, not a significance test."""
    if len(values) != len(counts):
        raise ValueError("Trend rates and counts must describe the same videos.")
    if len(values) < 3:
        return "not enough videos for a trend (needs 3 usable videos)"
    if min(counts) < 3 or sum(counts) < 30:
        return "too few to call a trend (needs 3 matches per video and 30 total)"
    if values[0] == values[-1]:
        return "steady"
    half = len(values) // 2
    first, last = fmean(values[:half]), fmean(values[-half:])
    direction = 1 if values[-1] > values[0] else -1
    consistent = sum((b - a) * direction > 0 for a, b in zip(values, values[1:]))
    if consistent * 3 < (len(values) - 1) * 2:
        return "no consistent direction"
    if first and values[0]:
        ratio, endpoint_ratio = last / first, values[-1] / values[0]
        if direction > 0 and min(ratio, endpoint_ratio) >= 1.25:
            return "rising"
        if direction < 0 and max(ratio, endpoint_ratio) <= 0.75:
            return "falling"
    return "steady"


def rate(count: int, words: int) -> float:
    return count * 1000 / words if words else 0.0


def default_filler_words() -> set[str]:
    return {word for line in package_text("fillers_en.txt").splitlines()
            for word in split_words(line.split("#", 1)[0])}


def term_counts(transcript: Transcript, filler_words: set[str] | None = None) -> Counter[str]:
    excluded = set(package_text("stopwords_en.txt").splitlines()) | (default_filler_words() if filler_words is None else filler_words)
    def qualifies(word: str) -> bool:
        return len(word) >= 3 and not word.isdigit() and word not in excluded
    counts: Counter[str] = Counter()
    for sentence in sentences(transcript.segments):
        words = [w.text for w in sentence.words]
        counts.update(w for w in words if qualifies(w))
        counts.update(a + " " + b for a, b in zip(words, words[1:]) if qualifies(a) and qualifies(b))
    return counts


def frequent(counts: Counter[str]) -> list[tuple[str, int]]:
    return sorted(((term, count) for term, count in counts.items() if count >= (2 if " " in term else 3)), key=lambda item: (-item[1], item[0]))[:15]


def recurring(counts: list[Counter[str]]) -> list[str]:
    terms = set().union(*(set(c) for c in counts))
    shared = [term for term in terms if sum(c[term] >= 2 for c in counts) >= 2]
    return sorted(shared, key=lambda term: (-sum(c[term] >= 2 for c in counts), -sum(c[term] for c in counts), term))[:25]


def unique_terms(counts: list[Counter[str]], position: int) -> list[tuple[str, int]]:
    eligible = Counter({term: count for term, count in counts[position].items()
                        if all(other[term] == 0 for n, other in enumerate(counts) if n != position)})
    return frequent(eligible)[:5]


def excerpt(sentence: Sentence, first: int = 0, *, last: int | None = None) -> tuple[float, str]:
    """Keep hint text bounded without changing sentence or filler counting rules."""
    words = sentence.words
    last = min(last if last is not None else len(words), first + 40)
    start = 0 if first == 0 else words[first].begin
    end = len(sentence.text) if last == len(words) else words[last].begin
    text = sentence.text[start:min(end, start + 281)].strip()
    clipped = end - start > 280
    if clipped:
        text = text[:280].rsplit(" ", 1)[0]
    return words[first].start, ("… " if first else "") + text + (" …" if clipped or last < len(words) or not sentence.end_mark else "")


@dataclass
class Hints:
    items: list[tuple[float, str] | tuple[float, str, str]] = field(default_factory=list)
    omitted: int = 0

    def add(self, sentence: Sentence, limit: int, first: int = 0, *,
            last: int | None = None, label: str = "") -> None:
        if len(self.items) >= limit:
            self.omitted += 1
            return
        hint = excerpt(sentence, first, last=last)
        self.items.append((*hint, label) if label else hint)


def questions(transcript: Transcript, limit: int = 30) -> Hints:
    result = Hints()
    for sentence in sentences(transcript.segments):
        if sentence.end_mark.endswith("?") and len(sentence.words) >= 4:
            result.add(sentence, limit)
        elif not sentence.end_mark:
            starts = [i for i, word in enumerate(sentence.words)
                      if word.text in {"who", "what", "when", "where", "why", "how", "which"}]
            for n, first in enumerate(starts):
                last = starts[n + 1] if n + 1 < len(starts) else len(sentence.words)
                if last - first >= 4:
                    result.add(sentence, limit, first, last=last, label="possible question; no punctuation")
    return result


def deferrals(transcript: Transcript, limit: int = 20) -> Hints:
    entries = [(line, split_words(line)) for line in package_text("deferrals_en.txt").splitlines() if line.strip()]
    result = Hints()
    for sentence in sentences(transcript.segments):
        words = [w.text for w in sentence.words]
        for text, entry in entries:
            found = next((i for i in range(len(words) - len(entry) + 1) if words[i:i + len(entry)] == entry), None)
            if found is not None:
                if text == "later":
                    action = any(w in {"will", "i'll", "we'll", "cover", "explain", "compare", "discuss", "show"} for w in words)
                    future = found in {0, len(words) - 1} or any(w in {"will", "i'll", "we'll"} for w in words)
                    if not action or not future:
                        continue
                # These time phrases also occur in routine sign-offs. Keep a
                # substantive promise even if the sentence also thanks viewers.
                if text in {"next week", "next time", "later", "i'll", "we'll", "i will", "we will"} and "see you" in " ".join(words) and not any(
                    w in {"cover", "explain", "compare", "comparison", "tutorial", "discuss", "show", "teach", "promise", "promised", "promising"}
                    for w in words
                ):
                    continue
                first = max(0, found - 8) if len(words) > 40 or len(sentence.text) > 280 else 0
                result.add(sentence, limit, first, label=text)
                break
    return result
