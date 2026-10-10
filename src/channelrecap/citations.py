"""Parse a closed report grammar and verify every evidence span without a model."""
from collections import Counter
import json
from pathlib import Path
import re
import sys
import unicodedata
from .errors import InputError, validate_text
from .pipeline import load_library, library_path
from .timecodes import parse_time, format_time
from .words import split_words

TITLE = '# Video analysis'
SECTIONS = (
    '### 1. Topics and trends',
    '### 2. Questions raised but not answered',
    '### 3. Thin explanations',
    '### 4. New video ideas',
    '### 5. Speaking habits',
    '### 6. What I could not determine',
)
CLOCK = r'[0-9]{2,}:[0-5][0-9]:[0-5][0-9]'
QUOTED = r'"(?:[^"\\' + chr(0) + '-' + chr(31) + r']|\\["\\])+"'
CITATION_PATTERN = re.compile(
    r'\[(?P<id>[a-z0-9]+(?:-[a-z0-9]+)*) @ (?P<time>' + CLOCK +
    r')\.\.(?P<end>' + CLOCK + r') \| (?P<quote>' + QUOTED + r')\]')
# A small, explicit alphabet for claim prose. References, links, quotes, Markdown,
# invisible characters and compatibility punctuation have no alternate production.
PROSE_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9 .,;:!?%\-]*[A-Za-z0-9.!?%]|[A-Za-z0-9]')
PARAGRAPH_PATTERN = re.compile(r'^\[(' + CLOCK + r')\] (.+)$')
STATUSES = ('ok', 'wrong-time', 'not-found', 'too-short', 'unknown-video', 'malformed')


def read_paragraphs(path: Path) -> list[tuple[float, str]]:
    try:
        text = path.read_text(encoding='utf-8')
        validate_text(text, 'Transcript')
    except (OSError, UnicodeError) as exc:
        raise InputError(f'Cannot read transcript {path.name}: {exc}') from None
    marker = 'Timestamps show the time in the original video.\n\n'
    if marker not in text:
        raise InputError(f'Transcript {path.name} has no body marker.')
    paragraphs = []
    for line in text.split(marker, 1)[1].split('\n'):
        if not line:
            continue
        match = PARAGRAPH_PATTERN.fullmatch(line)
        if not match:
            raise InputError(f'Transcript {path.name} has an invalid paragraph.')
        paragraphs.append((parse_time(match[1]), match[2]))
    return paragraphs


def evaluate(quote: str, time: float, paragraphs: list[tuple[float, str]],
             end: float | None = None) -> tuple[str, float | None]:
    """Match exact characters and complete words, including adjacent paragraphs."""
    if len(split_words(quote)) < 4:
        return 'too-short', None
    whole = ' '.join(text for _, text in paragraphs)
    spans = []
    offset = 0
    for stamp, text in paragraphs:
        spans.append((offset, offset + len(text), stamp))
        offset += len(text) + 1
    first_found = None
    def word_character(char: str) -> bool:
        return unicodedata.category(char)[0] in 'LMN' or char in "_'’"
    for match in re.finditer(re.escape(quote), whole):
        if (word_character(quote[0]) and match.start() and word_character(whole[match.start() - 1])) or (
                word_character(quote[-1]) and match.end() < len(whole) and word_character(whole[match.end()])):
            continue
        first = next((stamp for start, stop, stamp in spans if start <= match.start() < stop), None)
        last = next((stamp for start, stop, stamp in spans if start <= match.end() - 1 < stop), None)
        if first is not None and last is not None:
            if first == time and last == (time if end is None else end):
                return 'ok', None
            if first_found is None:
                first_found = first
    return ('wrong-time', first_found) if first_found is not None else ('not-found', None)


def parse_claim(line: str) -> list[re.Match[str]]:
    if not line.startswith('- ') or ' | ' not in line:
        raise ValueError('use - claim | [video-id @ START..END | "exact quote"]')
    claim, tail = line[2:].split(' | ', 1)
    if not PROSE_PATTERN.fullmatch(claim):
        raise ValueError('claim prose allows ASCII letters, digits, spaces and . , ; : ! ? % - only')
    citations = []
    while tail:
        match = CITATION_PATTERN.match(tail)
        if not match:
            raise ValueError('invalid canonical citation; links and alternate reference forms are not allowed')
        quote = json.loads(match['quote'])
        if not quote or json.dumps(quote, ensure_ascii=False) != match['quote']:
            raise ValueError('copy the quote exactly; escape only double quotes and backslashes')
        citations.append(match)
        tail = tail[match.end():]
        if tail:
            if not tail.startswith(' ['):
                raise ValueError('separate canonical citations with one space; no trailing material')
            tail = tail[1:]
    if not citations:
        raise ValueError('each claim needs a canonical citation')
    return citations


def check(report: Path, out_dir: Path) -> int:
    library = load_library(out_dir, required=True)
    entries = {e['video_id']: e for e in library['videos'] if e['status'] == 'done'}
    try:
        text = report.read_text(encoding='utf-8')
        validate_text(text, 'Report')
    except (OSError, UnicodeError) as exc:
        raise InputError(f'Cannot read report: {exc}') from None
    lines = text.split('\n')
    results: Counter[str] = Counter()
    cache: dict[str, list[tuple[float, str]]] = {}
    section = -1
    title_seen = False

    def malformed(number: int, message: str) -> None:
        results['malformed'] += 1
        print(f'malformed  line {number}: {message}', file=sys.stderr)

    for number, line in enumerate(lines, 1):
        if not line:
            continue
        if not title_seen:
            if line == TITLE:
                title_seen = True
            else:
                malformed(number, f'expected {TITLE}; use the report template in AGENT_PROMPT.md')
            continue
        if section + 1 < len(SECTIONS) and line == SECTIONS[section + 1]:
            section += 1
            continue
        if line.startswith('#'):
            expected = SECTIONS[section + 1] if section + 1 < len(SECTIONS) else SECTIONS[-1]
            malformed(number, f'expected level-3 heading (###), exactly: {expected}; use all six headings in order')
            continue
        # Free prose is explicitly outside the findings block. It cannot introduce
        # headings, bullets, references, quotations or additional finding sections.
        if section in (-1, 5) and line.startswith('Note: ') and PROSE_PATTERN.fullmatch(line[6:]):
            continue
        if not 0 <= section <= 4:
            malformed(number, 'outside findings, use a plain Note: line or the next exact section heading')
            continue
        try:
            matches = parse_claim(line)
        except (ValueError, TypeError) as exc:
            malformed(number, str(exc))
            continue
        for match in matches:
            identity, stamp, end = match['id'], match['time'], match['end']
            found = None
            if identity not in entries:
                status = 'unknown-video'
            else:
                if identity not in cache:
                    cache[identity] = read_paragraphs(library_path(out_dir, entries[identity]['transcript']))
                status, found = evaluate(json.loads(match['quote']), parse_time(stamp), cache[identity], parse_time(end))
            results[status] += 1
            suffix = f'  found at {format_time(found)}' if found is not None else ''
            print(f'{status}  {identity} @ {stamp}..{end}  line {number}{suffix}', file=sys.stdout if status == 'ok' else sys.stderr)
    if section != 5:
        malformed(len(lines), 'missing required section headings; use all six in order')
    if not results['ok']:
        print('No verified citations found.', file=sys.stderr)
    print('citations ' + ' '.join(f'{status}={results[status]}' for status in STATUSES))
    return 0 if results['ok'] and sum(results.values()) == results['ok'] else 1
