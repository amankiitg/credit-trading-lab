"""Tests for execution/calendar_utils.py (E11: NYSE calendar check)."""

from __future__ import annotations

import pytest

from execution.calendar_utils import is_trading_day


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
