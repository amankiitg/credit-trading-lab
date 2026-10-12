"""NYSE trading calendar utilities for v8.6 cron jobs.

Both cron scripts (run_signal.py and run_execution.py) call is_trading_day
before doing any work, then check_already_ran before executing, and call
write_cron_run at the END of a successful run only so partial runs retry.

It also holds the execution window, which is the daylight-saving guard: the execution
cron fires twice a day in UTC and this decides which firing is the real one.
"""

from __future__ import annotations

import logging
from datetime import datetime, time

logger = logging.getLogger(__name__)

_NYSE_CALENDAR = None
_NEW_YORK = None

# ------------------------------------------------ the daylight-saving execution window
#
# UTC cannot express "10:31 in New York". A fixed UTC schedule is an hour out for half
# the year, and on the winter side that hour puts the market orders into the 09:30 ET
# opening auction, which is exactly what this window exists to prevent.
#
# So the schedule carries the redundancy (render.yaml: "31 14,15 * * 1-5") and this
# decides which firing is real: 14:31 UTC is 10:31 EDT but 09:31 EST, while 15:31 UTC is
# 11:31 EDT but 10:31 EST. Exactly one of the two falls inside the window below, every
# day of the year, so the book trades at the same New York time in both seasons.
#
# The window is 25 minutes wide rather than an instant because Render firings arrive a
# few seconds late and a late firing must still be recognised; it also means a firing
# more than about 14 minutes late is skipped rather than executed, which is the safe
# direction to fail and is visible as a day with no summary email at all.
#
# US daylight-saving transitions happen on a Sunday, so no weekday firing lands on one.
EXECUTION_WINDOW_START_ET: time = time(10, 20)
EXECUTION_WINDOW_END_ET: time = time(10, 45)


def _new_york():
    """The New York zone, loaded once.

    Lazy and cached like the calendar above, so a runtime with no tz database cannot
    break the import of this module for both cron jobs.
    """
    global _NEW_YORK
    if _NEW_YORK is None:
        from zoneinfo import ZoneInfo
        _NEW_YORK = ZoneInfo("America/New_York")
    return _NEW_YORK


def new_york_now() -> datetime:
    """The current time in New York, daylight-saving aware."""
    return datetime.now(_new_york())


def in_execution_window(now_et: datetime) -> bool:
    """True when the New York clock is inside the execution window.

    Takes the moment rather than reading the clock, so both firings can be checked on
    both sides of a daylight-saving change without waiting for one. `now_et` must
    already be in New York time: the comparison is on wall-clock time, so a moment in
    another zone would compare the wrong hour.
    """
    return EXECUTION_WINDOW_START_ET <= now_et.time() <= EXECUTION_WINDOW_END_ET


def _get_nyse() -> object:
    global _NYSE_CALENDAR
    if _NYSE_CALENDAR is None:
        import exchange_calendars as ec
        _NYSE_CALENDAR = ec.get_calendar("XNYS")
    return _NYSE_CALENDAR


def is_trading_day(date_str: str) -> bool:
    """Return True if date_str (YYYY-MM-DD) is a NYSE regular or early-close session."""
    try:
        cal = _get_nyse()
        return bool(cal.is_session(date_str))
    except Exception as exc:
        logger.warning(
            "is_trading_day(%s) lookup failed, defaulting to True: %s", date_str, exc
        )
        return True  # fail open so we don't silently skip live days


def trading_days_elapsed(from_date: str, to_date: str) -> int:
    """NYSE sessions in the half-open interval (from_date, to_date].

    This is "how many sessions old is this signal", which is what a staleness
    guard needs. Counting sessions rather than calendar days is the point: a
    Friday signal used on Monday is one session old, not three.

    Returns 0 when the two dates are the same session. Raises ValueError if
    either date is not parseable. A calendar lookup failure raises too, because
    unlike `is_trading_day` this feeds a guard that decides whether real money
    trades, and silently guessing here is the wrong failure mode.
    """
    import pandas as pd

    cal = _get_nyse()
    start = pd.Timestamp(from_date)
    end = pd.Timestamp(to_date)
    if end <= start:
        return 0
    # sessions_in_range is inclusive at both ends.
    sessions = cal.sessions_in_range(start, end)
    return max(0, len(sessions) - 1)


def check_already_ran(job_name: str, run_date: str) -> bool:
    """Return True if job_name already completed for run_date (idempotency guard).

    Reads from the Supabase cron_runs table. Returns False if Supabase is
    unreachable so the job retries rather than silently skipping.
    """
    from dashboard.supabase_client import check_cron_run
    return check_cron_run(job_name, run_date)


def record_run(job_name: str, run_date: str) -> None:
    """Write a completed-run marker to Supabase. Called only on success."""
    from dashboard.supabase_client import write_cron_run
    ok = write_cron_run(job_name, run_date)
    if not ok:
        logger.warning(
            "record_run: failed to write cron_runs for %s/%s -- "
            "next run will retry (not idempotent until this write succeeds)",
            job_name, run_date,
        )
