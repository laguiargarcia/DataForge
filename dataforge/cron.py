"""Cron expressions between the time zone DataForge stores them in (UTC) and the one the OS
schedulers fire them in and the GUI shows them in (local). Pure, without Qt or I/O, so the CLI
and the GUI share one implementation."""
from __future__ import annotations

from datetime import timedelta

# Fields of a standard cron expression: minute, hour, day of month, month, day of week.
CRON_FIELDS = 5

_DAY_MINUTES = 24 * 60
_WEEK_DAYS = 7
_DOW_NAMES = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}


def _dow_number(token: str) -> int:
    """0-7 for a day-of-week digit (7 is Sunday again) or 0-6 for a 3-letter name; KeyError for
    anything else."""
    if token.isdigit() and int(token) <= _WEEK_DAYS:
        return int(token)
    return _DOW_NAMES[token.lower()]


def _expand_dow(field: str) -> set[int]:
    """The days (0 = Sunday) a day-of-week field names: lists of '*', a day, or a range 'a-b',
    each with an optional '/step'. Raises KeyError or ValueError for any other form ('a/step'
    reads differently across cron implementations, so it is not guessed)."""
    days: set[int] = set()
    for part in field.split(","):
        span, has_step, step = part.partition("/")
        if span == "*":
            first, last = 0, _WEEK_DAYS - 1
        elif "-" in span:
            low, high = span.split("-", 1)
            first, last = _dow_number(low), _dow_number(high)
        elif has_step:
            raise ValueError(f"day-of-week step without a range: {part!r}")
        else:
            first = last = _dow_number(span)
        days.update(day % _WEEK_DAYS for day in range(first, last + 1, int(step) if has_step else 1))
    return days


def _shift_dow(dow: str, day_shift: int) -> str:
    """*dow* moved by *day_shift* days, as a sorted list of digits; unchanged when no day moves or
    the field is not a form _expand_dow reads (as before df-vo3x.5, when only a digit moved)."""
    if day_shift == 0 or dow == "*":
        return dow
    try:
        days = _expand_dow(dow)
    except (KeyError, ValueError):
        return dow
    if not days:          # a reversed range ('5-1') names no day: keep it as written
        return dow
    return ",".join(str(day) for day in sorted((day + day_shift) % _WEEK_DAYS for day in days))


def shift_cron(cron: str, offset: timedelta) -> str:
    """*cron* moved by *offset*: a plain-integer minute and hour are shifted, and when that crosses
    midnight a restricted day of week moves with them (df-vo3x.5). Anything else passes through:
    a cron without 5 fields, a minute or hour that is not a plain integer (no single shifted
    form), and the day of month, whose wraparound depends on the month."""
    parts = cron.strip().split()
    if len(parts) != CRON_FIELDS:
        return cron
    minute, hour, dom, month, dow = parts
    if not (minute.isdigit() and hour.isdigit()):
        return cron
    total = int(hour) * 60 + int(minute) + int(offset.total_seconds() // 60)
    day_shift, total = divmod(total, _DAY_MINUTES)
    new_hour, new_minute = divmod(total, 60)
    return f"{new_minute} {new_hour} {dom} {month} {_shift_dow(dow, day_shift)}"
