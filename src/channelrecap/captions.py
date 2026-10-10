"""Small VTT and SRT reader; all times stay in the source timeline."""
from dataclasses import dataclass
from pathlib import Path
import re
from .errors import InputError, RowError, InvalidTextError, validate_text
from .timecodes import parse_time


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


def read_captions(path: Path, *, start: float | None = None,
                  end: float | None = None, source: str | None = None) -> list[Cue]:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf_16" if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else "utf-8-sig")
        text = text.replace('\r\n', '\n').replace('\r', '\n')
    except UnicodeError:
        raise InvalidTextError("Cannot read captions: input must contain valid Unicode text.") from None
    except OSError as exc:
        raise RowError(f"Cannot read captions {source or path.name}: {exc}") from None
    validate_text(text, "Caption text")
    cues: list[Cue] = []
    previous = ""
    text = re.sub(r'^WEBVTT[^\n]*\n?', '', text, count=1)
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.splitlines()
        if not lines or re.match(r"^(WEBVTT|NOTE|STYLE|REGION)(?:\s|$)", lines[0]):
            continue
        for i, line in enumerate(lines):
            match = re.match(r"\s*(\S+)\s+-->\s+(\S+)", line)
            if not match:
                continue
            try:
                first, last = (parse_time(t.replace(",", ".")) for t in match.groups())
            except InputError:
                break
            if last <= first:
                break
            content = re.sub(r"<[^>]*>", "", " ".join(lines[i + 1:]))
            content = re.sub(r"\{\\an[1-9]\}", "", content)
            for entity, replacement in (("&lt;", "<"), ("&gt;", ">"), ("&nbsp;", " "), ("&amp;", "&")):
                content = content.replace(entity, replacement)
            content = " ".join(content.split())
            duplicate = content == previous
            previous = content
            if content and not duplicate and first >= (start or 0) and (end is None or first < end):
                cues.append(Cue(first, last, content))
            break
    if not cues:
        raise RowError(f"No captions found in {source or path.name}.")
    return cues
