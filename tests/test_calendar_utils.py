"""Tests for execution/calendar_utils.py (E11: NYSE calendar check)."""

from __future__ import annotations

import pytest

from execution.calendar_utils import is_trading_day, trading_days_elapsed


def test_trading_day_known_weekday() -> None:
    """A known Wednesday that is not a holiday is a trading day."""
    assert is_trading_day("2026-06-17") is True  # Wednesday


def test_trading_day_weekday_2() -> None:
    """Another known trading day -- regression guard."""
    assert is_trading_day("2026-01-02") is True  # Friday after New Year's Day (2026-01-01 is Thursday = holiday)


def test_non_trading_day_saturday() -> None:
    """Saturdays are never NYSE trading sessions."""
    assert is_trading_day("2026-06-20") is False  # Saturday


def test_non_trading_day_sunday() -> None:
    """Sundays are never NYSE trading sessions."""
    assert is_trading_day("2026-06-21") is False  # Sunday


def test_non_trading_day_christmas() -> None:
    """Christmas Day (Dec 25) is an NYSE holiday."""
    assert is_trading_day("2026-12-25") is False


def test_non_trading_day_new_years() -> None:
    """New Year's Day (Jan 1) is an NYSE holiday."""
    assert is_trading_day("2026-01-01") is False


def test_non_trading_day_independence_day() -> None:
    """July 4 (Independence Day, when on a weekday) is an NYSE holiday."""
    assert is_trading_day("2025-07-04") is False  # Friday


# ------------------------------------------------------------------ v9.4: signal age
#
# The execution cron traded a signal for 2026-09-22 on 2026-09-24, so the staleness
# guard needs the age of a signal in sessions. Counting calendar days would get the
# common case wrong: a Friday signal used on Monday is one session old, not three,
# and trading it is correct.


def test_age_of_the_same_session_is_zero() -> None:
    assert trading_days_elapsed("2026-09-24", "2026-09-24") == 0


def test_age_of_consecutive_sessions_is_one() -> None:
    """Wed 2026-09-23 -> Thu 2026-09-24."""
    assert trading_days_elapsed("2026-09-23", "2026-09-24") == 1


def test_friday_signal_used_on_monday_is_one_session_old() -> None:
    """The case calendar-day counting would get wrong: three days, one session."""
    assert trading_days_elapsed("2026-09-18", "2026-09-21") == 1


def test_two_sessions_old_is_two() -> None:
    """Tue 2026-09-22 -> Thu 2026-09-24, the pair that was actually traded."""
    assert trading_days_elapsed("2026-09-22", "2026-09-24") == 2


def test_a_week_is_five_sessions() -> None:
    assert trading_days_elapsed("2026-09-17", "2026-09-24") == 5


def test_a_holiday_is_not_counted_as_a_session() -> None:
    """Thu 2026-11-26 is Thanksgiving, so Wed 25 -> Fri 27 is one session, not two."""
    assert trading_days_elapsed("2026-11-25", "2026-11-27") == 1


def test_a_future_signal_is_zero_not_negative() -> None:
    """Bad data must not produce a negative age that slips under the limit."""
    assert trading_days_elapsed("2026-09-30", "2026-09-24") == 0


def test_non_session_endpoints_snap_to_the_nearest_session() -> None:
    """exchange_calendars snaps endpoints: start forward, end back.

    So Fri -> Sun is [Fri] = 0 and Sat -> Mon is [Mon] = 0. Both are the honest
    answers: no session has occurred since the signal's session, and no session
    occurred on the signal's date either.

    This is not reachable in production. run_execution checks is_trading_day first
    and exits before the guard on a non-session, and a signal date is always a
    session. It is pinned here so the snapping is deliberate rather than a latent
    surprise if the call order ever changes.
    """
    assert trading_days_elapsed("2026-09-18", "2026-09-20") == 0
    assert trading_days_elapsed("2026-09-19", "2026-09-21") == 0


def test_an_unparseable_date_raises() -> None:
    """Unlike is_trading_day, which fails open, this feeds a guard on real money.

    Guessing an age here would defeat the guard, so it must raise and let the
    caller refuse to trade.
    """
    with pytest.raises(Exception):
        trading_days_elapsed("not-a-date", "2026-09-24")
