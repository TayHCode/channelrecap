"""Longest-first filler matching with clear and ambiguous totals."""
from collections import Counter
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from .errors import InputError, validate_text
from .signals import rate
from .speech import speech_status
from .timecodes import format_time
from .transcript import Transcript
from .words import package_text, split_words, spoken_words


@dataclass(frozen=True)
class Entry:
    text: str
    words: tuple[str, ...]
    ambiguous: bool = False
    sentence_start_only: bool = False


@dataclass
class Counts:
    words: int
    counts: Counter[str]
    clear: int
    all: int
    busiest: str
    words_per_minute: int

    @property
    def clear_rate(self) -> float:
        return rate(self.clear, self.words)

    @property
    def all_rate(self) -> float:
        return rate(self.all, self.words)

    def top(self, limit: int = 5) -> str:
        return ", ".join(f"{name} ({count})" for name,count in sorted(self.counts.items(), key=lambda x: (-x[1],x[0]))[:limit])


def parse_entries(text: str) -> list[Entry]:
    validate_text(text, "Filler list")
    entries: list[Entry] = []
    seen: set[str] = set()
    for line_number, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        ambiguous = sentence_start = False
        while line and line[0] in "~^":
            ambiguous = ambiguous or line[0] == "~"
            sentence_start = sentence_start or line[0] == "^"
            line = line[1:].lstrip()
        words = tuple(split_words(line))
        normal = " ".join(words)
        if not words:
            raise InputError(f"Empty filler entry on line {line_number}.")
        if normal in seen:
            raise InputError(f"Duplicate filler entry on line {line_number}.")
        seen.add(normal)
        entries.append(Entry(normal, words, ambiguous, sentence_start))
    if not entries:
        raise InputError("Filler list is empty.")
    return sorted(entries, key=lambda entry: (-len(entry.words), entry.text))


def load_entries(path: Path | None = None) -> list[Entry]:
    try:
        return parse_entries(path.read_text(encoding="utf-8") if path else package_text("fillers_en.txt"))
    except (OSError, UnicodeError) as exc:
        raise InputError(f"Cannot read filler list: {exc}") from None


def count_fillers(transcript: Transcript, entries: list[Entry]) -> Counts:
    words = spoken_words(transcript.segments)
    text = [w.text for w in words]
    counts: Counter[str] = Counter()
    minutes: Counter[int] = Counter()
    index = 0
    ordered = sorted(entries, key=lambda entry: (-len(entry.words), entry.text))
    while index < len(words):
        for entry in ordered:
            if entry.sentence_start_only and not words[index].sentence_start:
                continue
            if tuple(text[index:index + len(entry.words)]) == entry.words:
                counts[entry.text] += 1
                minutes[int(words[index].start // 60)] += 1
                index += len(entry.words)
                break
        else:
            index += 1
    clear = sum(counts[e.text] for e in entries if not e.ambiguous)
    busiest = min(minutes, key=lambda minute: (-minutes[minute],minute)) if minutes else None
    return Counts(len(words), counts, clear, sum(counts.values()),
                  f"{format_time(busiest * 60)} ({minutes[busiest]})" if busiest is not None else "",
                  round(len(words) * 60 / transcript.duration) if transcript.duration else 0)


def csv_text(value: str) -> str:
    """Keep spreadsheet applications from evaluating user-supplied text."""
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')) else value


def write_csv(out_dir: Path, items: list[tuple[dict[str, Any], Transcript]], entries: list[Entry], stats: list[Counts]) -> None:
    mapping = {e.text: e for e in entries}
    with (out_dir / "fillers.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["video_id", "date", "title", "words", "filler", "ambiguous", "sentence_start_only", "count", "per_1000_words"])
        for (entry, transcript), result in zip(items, stats):
            usable = speech_status(transcript) == "usable"
            prefix = [csv_text(entry["video_id"]), csv_text(entry["date"]), csv_text(entry["title"]), result.words]
            writer.writerow(prefix + ["TOTAL_CLEAR", "", "", result.clear, f"{result.clear_rate:.1f}" if usable else ""])
            writer.writerow(prefix + ["TOTAL_ALL", "", "", result.all, f"{result.all_rate:.1f}" if usable else ""])
            for text, count in sorted(result.counts.items(), key=lambda x: (-x[1],x[0])):
                filler = mapping[text]
                writer.writerow(prefix + [text, "yes" if filler.ambiguous else "no", "yes" if filler.sentence_start_only else "no", count, f"{rate(count,result.words):.1f}" if usable else ""])


def report_fillers(out_dir: Path, path: Path | None = None) -> int:
    from .pipeline import finished
    entries = load_entries(path)
    _, items = finished(out_dir)
    stats = [count_fillers(t, entries) for _,t in items]
    write_csv(out_dir,items,entries,stats)
    for (entry, transcript), result in zip(items, stats):
        if speech_status(transcript) != "usable":
            print(f"{entry['video_id']} excluded from rates and trends; transcript retained")
            continue
        print(f"{entry['video_id']}  {result.words}  {result.clear}  {result.clear_rate:.1f}  {result.all}  {result.all_rate:.1f}  {result.top(3)}")
    return len(items)
