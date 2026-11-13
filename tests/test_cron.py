"""dataforge.cron.shift_cron: the UTC <-> local move of a cron shared by the CLI (OS scheduler
registration) and the GUI (display and edit), df-vo3x.5."""
from datetime import timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from dataforge.cron import shift_cron

BRT = timedelta(hours=-3)
WEEK = 7 * 24 * 60


@pytest.mark.parametrize(("cron", "offset", "shifted"), [
    # what already worked: plain minute/hour, a single-digit day of week, pass-through forms
    ("0 9 * * *", BRT, "0 6 * * *"),
    ("30 1 * * *", BRT, "30 22 * * *"),
    ("0 23 * * *", timedelta(hours=2), "0 1 * * *"),
    ("0 1 * * 1", BRT, "0 22 * * 0"),
    ("0 23 * * 6", timedelta(hours=2), "0 1 * * 0"),
    ("15 10 * * 3", timedelta(minutes=-45), "30 9 * * 3"),
    ("0 12 * * 1", timedelta(hours=5, minutes=30), "30 17 * * 1"),
    ("0 9 1 * *", BRT, "0 6 1 * *"),
    ("*/15 * * * *", BRT, "*/15 * * * *"),
    ("0 */2 * * 1", BRT, "0 */2 * * 1"),
    ("0 9 * * * 0", BRT, "0 9 * * * 0"),
    ("@daily", BRT, "@daily"),
    ("0 1 * * 1-5", timedelta(0), "0 1 * * 1-5"),
    # df-vo3x.5: lists, ranges, names and steps move with the day too
    ("0 1 * * 1,3", BRT, "0 22 * * 0,2"),
    ("0 1 * * 1-5", BRT, "0 22 * * 0,1,2,3,4"),
    ("0 1 * * MON", BRT, "0 22 * * 0"),
    ("0 1 * * mon-fri", BRT, "0 22 * * 0,1,2,3,4"),
    ("0 1 * * SUN,sat", BRT, "0 22 * * 5,6"),
    ("0 1 * * 7", BRT, "0 22 * * 6"),
    ("0 1 * * 5-7", BRT, "0 22 * * 4,5,6"),
    ("0 1 * * */2", BRT, "0 22 * * 1,3,5,6"),
    ("0 1 * * 1-5/2", BRT, "0 22 * * 0,2,4"),
    ("0 22 * * 0,2", -BRT, "0 1 * * 1,3"),
    # forms it does not read keep the old pass-through
    ("0 1 * * 1/2", BRT, "0 22 * * 1/2"),
    ("0 1 * * 5-1", BRT, "0 22 * * 5-1"),
    ("0 1 * * 8", BRT, "0 22 * * 8"),
    ("0 1 * * xyz", BRT, "0 22 * * xyz"),
    ("0 1 * * 1-5/0", BRT, "0 22 * * 1-5/0"),
    ("0 1 * * 1#2", BRT, "0 22 * * 1#2"),
    ("0 1 * * *", BRT, "0 22 * * *"),
])
def test_shift_cron(cron, offset, shifted):
    assert shift_cron(cron, offset) == shifted


def _fire_minutes(cron: str) -> set[int]:
    """Minutes of the week (0 = Sunday 00:00) a 'M H * * <digit list>' cron fires at."""
    minute, hour, _, _, dow = cron.split()
    return {int(day) * 24 * 60 + int(hour) * 60 + int(minute) for day in dow.split(",")}


@given(minute=st.integers(0, 59), hour=st.integers(0, 23),
       days=st.sets(st.integers(0, 6), min_size=1),
       offset=st.integers(-14 * 60, 14 * 60))
def test_shifted_cron_fires_at_the_same_instants(minute, hour, days, offset):
    cron = f"{minute} {hour} * * {','.join(map(str, sorted(days)))}"
    shifted = shift_cron(cron, timedelta(minutes=offset))
    assert _fire_minutes(shifted) == {(t + offset) % WEEK for t in _fire_minutes(cron)}
    assert shift_cron(shifted, timedelta(minutes=-offset)) == cron
