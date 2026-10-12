"""The daylight-saving guard on the execution cron.

The cron fires twice a day in UTC because UTC cannot express "10:31 in New York": the
14:31 UTC firing is 10:31 in summer and 09:31 in winter, and 15:31 UTC is 11:31 in
summer and 10:31 in winter. Exactly one of the two is inside the New York window, so the
book trades at the same local time all year instead of moving an hour on the change.

`in_execution_window` takes the moment rather than reading the clock, which is what lets
both firings be checked on both sides of a change without waiting for one. Patching the
clock is also the only honest way to test the guard: a test that read the real clock
would pass or fail depending on the hour it ran at.

The off-hour firing has to be genuinely inert: exiting 0 after doing nothing, with no
email, no cron_runs row and no writes. That is asserted by proving it never reaches
execute_job, which is the only path to any of those three.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import execution.calendar_utils as cal
import scripts.run_execution as run_mod
from execution.calendar_utils import (
    EXECUTION_WINDOW_END_ET,
    EXECUTION_WINDOW_START_ET,
    in_execution_window,
)

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# One weekday in each season. DST transitions are on Sundays, so a weekday firing is
# never on one; what changes is the offset the firings are read under.
SUMMER_DAY = "2026-07-15"   # EDT, UTC-4
WINTER_DAY = "2026-01-14"   # EST, UTC-5


def _firing(utc_day: str, utc_hour: str) -> datetime:
    """A cron firing as a New York datetime. This is what the job would see."""
    return datetime.fromisoformat(f"{utc_day}T{utc_hour}:31:00").replace(
        tzinfo=UTC
    ).astimezone(NY)


def _et(month_day: str, hour: int, minute: int = 31) -> datetime:
    """A New York wall-clock moment in 2026, e.g. _et("07-15", 10, 31)."""
    month, day = (int(part) for part in month_day.split("-"))
    return datetime(2026, month, day, hour, minute, tzinfo=NY)


# ------------------------------------------------------------------ the clock itself


def test_new_york_now_is_an_aware_new_york_time() -> None:
    """The zone has to resolve, and to resolve to Eastern, or the window means nothing.

    A tz database that fell back to UTC would put 10:31 ET at 10:31 UTC, which is 06:31
    in New York: the guard would then pass on neither firing and the book would never
    trade.
    """
    now = cal.new_york_now()

    assert now.tzinfo is not None
    assert now.utcoffset() in (timedelta(hours=-4), timedelta(hours=-5))


# ------------------------------------------------------------------ both firings


@pytest.mark.parametrize(
    "utc_day,offset_hours,running_firing",
    [(SUMMER_DAY, -4, "14"), (WINTER_DAY, -5, "15")],
)
def test_exactly_one_firing_lands_in_the_window(
    utc_day: str, offset_hours: int, running_firing: str
) -> None:
    """Both firings, on both sides of the clock change, one of them in the window."""
    firings = {hour: _firing(utc_day, hour) for hour in ("14", "15")}

    assert {f.utcoffset() for f in firings.values()} == {timedelta(hours=offset_hours)}

    inside = [hour for hour, moment in firings.items() if in_execution_window(moment)]
    assert inside == [running_firing], (
        f"expected only the {running_firing}:31 UTC firing to be in the window"
    )

    # The firing that runs is 10:31 in New York in both seasons, which is the whole point.
    assert firings[running_firing].strftime("%H:%M") == "10:31"

    # And it is the same New York calendar day, so the weekday cron line and the date
    # the job works on do not disagree.
    assert firings[running_firing].date().isoformat() == utc_day


@pytest.mark.parametrize(
    "utc_day,off_hour",
    [(SUMMER_DAY, "15"), (WINTER_DAY, "14")],
)
def test_the_other_firing_is_an_hour_off_the_new_york_morning(
    utc_day: str, off_hour: str
) -> None:
    """Summer's second firing is 11:31 ET; winter's first is 09:31 ET, inside the open."""
    off = _firing(utc_day, off_hour)

    assert not in_execution_window(off)
    assert off.strftime("%H:%M") in ("09:31", "11:31")


def test_the_guard_reads_the_new_york_clock_not_utc() -> None:
    """10:31 local is in the window in both seasons, though the UTC instants differ.

    Summer's 10:31 ET is 14:31 UTC and winter's is 15:31 UTC. A guard built on UTC would
    have to know which season it was in; a guard on the New York clock does not.
    """
    summer = _et("07-15", 10)
    winter = _et("01-14", 10)

    assert summer.astimezone(UTC).strftime("%H:%M") == "14:31"
    assert winter.astimezone(UTC).strftime("%H:%M") == "15:31"
    assert in_execution_window(summer) and in_execution_window(winter)


# ------------------------------------------------------------------ the window edges


@pytest.mark.parametrize(
    "hhmm,inside",
    [
        ("10:19", False),
        ("10:20", True),   # the start, inclusive
        ("10:31", True),   # the intended firing
        ("10:44", True),
        ("10:45", True),   # the end, inclusive
        ("10:46", False),
    ],
)
def test_the_window_edges(hhmm: str, inside: bool) -> None:
    """A firing a few minutes late is still the firing; one outside the window is not."""
    hour, minute = (int(part) for part in hhmm.split(":"))

    assert in_execution_window(_et("07-15", hour, minute)) is inside


def test_the_window_is_wide_enough_for_a_late_firing() -> None:
    """Render firings arrive seconds late; the window has to absorb that."""
    schedule_slippage = timedelta(minutes=14)
    late = _et("07-15", 10, 31) + schedule_slippage

    assert late.strftime("%H:%M") == "10:45"
    assert in_execution_window(late)
    assert not in_execution_window(late + timedelta(minutes=1))


def test_the_window_bounds_are_the_documented_ones() -> None:
    """Pinned so a later edit cannot quietly move the firing window."""
    assert EXECUTION_WINDOW_START_ET.strftime("%H:%M") == "10:20"
    assert EXECUTION_WINDOW_END_ET.strftime("%H:%M") == "10:45"


# ------------------------------------------------------------------ the job itself


def _pin_clock(monkeypatch, moment: datetime) -> None:
    monkeypatch.setattr(cal, "new_york_now", lambda: moment)


def test_the_off_hour_firing_does_nothing_at_all(
    monkeypatch, no_real_email, caplog
) -> None:
    """Exit 0, one log line, and no way into the email or the database.

    execute_job is the only path to a summary email, a cron_runs row or any Supabase
    write, so proving it is never reached proves all three. The exit code is 0 rather
    than a failure because nothing is wrong: this firing simply is not the one that does
    the work, and a non-zero exit would have the scheduler retry it.
    """
    import execution.daily_summary as ds

    _pin_clock(monkeypatch, _et("07-15", 11))  # summer's 15:31 UTC firing

    reached: list = []
    monkeypatch.setattr(ds, "execute_job", lambda **kwargs: reached.append(kwargs) or 0)

    with caplog.at_level(logging.INFO, logger="run_execution"):
        code = run_mod.main(max_secs=120)

    assert code == 0
    assert reached == [], "the off-hour firing must not enter the job"
    assert no_real_email == [], "and must not send a summary email"

    lines = [r.getMessage() for r in caplog.records if r.name == "run_execution"]
    assert len(lines) == 1, f"exactly one log line, got {lines}"
    assert "off-hour daylight-saving firing" in lines[0]
    assert "11:31:00" in lines[0], "the line names the local time it fired at"
    assert "10:20-10:45 ET" in lines[0], "and the window it was outside of"


def test_the_winter_off_hour_firing_does_nothing_too(
    monkeypatch, no_real_email, caplog
) -> None:
    """The other side of the change: winter's 14:31 UTC firing is 09:31 ET."""
    import execution.daily_summary as ds

    _pin_clock(monkeypatch, _et("01-14", 9))  # inside the opening auction

    reached: list = []
    monkeypatch.setattr(ds, "execute_job", lambda **kwargs: reached.append(kwargs) or 0)

    with caplog.at_level(logging.INFO, logger="run_execution"):
        code = run_mod.main(max_secs=120)

    assert code == 0
    assert reached == []
    assert no_real_email == []
    assert "09:31:00" in "\n".join(
        r.getMessage() for r in caplog.records if r.name == "run_execution"
    )


