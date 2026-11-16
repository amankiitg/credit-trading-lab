"""The share-count line reads public.share_history, a daily series, with GAPS.

The panel used to read the positions table, which only started carrying a shares column
on 2026-09-28 -- so the line was blank before October while the weight above it started
in June. The replacement is a daily share history, and the rule that matters is that a
session it does not cover is UNKNOWN: the line breaks, it is never carried forward and
never zero-filled. A zero says the account held nothing, which is a claim nothing in the
data supports.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dashboard import signal_history


def _closes(periods: int = 300, tickers: tuple[str, ...] = ("SPY", "TLT")) -> pd.DataFrame:
    index = pd.bdate_range("2025-06-02", periods=periods)
    return pd.DataFrame({t: [100.0 + i for i in range(periods)] for t in tickers}, index=index)


def _weights(sessions: pd.DatetimeIndex, ticker: str = "SPY") -> pd.DataFrame:
    return pd.DataFrame(
        [{"trade_date": s, "ticker": ticker, "weight": 0.25} for s in sessions]
    )


def _share_history(dates, shares, ticker: str = "SPY") -> pd.DataFrame:
    return pd.DataFrame(
        [{"session_date": d, "ticker": ticker, "shares": v}
         for d, v in zip(dates, shares)]
    )


def test_a_covered_session_is_the_measured_value() -> None:
    sessions = pd.bdate_range("2026-01-05", periods=5)
    frame = _share_history(sessions, [100.0, 120.0, 120.0, 90.0, 90.0])

    series = signal_history.shares_series(frame, "SPY", sessions)

    assert series.tolist() == [100.0, 120.0, 120.0, 90.0, 90.0]


def test_a_session_with_no_row_is_a_gap_not_a_carry_forward_and_not_zero() -> None:
    sessions = pd.bdate_range("2026-01-05", periods=5)
    frame = _share_history(
        [sessions[0], sessions[1], sessions[3]], [100.0, 120.0, 90.0]
    )

    series = signal_history.shares_series(frame, "SPY", sessions)

    assert series.loc[sessions[0]] == pytest.approx(100.0)
    assert np.isnan(series.loc[sessions[2]]), "no row is unknown, not the previous level"
    assert series.loc[sessions[3]] == pytest.approx(90.0)


def test_dates_before_the_series_starts_are_gaps_too() -> None:
    sessions = pd.bdate_range("2026-01-05", periods=5)
    frame = _share_history([sessions[3], sessions[4]], [50.0, 50.0])

    series = signal_history.shares_series(frame, "SPY", sessions)

    assert series.loc[sessions[:3]].isna().all(), (
        "the book had no position data before this point, and 0 would say it held nothing"
    )
    assert series.loc[sessions[3]] == pytest.approx(50.0)


def test_a_ticker_absent_from_the_history_omits_the_axis() -> None:
    sessions = pd.bdate_range("2026-01-05", periods=4)
    frame = _share_history(sessions, [10.0, 10.0, 10.0, 10.0], ticker="TLT")

    assert signal_history.shares_series(frame, "SPY", sessions) is None
    assert signal_history.shares_series(
        pd.DataFrame(columns=["session_date", "ticker", "shares"]), "SPY", sessions
    ) is None


def test_the_figure_draws_the_gap_and_names_it() -> None:
    closes = _closes()
    positions = _weights(closes.index)
    window = signal_history.history_window(closes, positions["trade_date"].min())
    frame = _share_history(
        [window[5], window[6], window[-1]], [40.0, 45.0, 45.0]
    )

    history = signal_history.build_ticker_history(
        closes, positions, "SPY", window, positions["trade_date"].min(),
        share_history=frame,
    )
    fig = signal_history.ticker_figure("SPY", history, window)

    shares = [t for t in fig.data if t.name == "shares held"][0]
    assert shares.connectgaps is False, "a gap must break the line, not be bridged"
    y = np.asarray(shares.y, dtype=float)
    assert np.isnan(y[:5]).all()
    assert not np.isnan(y[5])
    notes = " ".join(str(a.text) for a in fig.layout.annotations)
    assert "no position data" in notes


def test_a_chart_without_any_share_history_omits_the_axis() -> None:
    closes = _closes()
    positions = _weights(closes.index)
    window = signal_history.history_window(closes, positions["trade_date"].min())

    history = signal_history.build_ticker_history(
        closes, positions, "SPY", window, positions["trade_date"].min()
    )
    fig = signal_history.ticker_figure("SPY", history, window)

    assert history["has_shares"] is False
    assert not [t for t in fig.data if t.name == "shares held"]
    assert "shares held" not in (fig.layout.yaxis4.title.text or "")


def test_the_upper_line_says_held_because_that_is_what_it_is() -> None:
    closes = _closes()
    positions = _weights(closes.index)
    window = signal_history.history_window(closes, positions["trade_date"].min())
    history = signal_history.build_ticker_history(
        closes, positions, "SPY", window, positions["trade_date"].min()
    )
    fig = signal_history.ticker_figure("SPY", history, window)

    names = [t.name for t in fig.data]
    assert "held weight" in names and "position weight" not in names
    assert fig.layout.yaxis2.title.text == "held weight"
