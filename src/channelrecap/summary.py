"""Stable Markdown tables and file preparation."""
from collections import Counter
from pathlib import Path
from typing import Any
from . import NAME, __version__
from .pipeline import finished
from .speech import speech_status, speech_notice
from .transcript import atomic_json
from .agentfiles import write_agent_files
from .fillers import Entry, load_entries, count_fillers, write_csv
from .signals import questions, deferrals, frequent, rate, recurring, term_counts, trend_label, unique_terms
from .timecodes import covered, format_time
from .transcript import Transcript
from .errors import display_source, safe_error


def cell(value: Any) -> str:
    return " ".join(str(value).splitlines()).replace("|", "\\|")


def table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "None found."
    return "\n".join(["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|",
                       *("| " + " | ".join(cell(v) for v in row) + " |" for row in rows)])


def sampled_trend(values: list[float], counts: list[int]) -> str:
    return trend_label(values, counts) + f" ({sum(counts)} matches in {len(counts)} usable videos)"


def compose_summary(library: dict[str, Any], items: list[tuple[dict[str, Any], Transcript]],
           filler_words: set[str] | None = None, filler_entries: list[Entry] | None = None) -> str:
    filler_entries = load_entries() if filler_entries is None else filler_entries
    if filler_words is None:
        filler_words = {w for e in filler_entries for w in e.words}
    entries = [e for e, _ in items]
    usable = [(e, t) for e, t in items if speech_status(t) == "usable"]
    usable_entries = [e for e, _ in usable]
    counts = [term_counts(t, filler_words) for _, t in usable]
    order = "by date" if all(e["date"] for e in entries) else "as listed (some videos have no date)"
    sections = ["# Video library summary",
        f"Made by {NAME} {__version__} from {len(items)} videos ({sum(e['words'] for e in entries)} words in total). Order: {order}.",
        'This file holds counts and excerpts made by a program. No AI model wrote it. The "Hints"\nsections show where to look; they are not conclusions. Quote only from the files in\n`transcripts/`. To analyse these videos, follow `AGENT_PROMPT.md`.',
        "Word and filler counts are estimates from the transcript. They can differ slightly across machines and models.",
        "Rates and trends exclude videos with no speech or fewer than 20 words. Rate series list usable videos only: " +
        (", ".join(e["video_id"] for e in usable_entries) or "none") + ".",
        "## Videos", table(["#", "Video id", "Title", "Date", "Covered", "Minutes", "Words", "Words/min", "Transcript", "Speech status"],
        [[n, e["video_id"], e["title"], e["date"], covered(e["start"], e["end"]), f"{e['duration'] / 60:.1f}", e["words"],
          (round(e["words"] * 60 / e["duration"]) if e["duration"] else 0) if speech_status(t) == "usable" else "", e["transcript"], speech_status(t)] for n, (e, t) in enumerate(items, 1)]),
        "## Hints: topics", "### Recurring terms (in two or more videos)"]
    rows = []
    for term in recurring(counts):
        values = [rate(c[term], e["words"]) for c, e in zip(counts, usable_entries)]
        rows.append([term, sum(c[term] >= 2 for c in counts), ", ".join(f"{v:.1f}" for v in values), sampled_trend(values, [c[term] for c in counts])])
    sections.extend([table(["Term", "Videos", "Per 1,000 words, in video order", "Trend"], rows), "### Frequent terms per video"])
    sections.append("\n".join(f"- **{e['video_id']}**: " + (", ".join(f"{term} ({n})" for term,n in frequent(c)) or "None found.") for e,c in zip(usable_entries, counts)) or "None found.")
    if len(items) > 1:
        sections.extend(["### Terms used in one video only", "\n".join(f"- **{e['video_id']}**: " + (", ".join(f"{term} ({n})" for term,n in unique_terms(counts, i)) or "None found.") for i,e in enumerate(usable_entries))])
    sections.append("## Hints: gaps")
    for heading, function in (("Questions asked on camera", questions), ("Promised for later or skipped", deferrals)):
        sections.append("### " + heading)
        found_any = False
        for entry, transcript in items:
            hints = function(transcript)
            if not hints.items:
                continue
            found_any = True
            sections.append("#### " + entry["video_id"])
            lines = []
            for hint in hints.items:
                quote = hint[1].replace('"', "'")
                suffix = (f" ({hint[2]})" if function is questions else f" (matched: {hint[2]})") if len(hint) == 3 else ""
                lines.append(f'- [{format_time(hint[0])}] "{quote}"{suffix}')
            if hints.omitted:
                lines.append(f"(+{hints.omitted} more)")
            sections.append("\n".join(lines))
        if not found_any:
            sections.append("None found.")
    stats = [count_fillers(t, filler_entries) for _,t in usable]
    sections.extend(["## Filler words", table(["Video id", "Words", "Clear fillers", "Per 1,000", "All candidates", "Per 1,000", "Top fillers", "Busiest minute"],
        [[e["video_id"], s.words, s.clear, f"{s.clear_rate:.1f}", s.all, f"{s.all_rate:.1f}", s.top(), s.busiest] for e,s in zip(usable_entries,stats)]),
        "Trend, clear fillers per 1,000 words: " + sampled_trend([s.clear_rate for s in stats], [s.clear for s in stats]) + "\n" +
        "Trend, all candidates per 1,000 words: " + sampled_trend([s.all_rate for s in stats], [s.all for s in stats])])
    totals: Counter[str] = Counter()
    for stat in stats:
        totals.update(stat.counts)
    filler_rows = []
    for name, _ in sorted(totals.items(), key=lambda x: (-x[1],x[0]))[:10]:
        values = [rate(s.counts[name],s.words) for s in stats]
        filler_rows.append([name, ", ".join(f"{v:.1f}" for v in values), sampled_trend(values, [s.counts[name] for s in stats])])
    sections.extend([table(["Filler", "Per 1,000 words, in video order", "Trend"],filler_rows),
        '\"Clear fillers\" leaves out words that are often normal speech, such as \"like\" and \"so\".\n\"All candidates\" counts them every time. Hesitation sounds such as \"um\" are often dropped\nby speech-to-text, so those counts are a lower bound.'])
    excluded = [(e, t) for e, t in items if speech_status(t) != "usable"]
    if excluded:
        sections.extend(["## Retained transcripts excluded from rates and trends", "\n".join(
            "- " + cell(speech_notice(speech_status(t), e['video_id'])) + "; transcript retained."
            for e, t in excluded)])
    failed = [e for e in library["videos"] if e["status"] == "failed"]
    if failed:
        sections.extend(["## Not included", "\n".join(f"- {cell(display_source(e['source']))}: failed: {cell(safe_error(e['error'], e['source']))}" for e in failed)])
    sections.extend(["## Limits of this file", "- Timestamps are the start of the speech segment, in the original video's time.\n- Transcripts are machine-made. Names and technical words may be wrong.\n- Terms match exact words without stemming: grinder and grinders are separate. No understanding of meaning.\n- Trends follow the stated video order and need at least three usable videos, at least 3 matches in every video and 30 total; smaller samples are too few to call a trend.\n- Steady means sufficient counts but equal endpoints or a change below the direction threshold; it does not establish stability. Equal endpoint rates never get a direction. Rising/falling requires a 25% change in both endpoints and first/last half means, with at least two-thirds of adjacent steps agreeing. Labels are descriptive, not statistical significance.\n- Everyone who speaks is counted together."])
    if not items:
        sections.insert(2, "No finished videos are available. Earlier transcript files may remain as evidence, but are excluded from this library's current results. Retry the failed sources before analyzing them.")
    return "\n\n".join(sections) + "\n"


def prepare(out_dir: Path, fillers_file: Path | None = None) -> int:
    filler_entries = load_entries(fillers_file)
    library, items = finished(out_dir)
    for entry, transcript in items:
        entry["speech_status"] = speech_status(transcript)
        entry["trend_usable"] = entry["speech_status"] == "usable"
        entry["notice"] = "" if entry["trend_usable"] else speech_notice(entry["speech_status"], entry["video_id"])
    atomic_json(out_dir / "library.json", library)
    write_csv(out_dir, items, filler_entries, [count_fillers(t, filler_entries) for _,t in items])
    (out_dir / "summary.md").write_text(compose_summary(library, items, filler_entries=filler_entries), encoding="utf-8", newline="\n")
    write_agent_files(out_dir)
    return len(items)
