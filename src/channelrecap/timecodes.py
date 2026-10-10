"""Parse source time codes and format whole seconds."""
import math
import re
from .errors import InputError


def parse_time(value: str) -> float:
    parts = value.strip().split(":")
    if not 1 <= len(parts) <= 3 or any(not re.fullmatch(r"[0-9]+", p) for p in parts[:-1]) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", parts[-1]):
        raise InputError(f"Invalid time code: {value!r}")
    try:
        numbers = [float(p) for p in parts]
        result = 0.0
        for number in numbers:
            result = result * 60 + number
    except (ValueError, OverflowError):
        raise InputError(f"Invalid time code: {value!r}") from None
    if not math.isfinite(result) or (len(parts) > 1 and numbers[-1] >= 60) or (len(parts) == 3 and numbers[1] >= 60):
        raise InputError(f"Invalid time code: {value!r}")
    return result


def format_time(seconds: float) -> str:
    total = math.floor(seconds)
    hours, rest = divmod(total, 3600)
    minutes, sec = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{sec:02d}"


def covered(start: float | None, end: float | None) -> str:
    if start is not None and end is not None:
        return f"{format_time(start)} to {format_time(end)}"
    if start is not None:
        return f"from {format_time(start)}"
    if end is not None:
        return f"up to {format_time(end)}"
    return "whole video"
