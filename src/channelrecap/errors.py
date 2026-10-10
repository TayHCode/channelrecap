"""Expected failures with stable command exit codes."""
from pathlib import Path, PureWindowsPath
import re
from urllib.parse import unquote, urlsplit

class InputError(Exception):
    """Invalid or unreadable input (exit 2)."""
    exit_code = 2

class ToolError(Exception):
    """Missing or unusable optional component (exit 3)."""
    exit_code = 3

class RowError(Exception):
    """One source failed (exit 1)."""
    exit_code = 1


class UnsupportedTypeError(InputError):
    """A source is not an audio, video or caption file (exit 4)."""
    exit_code = 4


class DamagedMediaError(RowError):
    """Unreadable or incomplete media (exit 5)."""
    exit_code = 5


class NoAudioError(RowError):
    """Readable media without a usable audio stream (exit 6)."""
    exit_code = 6


class InvalidTextError(InputError, RowError):
    """Malformed text is an input error, including inside an isolated row."""


def validate_text(value: str, label: str = "Text") -> None:
    if not isinstance(value, str):
        raise InputError(f"{label} must be text.")
    if '\x00' in value:
        raise InvalidTextError(f"{label} contains a NUL byte.")
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise InvalidTextError(f"{label} must contain valid Unicode text.") from None


def validate_strings(value: object) -> None:
    """Validate even retained or unknown JSON metadata, before serialization."""
    if isinstance(value, str):
        validate_text(value, "Metadata")
    elif isinstance(value, dict):
        for key, item in value.items():
            validate_strings(key)
            validate_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            validate_strings(item)


def display_source(source: str) -> str:
    """Keep absolute machine locations out of files intended for sharing."""
    if source.casefold().startswith('file:'):
        try:
            source = unquote(urlsplit(source).path)
        except ValueError:
            return '[local source]'
        return source.replace('\\', '/').rstrip('/').rsplit('/', 1)[-1] or '[local source]'
    if source.startswith('~'):
        source = source.replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]
        if source.startswith('~'):
            return '[local source]'
    elif PureWindowsPath(source).drive or PureWindowsPath(source).root:
        source = PureWindowsPath(source).name
    elif Path(source).is_absolute():
        source = Path(source).name
    return " ".join(source.split())


def safe_error(message: str, *paths: str | Path) -> str:
    # Free-form tool diagnostics have no reliable delimiter for paths with spaces.
    # If path notation occurs, discard the entire detail, not just its first word.
    # This also covers relative, UNC, drive-relative and percent-encoded file URIs.
    if re.search(r"[/\\]|\bfile:|(?<!\w)[a-z]:", message, re.IGNORECASE):
        return "Source processing failed; local path details omitted."
    for path in sorted((str(p) for p in paths if str(p)), key=len, reverse=True):
        message = message.replace(path, display_source(path))
    return " ".join(message.split())[:500]