def test_the_in_window_firing_enters_the_job(monkeypatch) -> None:
    """The negative control: the firing that should run does reach execute_job."""
    import execution.daily_summary as ds

    _pin_clock(monkeypatch, _et("07-15", 10))  # the real firing

    reached: list = []
    monkeypatch.setattr(ds, "execute_job", lambda **kwargs: reached.append(kwargs) or 0)

    code = run_mod.main(max_secs=120)

    assert code == 0
    assert len(reached) == 1, "the in-window firing runs the job"
    assert reached[0]["job"] == "run_execution"


def test_a_skip_after_the_guard_still_emails(monkeypatch, no_real_email) -> None:
    """The contrast that makes the silence above mean something.

    A closed market is a normal skip and it does send one email, so an inbox with no
    execution email means the guard fired or the run was lost, not that skips are quiet.
    """
    import execution.alpaca_paper as ap
    from datetime import date as _date

    _pin_clock(monkeypatch, _et("07-15", 10))
    monkeypatch.setattr(ap, "DRY_RUN_DEFAULT", False)
    monkeypatch.setattr(cal, "is_trading_day", lambda d: False)

    code = run_mod.main(max_secs=120)

    assert code == 0
    assert len(no_real_email) == 1
    assert no_real_email[0]["subject"] == f"[SKIP] run_execution {_date.today().isoformat()}"
    assert "NYSE closed" in no_real_email[0]["body"]
