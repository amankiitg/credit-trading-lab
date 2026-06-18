"""NYSE trading calendar utilities for v8.6 cron jobs.

Both cron scripts (run_signal.py and run_execution.py) call is_trading_day
before doing any work, then check_already_ran before executing, and call
write_cron_run at the END of a successful run only so partial runs retry.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_NYSE_CALENDAR = None


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
